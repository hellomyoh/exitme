"""예상 시가 관찰 (2026-09-09) — 08:30~08:59 예상체결가 / 09:00~ 확정 시가 기록, 표본 누적, 주문표 뷰의 갭 판정. Redis 는 스텁."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.preopen_watch import expected_open_view, poll_expected_open, read_expected

KST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def _clean_samples():
    """이 파일이 공유 DB 에 남기는 예상체결가 표본을 지운다 — 실코드(102110·069500)를 쓰므로 남기면 분석을 오염시킨다."""
    yield
    try:
        from sqlalchemy import delete

        from app.db import SessionLocal
        from app.models import PreopenSample

        with SessionLocal() as s:
            s.execute(delete(PreopenSample).where(PreopenSample.trade_date == date(2026, 9, 9)))
            s.commit()
    except Exception:      # noqa: BLE001 — DB 없는 환경에서도 이 파일은 돈다
        pass


class _R:
    def __init__(self):
        self.d: dict[str, str] = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v, ex=None):
        self.d[k] = v


class _Kis:
    def __init__(self, expected: int, open_px: int, prpr: int = 0):
        self.expected, self.open_px, self.prpr = expected, open_px, prpr
        self.calls: list[str] = []

    def fetch_expected(self, code):
        self.calls.append(f"exp:{code}")
        return {"expected": self.expected, "expected_qty": 10, "time": "085800", "raw": {}}

    def fetch_price(self, code):
        self.calls.append(f"px:{code}")
        return {"stck_oprc": str(self.open_px), "stck_prpr": str(self.prpr or self.open_px)}


def test_poll_records_expected_before_open_and_actual_open_after():
    r = _R()
    day = date(2026, 9, 9)
    kis = _Kis(expected=105_300, open_px=105_450)
    t1 = datetime(2026, 9, 9, 8, 40, tzinfo=KST)
    out = poll_expected_open(now=t1, client=kis, codes=("102110",), r=r)
    got = out["codes"]["102110"]                       # stored 는 DB 적재 여부(0029) — 여기 관심사가 아니다
    assert (got["price"], got["kind"]) == (105_300, "expected") and kis.calls == ["exp:102110"]
    poll_expected_open(now=datetime(2026, 9, 9, 8, 58, tzinfo=KST), client=_Kis(expected=105_100, open_px=0), codes=("102110",), r=r)
    doc = read_expected("102110", day, r)
    assert doc["price"] == 105_100 and doc["at"] == "08:58" and [s["price"] for s in doc["samples"]] == [105_300, 105_100]
    # 09:00 뒤에는 확정 시가(stck_oprc) — 예상체결가 TR 은 부르지 않는다
    kis2 = _Kis(expected=0, open_px=105_450)
    out2 = poll_expected_open(now=datetime(2026, 9, 9, 9, 1, tzinfo=KST), client=kis2, codes=("102110",), r=r)
    got2 = out2["codes"]["102110"]
    assert (got2["price"], got2["kind"]) == (105_450, "open") and kis2.calls == ["px:102110"]
    # 시가가 아직 0 이면 현재가로 대신하고 kind 를 구분한다
    out3 = poll_expected_open(now=datetime(2026, 9, 9, 9, 0, tzinfo=KST), client=_Kis(expected=0, open_px=0, prpr=105_400), codes=("069500",), r=r)
    assert out3["codes"]["069500"]["kind"] == "current" and out3["codes"]["069500"]["price"] == 105_400
    # 예상체결가 0(아직 없음) 은 기록하지 않는다
    r2 = _R()
    out4 = poll_expected_open(now=t1, client=_Kis(expected=0, open_px=0), codes=("102110",), r=r2)
    assert out4["codes"]["102110"]["price"] is None and read_expected("102110", day, r2) is None


def test_view_reports_gap_hit_against_exact_threshold():
    r = _R()
    day = date(2026, 9, 9)
    poll_expected_open(now=datetime(2026, 9, 9, 8, 50, tzinfo=KST), client=_Kis(expected=101_800, open_px=0), codes=("102110",), r=r)
    v = expected_open_view("102110", day, gap_cancel_exact=101_847.3, r=r)
    assert v["price"] == 101_800 and v["gap_hit"] is True and v["kind"] == "expected" and v["at"] == "08:50"
    v2 = expected_open_view("102110", day, gap_cancel_exact=101_000.0, r=r)
    assert v2["gap_hit"] is False
    assert expected_open_view("069500", day, gap_cancel_exact=101_000.0, r=r) is None      # 관찰값 없음
    assert expected_open_view("102110", day, gap_cancel_exact=None, r=r)["gap_hit"] is False  # 기준 없음(하락장)


def test_samples_are_persisted_so_accuracy_can_be_measured_later():
    """표본 DB 적재 (0029, 2026-09-15 지시) — Redis 는 TTL 12시간이라 다음 날이면 사라진다.

    KIS 가 과거 예상체결가를 주지 않으므로 지금 쌓지 않으면 "예상가가 실제 시가와 얼마나 맞나"를 영영 못 잰다.
    같은 분을 두 번 관찰해도 한 행이어야 한다(재실행·중복 발사).
    """
    import uuid

    from sqlalchemy import delete, select

    from app.db import SessionLocal
    from app.models import PreopenSample

    code = "PS" + uuid.uuid4().hex[:6].upper()
    day = date(2026, 9, 9)
    kis = _Kis(expected=105_300, open_px=105_450)
    try:
        r = _R()
        out = poll_expected_open(now=datetime(2026, 9, 9, 8, 40, tzinfo=KST), client=kis, codes=(code,), r=r)
        assert out["codes"][code]["stored"] is True
        # 같은 분 재관찰 — 행이 늘지 않는다
        poll_expected_open(now=datetime(2026, 9, 9, 8, 40, 45, tzinfo=KST), client=kis, codes=(code,), r=r)
        # 09:00 이후는 확정 시가로 같은 날에 한 행 더
        poll_expected_open(now=datetime(2026, 9, 9, 9, 2, tzinfo=KST), client=kis, codes=(code,), r=r)

        with SessionLocal() as s:
            rows = s.execute(select(PreopenSample.at, PreopenSample.price, PreopenSample.kind)
                             .where(PreopenSample.code == code, PreopenSample.trade_date == day)
                             .order_by(PreopenSample.at)).all()
        assert [(p, k) for _a, p, k in rows] == [(105_300, "expected"), (105_450, "open")], rows
        assert [a.astimezone(KST).strftime("%H:%M") for a, _p, _k in rows] == ["08:40", "09:02"]
    finally:
        with SessionLocal() as s:
            s.execute(delete(PreopenSample).where(PreopenSample.code == code))
            s.commit()
