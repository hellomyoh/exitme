"""KRX 애프터마켓(2026-09-14, 16:00~20:00) 대응 — 국내 주식 확정봉 20:00, 수집 범위 분리, 저녁 스냅샷·조건부 일일 현황.

사용자 결정(2026-09-10): 일일 현황은 정규장 마감 뒤 1회(16:40), 애프터마켓이 끝난 뒤에는 **추가 거래가 있을 때만** 한 번 더.
가격만 움직인 경우는 보내지 않는다.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.main import app

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

db = pytest.mark.skipif(not DB_UP, reason="database not reachable")
KST = timezone(timedelta(hours=9))
TOKEN = "123456789:AAHxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"am{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_kr_stock_bar_is_final_only_after_the_after_market():
    """국내 주식의 오늘 봉은 애프터마켓이 끝난 20:00 뒤에만 확정 — ETF 와 미국은 종전 그대로."""
    from app.services import ingest as ing

    today = date(2026, 9, 14)

    def at(hhmm, tz=KST):
        return datetime.combine(today, time(*hhmm), tzinfo=tz)

    with patch.object(ing, "datetime") as dt:
        for hhmm, etf_final, stock_final in (((15, 29), False, False), ((15, 30), True, False),
                                             ((16, 5), True, False), ((19, 59), True, False),
                                             ((20, 0), True, True), ((20, 10), True, True)):
            dt.now.return_value = at(hhmm)
            assert ing.bar_is_final(today, "KOSPI", "ETF") is etf_final, hhmm
            assert ing.bar_is_final(today, "KOSPI", "STOCK") is stock_final, hhmm
            # 종류를 주지 않으면 정규장 기준 (주문표 판정 등 기존 호출부)
            assert ing.bar_is_final(today, "KOSPI") is etf_final, hhmm
            # 과거 봉은 언제나 확정, 미래 봉은 언제나 거부
            assert ing.bar_is_final(today - timedelta(days=1), "KOSPI", "STOCK") is True
            assert ing.bar_is_final(today + timedelta(days=1), "KOSPI", "ETF") is False


@db
def test_upsert_rejects_todays_stock_bar_until_20_00():
    """16:05 에 받은 국내 주식 봉은 저장하지 않고(저녁 거래가 반영되지 못한 채 굳는 것 방지), 20:10 에 받은 것이 자리를 차지한다."""
    from app.services import ingest as ing
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars

    today = date.today()
    code = "A" + uuid.uuid4().hex[:5].upper()
    bar = {"trade_date": today, "open": 70_000, "high": 71_000, "low": 69_500, "close": 70_500, "volume": 100}
    with SessionLocal() as s:
        inst = get_or_create_instrument(s, code, f"주식{code}", "KOSPI", type_="STOCK")
        with patch.object(ing, "market_session_state", return_value=(today, False)):   # 아직 애프터마켓 중
            r1 = upsert_daily_bars(s, inst.id, [bar], source="kis")
        assert r1.inserted == 0 and r1.rejected == 1
        with patch.object(ing, "market_session_state", return_value=(today, True)):    # 20:00 이후
            r2 = upsert_daily_bars(s, inst.id, [{**bar, "high": 72_000, "close": 72_000}], source="kis")
        assert r2.inserted == 1
        s.commit()
        from sqlalchemy import select

        from app.models import OhlcvDaily

        row = s.scalar(select(OhlcvDaily).where(OhlcvDaily.instrument_id == inst.id, OhlcvDaily.trade_date == today))
        assert int(row.close_raw) == 72_000   # 저녁 값이 그날 종가


@db
def test_ingest_targets_split_regular_and_stocks():
    """수집 범위 — regular 는 국내 ETF·미국, stocks 는 국내 주식만. all 은 전부."""
    from app.services.ingest import get_or_create_instrument
    from app.worker import ingest_targets

    sfx = uuid.uuid4().hex[:4].upper()
    with SessionLocal() as s:
        etf = get_or_create_instrument(s, f"E{sfx}0", f"ETF{sfx}", "KOSPI", type_="ETF")
        stk = get_or_create_instrument(s, f"S{sfx}0", f"주식{sfx}", "KOSPI", type_="STOCK")
        us = get_or_create_instrument(s, f"U{sfx}", f"US{sfx}", "NASDAQ", type_="ETF")
        s.commit()
        codes = lambda scope: {i.code for i in ingest_targets(s, scope)}   # noqa: E731
        reg, st, all_ = codes("regular"), codes("stocks"), codes("all")
    # 공유 DB 라 다른 종목도 들어 있다 — 이 세 종목이 어느 무리에 속하는지만 본다
    assert etf.code in reg and us.code in reg and stk.code not in reg
    assert stk.code in st and etf.code not in st and us.code not in st
    assert {etf.code, stk.code, us.code} <= all_ and not (reg & st)


@db
def test_evening_snapshot_notifies_only_when_there_were_evening_fills(monkeypatch):
    """20:20 — 스냅샷은 늘 다시 계산(그날 최종값), 일일 현황은 저녁에 새 체결이 있은 사용자에게만 한 번 더."""
    import app.notify as nt
    from sqlalchemy import select

    from app.dashboard import compute_user_snapshot, kst_today
    from app.models import AssetSnapshot, TradePortfolio
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars
    from app.worker import EVENING_SINCE, users_with_evening_fills

    today = kst_today()
    code = "V" + uuid.uuid4().hex[:5].upper()
    with SessionLocal() as s:
        inst = get_or_create_instrument(s, code, f"저녁{code}", "KOSPI", type_="ETF")
        with patch("app.services.ingest.market_session_state", return_value=(today, True)):
            upsert_daily_bars(s, inst.id, [{"trade_date": today, "open": 10_000, "high": 10_000,
                                            "low": 10_000, "close": 10_000, "volume": 1}], source="kis")
        s.commit()

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "저녁포트", "market": "KR", "code_200": "102110"}, headers=h).json()["id"]
    with SessionLocal() as s:
        uid = s.get(TradePortfolio, pid).user_id
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000,
                               "executed_at": (today - timedelta(days=1)).isoformat() + "T10:00:00+09:00"}, headers=h)
    c.put("/settings/notify", json={"bot_token": TOKEN, "chat_id": "7", "enabled": True}, headers=h)
    sent: list[str] = []
    monkeypatch.setattr(nt, "_http_send", lambda token, chat_id, text: sent.append(text) or {"ok": True})

    # 16:40 정규장 마감 발송
    with SessionLocal() as s:
        compute_user_snapshot(s, uid, today)
        s.commit()
        assert nt.send_daily_status(s, uid, today) is True
        s.commit()
    assert len(sent) == 1 and sent[0].startswith("ℹ️ [ExitMe] 📊 일일 현황")
    # 같은 단계 재실행은 보내지 않는다 (스케줄러 따라잡기)
    with SessionLocal() as s:
        assert nt.send_daily_status(s, uid, today) is False

    # 저녁 체결이 없으면 대상 아님 (입금은 체결이 아니라 제외). 운영의 기준 시각은 16:45,
    # 테스트는 지금을 기준으로 삼는다 — 실행 시각에 상관없이 '이 시점 이후의 체결'만 세는지 본다
    assert EVENING_SINCE == time(16, 45)
    since = datetime.now(KST)
    with SessionLocal() as s:
        assert uid not in users_with_evening_fills(s, today, since)

    # 애프터마켓 체결 등록 → 대상이 되고, 20:20 발송은 최종값·다른 제목
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": code, "qty": 10, "price": 9_000,
                               "executed_at": today.isoformat() + "T18:30:00+09:00"}, headers=h)
    with SessionLocal() as s:
        assert uid in users_with_evening_fills(s, today, since)
        compute_user_snapshot(s, uid, today)
        s.commit()
        assert nt.send_daily_status(s, uid, today, phase="after") is True
        s.commit()
        snap = s.scalar(select(AssetSnapshot).where(AssetSnapshot.user_id == uid, AssetSnapshot.snap_date == today))
        assert int(snap.stock) == 10 * 10_000 and int(snap.cash) == 1_000_000 - 10 * 9_000   # 저녁 체결이 반영된 최종값
    assert len(sent) == 2 and sent[1].startswith("ℹ️ [ExitMe] 🌙 애프터마켓 마감 반영")
    with SessionLocal() as s:                      # 저녁 단계도 하루 한 번
        assert nt.send_daily_status(s, uid, today, phase="after") is False
