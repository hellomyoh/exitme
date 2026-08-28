"""일일 시그널 테스트 — 백테스트=시그널 동일성(R2)·버전 체인·MISSING (feature-strategy-engine §12)."""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.main import app
from app.strategy.backtest import run_backtest
from app.strategy.params import Params

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False


# ── R2: 백테스트 d일 절단의 최종 계획 = 전체 실행의 d일 계획 (전략 코드 단일 소스, DB 불필요)
def test_truncated_backtest_final_plan_equals_full_run_plan():
    from tests.test_strategy_backtest import make_bars

    b200, blev = make_bars(n=600), make_bars(n=600, ratio=0.3)
    full = run_backtest(b200, blev, 100_000_000, Params(), collect_plans=True)
    for cut in (350, 450, 599):
        part = run_backtest(b200[:cut], blev[:cut], 100_000_000, Params(),
                            collect_plans=True, plan_final=True)
        final_plan = part.plans[-1]          # 절단 실행의 최신 신호 (인덱스 cut-1)
        reference = full.plans[cut - 1]      # 전체 실행에서 같은 날 계획
        assert final_plan.regime == reference.regime
        assert final_plan.orders == reference.orders
        assert final_plan.e_target == reference.e_target
        assert final_plan.gap_cancel_below == reference.gap_cancel_below


pytestmark_db = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]


@pytest.mark.integration
@pytest.mark.skipif(not DB_UP, reason="database not reachable")
class TestSignalBatch:
    @pytest.fixture(autouse=True)
    def seeded(self):
        from tests.test_backtest_api import seed_synthetic

        with SessionLocal() as s:
            seed_synthetic(s, "069500", "KODEX 200")
            seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)

    def test_batch_writes_snapshot_and_orders(self):
        from app.models import OrderSheetRow, SignalSnapshot
        from app.signals import run_signal_batch
        from sqlalchemy import select

        with SessionLocal() as s:
            snap = run_signal_batch(s)
            assert snap.status in ("OK", "INSUFFICIENT_HISTORY")
            assert snap.is_current is True
            if snap.status == "OK":
                assert snap.regime in ("BULL", "NEUTRAL", "BEAR")
                assert snap.e_target is not None
                # 지표(계산 근거) 포함
                assert "ma200" in snap.indicators and "grid" in snap.indicators

    def test_version_chain_on_rerun(self):
        from app.models import SignalSnapshot
        from app.signals import run_signal_batch
        from sqlalchemy import select

        with SessionLocal() as s:
            s1 = run_signal_batch(s)
            v1, d1 = s1.version, s1.trade_date
            s2 = run_signal_batch(s)
            assert s2.trade_date == d1 and s2.version == v1 + 1
            currents = s.scalars(select(SignalSnapshot).where(
                SignalSnapshot.trade_date == d1, SignalSnapshot.is_current)).all()
            assert len(currents) == 1 and currents[0].version == s2.version  # is_current 유일

    def test_missing_when_target_beyond_data(self):
        from app.signals import run_signal_batch

        with SessionLocal() as s:
            snap = run_signal_batch(s, target=date.today() + timedelta(days=30))
            assert snap.status == "MISSING"
            assert "last_bar" in snap.detail

    def test_api_requires_auth_and_returns_signal(self):
        client = TestClient(app, base_url="https://testserver")
        assert client.get("/signals/daily").status_code == 401
        email = f"sg{uuid.uuid4().hex[:8]}@stocklab.dev"
        token = client.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
        with SessionLocal() as s:
            from app.signals import run_signal_batch
            run_signal_batch(s)
        body = client.get("/signals/daily", headers={"Authorization": f"Bearer {token}"}).json()
        assert body["status"] in ("OK", "INSUFFICIENT_HISTORY")
        if body["status"] == "OK":
            assert isinstance(body["orders"], list)
