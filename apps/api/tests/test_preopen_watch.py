"""예상 시가 관찰 (2026-09-09) — 08:30~08:59 예상체결가 / 09:00~ 확정 시가 기록, 표본 누적, 주문표 뷰의 갭 판정. Redis 는 스텁."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.preopen_watch import expected_open_view, poll_expected_open, read_expected

KST = timezone(timedelta(hours=9))


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
    assert out["codes"]["102110"] == {"price": 105_300, "kind": "expected"} and kis.calls == ["exp:102110"]
    poll_expected_open(now=datetime(2026, 9, 9, 8, 58, tzinfo=KST), client=_Kis(expected=105_100, open_px=0), codes=("102110",), r=r)
    doc = read_expected("102110", day, r)
    assert doc["price"] == 105_100 and doc["at"] == "08:58" and [s["price"] for s in doc["samples"]] == [105_300, 105_100]
    # 09:00 뒤에는 확정 시가(stck_oprc) — 예상체결가 TR 은 부르지 않는다
    kis2 = _Kis(expected=0, open_px=105_450)
    out2 = poll_expected_open(now=datetime(2026, 9, 9, 9, 1, tzinfo=KST), client=kis2, codes=("102110",), r=r)
    assert out2["codes"]["102110"] == {"price": 105_450, "kind": "open"} and kis2.calls == ["px:102110"]
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
