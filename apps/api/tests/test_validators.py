"""OHLC 검증 규칙 테스트 — feature-market-data.md §12."""
from app.services.validators import validate_bar


def test_valid_bar_passes():
    assert validate_bar(100, 110, 95, 105, 1000) == []


def test_low_above_open_close_rejected():
    errors = validate_bar(100, 110, 101, 105, 1000)
    assert any(e.field == "low" for e in errors)


def test_high_below_open_close_rejected():
    errors = validate_bar(100, 104, 95, 105, 1000)
    assert any(e.field == "high" for e in errors)


def test_zero_price_rejected():
    # KRX가 거래정지일에 시가 0을 반환하는 케이스
    errors = validate_bar(0, 110, 95, 105, 1000)
    assert any(e.reason == "price must be > 0" for e in errors)


def test_negative_volume_rejected():
    errors = validate_bar(100, 110, 95, 105, -1)
    assert any(e.field == "volume" for e in errors)


def test_flat_bar_passes():
    # 동시호가만 체결된 날: O=H=L=C
    assert validate_bar(100, 100, 100, 100, 10) == []


# ── 확정봉 가드 (2026-09-07 사고 대응) ───────────────────────────────────────────

def test_bar_is_final_by_market_session(monkeypatch):
    """오늘 봉은 그 시장 정규장 마감 후에만 확정 — 미래 봉은 항상 거부."""
    from datetime import date as _date, datetime as _dt, timedelta
    from zoneinfo import ZoneInfo

    import app.services.ingest as ing

    def at(y, m, d, hh, mm, tz="Asia/Seoul"):
        monkeypatch.setattr(ing, "datetime", type("D", (), {
            "now": staticmethod(lambda z=None: _dt(y, m, d, hh, mm, tzinfo=ZoneInfo(tz)).astimezone(z) if z else _dt(y, m, d, hh, mm))}))

    today = _date(2026, 9, 7)
    at(2026, 9, 7, 8, 40)                                  # 장 시작 전 (사고 시각)
    assert ing.bar_is_final(today, "KOSPI") is False
    at(2026, 9, 7, 9, 26)                                  # 장중 (사고 시각)
    assert ing.bar_is_final(today, "KOSPI") is False
    at(2026, 9, 7, 15, 29)                                 # 마감 1분 전
    assert ing.bar_is_final(today, "KOSPI") is False
    at(2026, 9, 7, 15, 30)                                 # 마감 정각 → 확정
    assert ing.bar_is_final(today, "KOSPI") is True
    assert ing.bar_is_final(today - timedelta(days=1), "KOSPI") is True    # 과거 봉
    assert ing.bar_is_final(today + timedelta(days=1), "KOSPI") is False   # 미래 봉


def test_upsert_rejects_unfinished_today_bar(monkeypatch):
    """장중 수신한 '오늘 봉'은 저장하지 않는다 — ON CONFLICT DO NOTHING 이라 한 번 들어가면
    마감 후 진짜 종가가 영구히 막히기 때문(2026-09-07 사고: 거래량 0 봉이 주문표를 만들었다)."""
    import uuid
    from datetime import date as _date

    from sqlalchemy import select

    import app.services.ingest as ing
    from app.db import SessionLocal
    from app.models import OhlcvDaily

    day = _date(2026, 9, 7)
    code = "T" + uuid.uuid4().hex[:5].upper()   # 실행마다 새 종목 — 기존 봉과 충돌 방지
    with SessionLocal() as s:
        inst = ing.get_or_create_instrument(s, code, "가드검증", "KOSPI")
        s.commit()
        iid = inst.id

        monkeypatch.setattr(ing, "market_session_state", lambda market, type_=None: (day, False))  # 장중
        r1 = ing.upsert_daily_bars(s, iid, [{"trade_date": day, "open": 100, "high": 100,
                                             "low": 100, "close": 105_880, "volume": 0}], source="kis")
        s.commit()
        assert r1.inserted == 0 and r1.rejected == 1
        assert s.scalar(select(OhlcvDaily).where(OhlcvDaily.instrument_id == iid,
                                                 OhlcvDaily.trade_date == day)) is None

        monkeypatch.setattr(ing, "market_session_state", lambda market, type_=None: (day, True))   # 마감 후
        r2 = ing.upsert_daily_bars(s, iid, [{"trade_date": day, "open": 100_000, "high": 106_000,
                                             "low": 99_000, "close": 103_000, "volume": 12_000_000}], source="kis")
        s.commit()
        assert r2.inserted == 1 and r2.rejected == 0
        row = s.scalar(select(OhlcvDaily).where(OhlcvDaily.instrument_id == iid,
                                                OhlcvDaily.trade_date == day))
        assert row.close_raw == 103_000 and row.volume == 12_000_000   # 진짜 종가가 자리를 차지
