"""거래일 캘린더 갱신 (2026-09-08, ADR-009 선결) — KIS 국내휴장일조회 파싱·페이지 추적, upsert 멱등성, 실행일 계산 연동. DB 필요."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.db import SessionLocal, engine

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]


class _Auth:
    env = "prod"
    base_url = "http://kis.test"

    def headers(self, tr_id, session=None):
        return {}


def test_fetch_holidays_parses_and_follows_pages(monkeypatch):
    """CTCA0903R: 하루 한 행(주말 포함), opnd_yn 이 개장 여부, ctx_area_nk 가 다음 페이지의 BASS_DT. end 를 넘으면 멈추고 잘라 낸다."""
    from app.services.kis_client import KisClient

    c = KisClient(_Auth())
    calls: list[dict] = []

    def fake_get(path, tr_id, params):
        calls.append(params)
        assert path.endswith("/quotations/chk-holiday") and tr_id == "CTCA0903R"
        base = date.fromisoformat(f"{params['BASS_DT'][:4]}-{params['BASS_DT'][4:6]}-{params['BASS_DT'][6:]}")
        rows = []
        for i in range(3):   # 페이지당 3일로 흉내
            d = base + timedelta(days=i)
            closed = d.weekday() >= 5 or d == date(2031, 1, 2)
            rows.append({"bass_dt": d.strftime("%Y%m%d"), "wday_dvsn_cd": "0%d" % ((d.weekday() + 1) % 7 + 1),
                         "bzdy_yn": "N" if closed else "Y", "tr_day_yn": "N" if closed else "Y", "opnd_yn": "N" if closed else "Y", "sttl_day_yn": "Y"})
        nxt = base + timedelta(days=3)
        return {"rt_cd": "0", "output": rows, "ctx_area_nk": nxt.strftime("%Y%m%d") + " " * 12, "ctx_area_fk": params["BASS_DT"]}

    monkeypatch.setattr(c, "_get", fake_get)
    out = c.fetch_holidays(date(2031, 1, 1), date(2031, 1, 7))   # 수(01) ~ 화(07)
    assert [d.isoformat() for d, _ in out] == [f"2031-01-0{i}" for i in range(1, 8)]
    assert {d.isoformat(): o for d, o in out} == {"2031-01-01": True, "2031-01-02": False, "2031-01-03": True,
                                                   "2031-01-04": False, "2031-01-05": False, "2031-01-06": True, "2031-01-07": True}
    assert len(calls) == 3 and calls[1]["BASS_DT"] == "20310104" and calls[2]["BASS_DT"] == "20310107"


def test_refresh_calendar_upserts_idempotently_and_feeds_exec_day():
    """추가 → 재실행 무변경 → 값이 바뀐 날만 갱신·기록. 갱신된 휴장은 _next_exec_day 가 바로 건너뛴다."""
    from app.models import TradingCalendar
    from app.services.calendar import refresh_trading_calendar
    from app.signals import _next_exec_day

    start, end = date(2031, 3, 3), date(2031, 3, 12)   # 월 ~ 다음 수

    class Fake:
        def __init__(self, closed: set[date]):
            self.closed = closed

        def fetch_holidays(self, s, e):
            return [(s + timedelta(days=i), (s + timedelta(days=i)).weekday() < 5 and (s + timedelta(days=i)) not in self.closed)
                    for i in range((e - s).days + 1)]

    with SessionLocal() as s:
        for d in [start + timedelta(days=i) for i in range((end - start).days + 1)]:
            row = s.get(TradingCalendar, d)
            if row is not None:
                s.delete(row)
        s.commit()
        try:
            r1 = refresh_trading_calendar(s, Fake({date(2031, 3, 4)}), start, end)
            assert r1["fetched"] == 10 and r1["added"] == 10 and r1["updated"] == 0 and r1["closed_weekdays"] == ["2031-03-04"]
            r2 = refresh_trading_calendar(s, Fake({date(2031, 3, 4)}), start, end)
            assert r2["added"] == 0 and r2["updated"] == 0 and r2["changed"] == []
            assert _next_exec_day(date(2031, 3, 3), s) == date(2031, 3, 5)       # 3/4 휴장 → 3/5
            r3 = refresh_trading_calendar(s, Fake({date(2031, 3, 4), date(2031, 3, 5)}), start, end)   # 임시 휴장 추가
            assert r3["updated"] == 1 and r3["changed"] == [{"date": "2031-03-05", "was": True, "now": False}]
            assert _next_exec_day(date(2031, 3, 3), s) == date(2031, 3, 6)
        finally:
            for d in [start + timedelta(days=i) for i in range((end - start).days + 1)]:
                row = s.get(TradingCalendar, d)
                if row is not None:
                    s.delete(row)
            s.commit()
