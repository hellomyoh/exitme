"""1분봉 수집 테스트 — 페이지네이션(mock)·멱등 적재·증분 시작점·API timeframe (feature-market-data §12)."""
from datetime import date, datetime, timedelta, timezone

import pytest
import responses

from app.db import engine
from app.services.kis_auth import BASE_URLS, KisAuth
from app.services.kis_client import MINUTE_PATH, KisClient, MinuteBar

PROD = BASE_URLS["prod"]
KST = timezone(timedelta(hours=9))

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False


def token_response():
    from datetime import datetime as dt

    return {"access_token": "t", "access_token_token_expired": (dt.now() + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")}


def minute_row(day: str, hhmmss: str, price: int) -> dict:
    return {"stck_bsop_date": day, "stck_cntg_hour": hhmmss, "stck_prpr": str(price),
            "stck_oprc": str(price - 5), "stck_hgpr": str(price + 10),
            "stck_lwpr": str(price - 10), "cntg_vol": "100"}


@responses.activate
def test_fetch_minutes_paginates_with_time_cursor():
    responses.post(PROD + "/oauth2/tokenP", json=token_response())
    day = "20260827"
    # 1페이지: 15:30~15:29 / 2페이지: 15:28~09:00 도달 → 종료
    page1 = {"rt_cd": "0", "output2": [minute_row(day, "153000", 109135), minute_row(day, "152900", 109000)]}
    page2 = {"rt_cd": "0", "output2": [minute_row(day, "152800", 108900), minute_row(day, "090000", 108000)]}
    responses.get(PROD + MINUTE_PATH, json=page1)
    responses.get(PROD + MINUTE_PATH, json=page2)
    client = KisClient(KisAuth("key", "secret", "prod"))
    bars = client.fetch_minutes_day("069500", date(2026, 8, 27))
    assert len(bars) == 4
    assert bars[0].ts.hour == 9 and bars[-1].ts.hour == 15  # 오름차순
    assert bars[-1].close == 109135
    calls = [c for c in responses.calls if MINUTE_PATH in c.request.url]
    assert len(calls) == 2
    assert "FID_INPUT_HOUR_1=153000" in calls[0].request.url
    assert "FID_INPUT_HOUR_1=152800" in calls[1].request.url  # 152900 − 1분


@responses.activate
def test_fetch_minutes_empty_beyond_retention():
    responses.post(PROD + "/oauth2/tokenP", json=token_response())
    responses.get(PROD + MINUTE_PATH, json={"rt_cd": "0", "output2": []})
    client = KisClient(KisAuth("key", "secret", "prod"))
    assert client.fetch_minutes_day("069500", date(2020, 1, 2)) == []


@pytest.mark.integration
@pytest.mark.skipif(not DB_UP, reason="database not reachable")
def test_minute_upsert_idempotent_and_api_timeframe():
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.services.ingest import get_or_create_instrument, upsert_minute_bars

    with SessionLocal() as s:
        inst = get_or_create_instrument(s, "TESTMIN", "분봉 테스트", "KOSPI")
        # 자체 정리 — 이전 실행 잔여 행 제거 (멱등 검증의 전제)
        from app.models import OhlcvIntraday
        s.query(OhlcvIntraday).filter(OhlcvIntraday.instrument_id == inst.id).delete()
        s.commit()
        bars = [
            MinuteBar(ts=datetime(2026, 8, 27, 9, i, tzinfo=KST),
                      open=100, high=110, low=95, close=105, volume=10)
            for i in range(3)
        ] + [MinuteBar(ts=datetime(2026, 8, 27, 9, 3, tzinfo=KST),
                       open=0, high=110, low=95, close=105, volume=10)]  # 검증 거부
        r1 = upsert_minute_bars(s, inst.id, bars, source="kis")
        assert r1.inserted == 3 and r1.rejected == 1
        r2 = upsert_minute_bars(s, inst.id, bars, source="kis")
        assert r2.inserted == 0 and r2.skipped_conflict == 3  # 멱등 — 재수집해도 중복 없음
        s.commit()

    client = TestClient(app)
    body = client.get("/ohlcv", params={"code": "TESTMIN", "from": "2026-08-27", "to": "2026-08-27",
                                        "timeframe": "1m"}).json()
    assert body["timeframe"] == "1m" and len(body["items"]) == 3
    assert body["items"][0]["ts"] < body["items"][-1]["ts"]


@pytest.mark.integration
@pytest.mark.skipif(not DB_UP, reason="database not reachable")
def test_incremental_start_uses_max_ts():
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Instrument, OhlcvIntraday
    from sqlalchemy import func

    with SessionLocal() as s:
        inst = s.scalar(select(Instrument).where(Instrument.code == "TESTMIN"))
        last = s.scalar(select(func.max(OhlcvIntraday.ts)).where(OhlcvIntraday.instrument_id == inst.id))
        assert last is not None
        # scripts.seed_minutes 의 증분 시작점 로직과 동일: 최신 ts 의 날짜부터 재수집(멱등)
        assert last.date() == date(2026, 8, 27)
