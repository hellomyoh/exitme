"""KIS 유량 (2026-09-09 사고 EGW00201) — 앱키 단위 공용 초당 제한, 주문 POST 의 유량 초과 한정 재시도, 다른 오류는 재시도 없음. 네트워크·Redis 없음."""
from __future__ import annotations

import pytest

from app.services.kis_client import KisClient, KisError, KisTradingClient


class _Auth:
    def __init__(self, env="prod", app_key="PSkey"):
        self.env, self.app_key, self.base_url = env, app_key, "http://kis.test"

    def headers(self, tr_id, session=None):
        return {}


class _R:
    """Redis 흉내 — incr/expire 만."""

    def __init__(self):
        self.d: dict[str, int] = {}

    def incr(self, k):
        self.d[k] = self.d.get(k, 0) + 1
        return self.d[k]

    def expire(self, k, ttl):
        return True


def test_shared_throttle_limits_per_second_per_app_key_and_env():
    r = _R()
    c = KisClient(_Auth("prod", "A"))
    c._shared_r = r
    clock = [1000.2]
    slept: list[float] = []

    def now():
        return clock[0]

    def sleep(sec):
        slept.append(sec)
        clock[0] += sec
    # 실전 10건/초 — 11번째는 다음 초까지 기다린다
    waits = [c._shared_throttle(now_fn=now, sleep_fn=sleep) for _ in range(10)]
    assert waits == [0] * 10 and slept == []
    assert c._shared_throttle(now_fn=now, sleep_fn=sleep) == 1 and len(slept) == 1 and int(clock[0]) == 1001
    # 다른 앱키·다른 환경은 별도 카운터 (모의는 1건/초)
    v = KisClient(_Auth("vps", "B"))
    v._shared_r = r
    assert v._shared_throttle(now_fn=now, sleep_fn=sleep) == 0
    assert v._shared_throttle(now_fn=now, sleep_fn=sleep) == 1
    # Redis 없음 → 제한 없이 통과
    n = KisClient(_Auth("prod", "C"))
    n._shared_r = False
    assert n._shared_throttle(now_fn=now, sleep_fn=sleep) == 0


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class _Session:
    def __init__(self, responses):
        self.responses, self.posts = list(responses), 0

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts += 1
        return self.responses.pop(0)


def _trading(responses):
    c = KisTradingClient.__new__(KisTradingClient)
    KisClient.__init__(c, _Auth("prod", "A"), session=_Session(responses))
    c._shared_r = False
    c.cano, c.acnt_prdt_cd = "68800037", "01"
    return c


def test_order_post_retries_only_on_rate_limit():
    ok = _Resp(200, {"rt_cd": "0", "output": {"ODNO": "0002694300"}, "msg1": "정상"})
    limited = _Resp(500, {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."})
    slept: list[float] = []
    c = _trading([limited, limited, ok])
    out = c._post("/x", "TTTC0012U", {}, sleep_fn=slept.append)
    assert out["output"]["ODNO"] == "0002694300" and c.session.posts == 3 and slept == [1.0, 2.0]
    # 4번 연속 유량 초과 → 3회 재시도 뒤 실패
    c2 = _trading([limited, limited, limited, limited])
    with pytest.raises(KisError, match="EGW00201"):
        c2._post("/x", "TTTC0012U", {}, sleep_fn=lambda _s: None)
    assert c2.session.posts == 4
    # 다른 오류(주문가능금액 초과 등)는 재시도하지 않는다 — 중복 접수 방지 원칙
    other = _Resp(200, {"rt_cd": "1", "msg_cd": "40310000", "msg1": "주문가능금액을 초과하였습니다"})
    c3 = _trading([other, ok])
    with pytest.raises(KisError, match="40310000"):
        c3._post("/x", "TTTC0012U", {}, sleep_fn=lambda _s: None)
    assert c3.session.posts == 1


# ── 2026-09-11 사고: EGW00215(원장 초당 한도) ──────────────────────────────────────

class _GetResp(_Resp):
    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected raise_for_status {self.status_code}")


class _GetSession:
    def __init__(self, responses):
        self.responses, self.gets = list(responses), 0

    def get(self, url, headers=None, params=None, timeout=None):
        self.gets += 1
        return self.responses.pop(0)


def _get_client(responses, cano="68800037"):
    c = KisTradingClient.__new__(KisTradingClient)
    KisClient.__init__(c, _Auth("prod", "A"), session=_GetSession(responses))
    c._shared_r = False
    c.cano, c.acnt_prdt_cd = cano, "01"
    return c


def test_balance_get_retries_on_ledger_rate_limit(monkeypatch):
    """잔고 조회가 EGW00215 로 거절되면 백오프 뒤 다시 조회한다 — 한 번의 유량 초과로 그날 발주가 통째로 생략되지 않게."""
    import app.services.kis_client as kc

    monkeypatch.setattr(kc.time, "sleep", lambda _s: None)
    limited = _GetResp(500, {"rt_cd": "1", "msg_cd": "EGW00215",
                             "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다."})
    ok = _GetResp(200, {"rt_cd": "0", "output1": [], "output2": [{}]})
    c = _get_client([limited, limited, ok])
    assert c._get("/balance", "TTTC8434R", {})["rt_cd"] == "0"
    assert c.session.gets == 3


def test_rate_limit_in_200_body_is_retried_and_other_codes_are_not(monkeypatch):
    """200 + rt_cd!=0 형태로 와도 유량 코드면 재시도, 그 밖의 코드는 즉시 오류."""
    import app.services.kis_client as kc

    monkeypatch.setattr(kc.time, "sleep", lambda _s: None)
    limited = _GetResp(200, {"rt_cd": "1", "msg_cd": "EGW00215", "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다."})
    ok = _GetResp(200, {"rt_cd": "0", "output": {}})
    c = _get_client([limited, ok])
    assert c._get("/x", "TR", {})["rt_cd"] == "0" and c.session.gets == 2

    other = _GetResp(500, {"rt_cd": "1", "msg_cd": "EGW00304", "msg1": "앱시크릿이 올바르지 않습니다"})
    c2 = _get_client([other, ok])
    with pytest.raises(KisError, match="EGW00304"):
        c2._get("/x", "TR", {})
    assert c2.session.gets == 1


def test_order_post_retries_on_ledger_rate_limit():
    ok = _Resp(200, {"rt_cd": "0", "output": {"ODNO": "1"}, "msg1": "정상"})
    limited = _Resp(500, {"rt_cd": "1", "msg_cd": "EGW00215", "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다."})
    slept: list[float] = []
    c = _trading([limited, ok])
    assert c._post("/x", "TTTC0012U", {}, sleep_fn=slept.append)["output"]["ODNO"] == "1"
    assert c.session.posts == 2 and slept == [1.0]


def test_ledger_throttle_is_counted_per_account_not_only_per_app_key():
    """원장 버킷은 계좌별 — 같은 앱키라도 계좌가 다르면 따로 센다. 시세 전용(계좌 없음) 클라이언트는 대상 외."""
    r = _R()
    clock = [2000.1]
    slept: list[float] = []

    def now():
        return clock[0]

    def sleep(sec):
        slept.append(sec)
        clock[0] += sec

    a = _get_client([], cano="11110000")
    a._shared_r = r
    assert [a._ledger_throttle(now_fn=now, sleep_fn=sleep) for _ in range(2)] == [0, 0]   # 실전 2건/초
    assert a._ledger_throttle(now_fn=now, sleep_fn=sleep) == 1 and int(clock[0]) == 2001
    b = _get_client([], cano="22220000")
    b._shared_r = r
    assert b._ledger_throttle(now_fn=now, sleep_fn=sleep) == 0                             # 다른 계좌는 별도 카운터
    q = KisClient(_Auth("prod", "A"))
    q._shared_r = r
    assert q._ledger_throttle(now_fn=now, sleep_fn=sleep) == 0                             # 계좌 없음 → 제한 없음
