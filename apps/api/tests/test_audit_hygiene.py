"""감사 위생 항목 (2026-09-12, kodex-linkage-audit A5·A6·A10·A11·A12) — 데이터 무결성·입력 검증·API 계약."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.main import app

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]


def _seed_pair(a: str, b: str, n: int = 300):
    from tests.test_backtest_api import seed_synthetic

    with SessionLocal() as s:
        seed_synthetic(s, a, f"합성 {a}", n=n, seed=3)
        seed_synthetic(s, b, f"합성 {b}", n=n, seed=8, start=20000.0)
        s.commit()


def _inst_id(s, code):
    from app.models import Instrument
    return s.scalar(select(Instrument.id).where(Instrument.code == code))


# ── A5 결측 명시 오류 ────────────────────────────────────────────────────────────

def test_interior_gap_in_one_instrument_is_an_explicit_error_but_tail_lag_is_tolerated():
    """공통 구간 안의 한쪽 결측 → 409 + 날짜. 구간 끝의 한쪽만 있는 최신 봉(적재 시차)은 종전처럼 교집합."""
    from app.backtests import load_aligned_bars
    from app.models import OhlcvDaily

    a, b = "TSA" + uuid.uuid4().hex[:3].upper(), "TSB" + uuid.uuid4().hex[:3].upper()
    _seed_pair(a, b)
    with SessionLocal() as s:
        bars_a, bars_b, fp0 = load_aligned_bars(s, date(2024, 1, 1), date(2026, 12, 31), (a, b))
        assert [x["date"] for x in bars_a] == [x["date"] for x in bars_b]
        last = date.fromisoformat(bars_a[-1]["date"])
        # (1) 꼬리 시차 — a 에만 다음 거래일 봉 추가 → 오류 없이 마지막 공통 봉이 기준
        ia = _inst_id(s, a)
        nxt = last + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        s.add(OhlcvDaily(instrument_id=ia, trade_date=nxt, open_raw=1, high_raw=1, low_raw=1, close_raw=1, volume=1, adj_factor=1, source="t"))
        s.commit()
        bars_a2, _, _ = load_aligned_bars(s, date(2024, 1, 1), date(2026, 12, 31), (a, b))
        assert bars_a2[-1]["date"] == last.isoformat()
        # (2) 안쪽 결측 — b 의 중간 하루 삭제 → 409, 그 날짜가 메시지에
        ib = _inst_id(s, b)
        mid = date.fromisoformat(bars_b[len(bars_b) // 2]["date"])
        s.execute(text("DELETE FROM ohlcv_daily WHERE instrument_id = :i AND trade_date = :d"), {"i": ib, "d": mid})
        s.commit()
        with pytest.raises(HTTPException) as ei:
            load_aligned_bars(s, date(2024, 1, 1), date(2026, 12, 31), (a, b))
        assert ei.value.status_code == 409 and "시세 결측" in ei.value.detail and mid.isoformat() in ei.value.detail


def test_engine_rejects_equal_length_but_misaligned_dates():
    from app.strategy.backtest import run_backtest
    from app.strategy.params import Params

    def bars(start, n):
        d, out = start, []
        while len(out) < n:
            if d.weekday() < 5:
                out.append({"date": d.isoformat(), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1})
            d += timedelta(days=1)
        return out

    a = bars(date(2024, 1, 2), 320)
    b = bars(date(2024, 1, 3), 320)     # 같은 길이, 하루 어긋남
    with pytest.raises(ValueError, match="aligned by date"):
        run_backtest(a, b, 1e8, Params())


# ── A6 fingerprint ──────────────────────────────────────────────────────────────

def test_fingerprint_tracks_content_and_ignores_out_of_range_ingestion():
    from app.backtests import load_aligned_bars

    a, b = "TFA" + uuid.uuid4().hex[:3].upper(), "TFB" + uuid.uuid4().hex[:3].upper()
    _seed_pair(a, b)
    d0, d1 = date(2024, 1, 1), date(2025, 1, 31)
    with SessionLocal() as s:
        _, _, fp0 = load_aligned_bars(s, d0, d1, (a, b))
        ia = _inst_id(s, a)
        # 같은 행 수·같은 적재 시각인데 종가만 정정 → fingerprint 가 달라져야 한다
        s.execute(text("UPDATE ohlcv_daily SET close_raw = close_raw + 1 WHERE instrument_id = :i AND trade_date = :d"),
                  {"i": ia, "d": date(2024, 6, 3)})
        s.commit()
        _, _, fp1 = load_aligned_bars(s, d0, d1, (a, b))
        assert fp1 != fp0
        # 요청 기간 밖의 새 봉은 결과와 무관 → fingerprint 불변 (종전엔 전체 max(ingested_at) 이 바뀌어 stale 처리됐다)
        from app.models import OhlcvDaily
        s.add(OhlcvDaily(instrument_id=ia, trade_date=date(2026, 6, 1), open_raw=1, high_raw=1, low_raw=1, close_raw=1,
                         volume=1, adj_factor=1, source="t"))
        s.commit()
        _, _, fp2 = load_aligned_bars(s, d0, d1, (a, b))
        assert fp2 == fp1


# ── A10 비용 범위 ───────────────────────────────────────────────────────────────

def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"hy{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_backtest_rejects_negative_or_absurd_costs():
    c, h = _client()
    base = {"capital": 50_000_000, "date_from": "2024-01-02", "date_to": "2025-01-02"}
    for costs in ({"commission": -0.001}, {"slippage_market": -0.1}, {"lev_tax": 1.5}, {"fee_200": 0.5}):
        r = c.post("/backtests", json={**base, "costs": costs}, headers=h)
        assert r.status_code == 422, (costs, r.text[:200])


# ── A11 as-of 재생 ─────────────────────────────────────────────────────────────

def test_signal_batch_past_target_replays_as_of_that_date():
    from tests.test_backtest_api import seed_synthetic
    from app.signals import run_signal_batch

    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
        seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)
        s.commit()
        latest = run_signal_batch(s)
        s.commit()
        if latest.status != "OK":
            pytest.skip("synthetic data insufficient for OK signal")
        target = latest.trade_date - timedelta(days=30)
        snap = run_signal_batch(s, target=target)
        s.commit()
        assert snap.trade_date <= target                      # target 뒤의 봉을 쓰지 않았다
        assert snap.trade_date > target - timedelta(days=7)    # target 직전 거래일
        assert snap.detail.get("as_of") == target.isoformat()
        # 최신 날짜의 현재 스냅샷은 그대로 — 과거 재생이 밀어내지 않는다
        from app.models import SignalSnapshot
        cur = s.scalars(select(SignalSnapshot).where(SignalSnapshot.is_current).order_by(SignalSnapshot.trade_date.desc())).first()
        assert cur.trade_date == latest.trade_date


# ── A12 포트 응답 일관성 ─────────────────────────────────────────────────────────

def test_portfolio_signal_weights_come_from_the_same_plan():
    from tests.test_backtest_api import seed_synthetic
    from app.signals import run_signal_batch

    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
        seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)
        s.commit()
        snap = run_signal_batch(s)
        s.commit()
        if snap.status != "OK":
            pytest.skip("synthetic data insufficient for OK signal")
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "가중치 일관"}, headers=h).json()["id"]
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 100_000_000,
                               "executed_at": "2025-07-01T10:00:00+09:00"}, headers=h)
    body = c.get(f"/signals/daily?portfolio_id={pid}", headers=h).json()
    assert body["basis"] == "portfolio"
    assert body["w_200"] + 2 * body["w_lev"] == pytest.approx(body["e_target"], abs=1e-9)   # 같은 계획의 값
    assert body["trade_date"] == body["signal_date"]
