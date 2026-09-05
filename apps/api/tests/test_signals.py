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


@pytest.mark.integration
@pytest.mark.skipif(not DB_UP, reason="database not reachable")
def test_portfolio_basis_orders_respect_holdings():
    """내 실전 포트 기준 주문표 (2026-08-28 검토): 보유 로트 → 익절 주문 생성, 잔여예산 반영."""
    from tests.test_backtest_api import seed_synthetic

    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
        seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)
        from app.signals import run_signal_batch
        snap = run_signal_batch(s)
        if snap.status != "OK":
            pytest.skip("synthetic data insufficient for OK signal")

    client = TestClient(app, base_url="https://testserver")
    email = f"pb{uuid.uuid4().hex[:8]}@stocklab.dev"
    token = client.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    pid = client.post("/portfolios", json={"name": "내 계좌 검증"}, headers=h).json()["id"]
    now = "2025-07-01T10:00:00+09:00"  # 합성 봉 범위 내 — 주문표는 신호 기준일 이전 체결만 반영 (B안, 2026-09-02)
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 100_000_000,
                                    "executed_at": now}, headers=h)
    client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                    "qty": 200, "price": 60000, "executed_at": now}, headers=h)

    body = client.get(f"/signals/daily?portfolio_id={pid}", headers=h).json()
    assert body["basis"] == "portfolio" and body["account"]["qty_200"] == 200
    kinds = {o["kind"] for o in body["orders"]}
    if body["regime"] == "NEUTRAL":
        assert "tp" in kinds  # 보유 로트 → 익절 매도 생성 (모델 기준에는 없던 주문)
    # 모델 기준과 다른 주문 구성이어야 함 (보유 반영)
    model = client.get("/signals/daily", headers=h).json()
    assert body["orders"] != model["orders"]
    # 신호 이력 엔드포인트
    j = client.get("/signals/journal?days=10", headers=h).json()
    assert len(j["items"]) > 0 and {"date", "planned", "fills", "day_pnl"} <= set(j["items"][0])


# ── 2026-09-02 B안: 주문표 = 신호 기준일 종가 시점 상태 (feature-portfolio §5·§12)

def _last_bar_and_exec_day(code="069500"):
    from datetime import timedelta as _td
    from sqlalchemy import select
    from app.models import Instrument, OhlcvDaily
    with SessionLocal() as s:
        inst = s.scalar(select(Instrument).where(Instrument.code == code))
        from sqlalchemy import func
        last = s.scalar(select(func.max(OhlcvDaily.trade_date)).where(OhlcvDaily.instrument_id == inst.id))
    exec_day = last + _td(days=1)
    while exec_day.weekday() >= 5:
        exec_day += _td(days=1)
    return last, exec_day


def test_same_day_fill_does_not_change_order_sheet():
    """실행일 당일 체결 등록 → 오늘의 주문표 불변. 기준일 이전 소급 거래 → 반영 (자가 치유)."""
    from tests.test_backtest_api import seed_synthetic

    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
        seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)

    client = TestClient(app, base_url="https://testserver")
    email = f"bf{uuid.uuid4().hex[:8]}@stocklab.dev"
    token = client.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/portfolios", json={"name": "b안 검증"}, headers=h).json()["id"]
    base_day, exec_day = _last_bar_and_exec_day()

    past = f"{(base_day)}T10:00:00+09:00"
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 50_000_000,
                                    "executed_at": past}, headers=h)
    client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                    "qty": 100, "price": 60000, "executed_at": past}, headers=h)
    before = client.get(f"/signals/daily?portfolio_id={pid}", headers=h).json()
    assert before["basis"] == "portfolio" and before["account"]["qty_200"] == 100

    # 실행일 당일 체결 등록 — 주문표는 바뀌면 안 된다 (HTS 주문장은 원수량 그대로)
    client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                    "qty": 30, "price": 59000,
                                    "executed_at": f"{exec_day}T10:05:00+09:00"}, headers=h)
    after = client.get(f"/signals/daily?portfolio_id={pid}", headers=h).json()
    assert after["orders"] == before["orders"]
    assert after["account"] == before["account"]     # 계산 기준 상태도 동결

    # 기준일 이전 소급 입금 — 계획이 올바르게 갱신돼야 한다 (동결이 아니라 시점 재생임을 증명)
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 10_000_000,
                                    "executed_at": past}, headers=h)
    healed = client.get(f"/signals/daily?portfolio_id={pid}", headers=h).json()
    assert healed["account"]["cash"] == before["account"]["cash"] + 10_000_000


def test_state_replay_matches_current_ledger():
    """동등성 고정: cutoff=미래의 시점 재생 == 현재 로트 테이블·현금 원장 (등록 경로와 FIFO 일치)."""
    from datetime import date as _date
    from sqlalchemy import select
    from app.signals import _state_before
    from app.models import PositionLot
    from tests.test_backtest_api import seed_synthetic

    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")

    client = TestClient(app, base_url="https://testserver")
    email = f"eq{uuid.uuid4().hex[:8]}@stocklab.dev"
    token = client.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/portfolios", json={"name": "동등성"}, headers=h).json()["id"]
    ts = "2025-06-01T10:00:00+09:00"
    client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 20_000_000,
                                    "executed_at": ts}, headers=h)
    client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                    "qty": 100, "price": 60000, "executed_at": ts}, headers=h)
    client.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500",
                                    "qty": 50, "price": 61000, "executed_at": "2025-06-02T10:00:00+09:00"}, headers=h)
    client.post("/positions", json={"portfolio_id": pid, "kind": "sell", "code": "069500",
                                    "qty": 120, "price": 65000, "executed_at": "2025-06-03T10:00:00+09:00"}, headers=h)
    with SessionLocal() as s:
        lots, cash = _state_before(s, pid, _date(2100, 1, 1))
        table = s.scalars(select(PositionLot).where(PositionLot.portfolio_id == pid)
                          .order_by(PositionLot.opened_at, PositionLot.id)).all()
        assert [(l["qty"], l["price"]) for l in lots] == [(t.qty_open, t.price) for t in table]
    summ = client.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).json()
    assert cash == summ["cash"]


def test_per_portfolio_frozen_formula_isolation():
    """공식 격리 (2026-09-05 지시): 포트에 동결된 algo 가 있으면 그것만 쓰고, 같은 계정의
    다른 포트(설정 추종)와 섞이지 않는다. 설정 변경도 동결 포트에 영향 없음."""
    from tests.test_backtest_api import seed_synthetic

    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
        seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)
        from app.signals import run_signal_batch
        if run_signal_batch(s).status != "OK":
            pytest.skip("synthetic data insufficient")

    client = TestClient(app, base_url="https://testserver")
    email = f"iso{uuid.uuid4().hex[:8]}@x.dev"
    token = client.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    from tests.test_portfolios import _promote_admin
    _promote_admin(email)  # 알고리즘 설정 변경은 관리자 전용 (2026-09-05)
    now = "2025-07-01T10:00:00+09:00"

    def mk_port(name):
        pid = client.post("/portfolios", json={"name": name}, headers=h).json()["id"]
        client.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 100_000_000,
                                        "executed_at": now}, headers=h)
        return pid

    pid_frozen, pid_follow = mk_port("동결"), mk_port("추종")
    # 포트 A 에 극단 grid_coef 동결 (전환 경로가 저장하는 형태 그대로)
    from app.models import TradePortfolio
    with SessionLocal() as s:
        pf = s.get(TradePortfolio, pid_frozen)
        pf.params = {**(pf.params or {}), "algo": {"grid_coef": 2.0}}
        s.commit()

    a = client.get(f"/signals/daily?portfolio_id={pid_frozen}", headers=h).json()
    b = client.get(f"/signals/daily?portfolio_id={pid_follow}", headers=h).json()
    assert a["algo_source"] == "portfolio" and a["algo_overrides"] == {"grid_coef": 2.0}
    assert b["algo_source"] == "settings"
    ga = [o["price"] for o in a["orders"] if o["kind"] == "grid1"]
    gb = [o["price"] for o in b["orders"] if o["kind"] == "grid1"]
    if ga and gb:
        assert ga[0] < gb[0]  # coef 2.0 → 더 깊은 하락에서 매수 (같은 계정, 다른 공식 동시 운용)

    # 사용자 설정 변경 → 추종 포트만 영향, 동결 포트 불변
    client.put("/settings/algorithm", json={"values": {"grid_coef": 0.3}}, headers=h)
    a2 = client.get(f"/signals/daily?portfolio_id={pid_frozen}", headers=h).json()
    b2 = client.get(f"/signals/daily?portfolio_id={pid_follow}", headers=h).json()
    assert a2["orders"] == a["orders"] and a2["algo_overrides"] == {"grid_coef": 2.0}
    assert b2["algo_overrides"] == {"grid_coef": 0.3}
    if gb:
        gb2 = [o["price"] for o in b2["orders"] if o["kind"] == "grid1"]
        assert not gb2 or gb2[0] > gb[0]

    # 개정으로 사라진 키가 동결본에 있어도 무시(견고성)
    with SessionLocal() as s:
        pf = s.get(TradePortfolio, pid_frozen)
        pf.params = {**pf.params, "algo": {"grid_coef": 2.0, "ghost_param": 9}}
        s.commit()
    assert client.get(f"/signals/daily?portfolio_id={pid_frozen}", headers=h).status_code == 200


def test_conversion_freezes_algo_key():
    """전환 시 params['algo'] 가 항상 명시 저장 — 잡에 algo 없으면 {} (기본값 동결) (2026-09-05)."""
    from tests.test_backtest_api import run_job_inline, seed_synthetic

    with SessionLocal() as s:
        seed_synthetic(s, "069500", "KODEX 200")
        seed_synthetic(s, "122630", "KODEX 레버리지", start=20000.0, seed=9)

    client = TestClient(app, base_url="https://testserver")
    token = client.post("/auth/register", json={"email": f"cvz{uuid.uuid4().hex[:8]}@x.dev",
                                                "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    bt = client.post("/backtests", json={"capital": 30_000_000, "date_from": "2024-06-03",
                                         "date_to": "2025-06-30", "etf": "KODEX",
                                         "algo": {"grid_coef": 1.25}}, headers=h).json()
    run_job_inline(bt["id"])
    pid = client.post(f"/portfolios/from-backtest/{bt['id']}", headers=h).json()["id"]
    from app.models import TradePortfolio
    with SessionLocal() as s:
        pf = s.get(TradePortfolio, pid)
        assert pf.params.get("algo", {}).get("grid_coef") == 1.25
    sig = client.get(f"/signals/daily?portfolio_id={pid}", headers=h).json()
    assert sig["algo_source"] == "portfolio" and sig["algo_overrides"].get("grid_coef") == 1.25
