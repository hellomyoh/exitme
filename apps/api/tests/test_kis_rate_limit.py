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
