"""실전 로트 전략 태그 (감사 A1·A2, 2026-09-12, 0027) — 체결 시점 로트 종류·익절가를 원장에 남기고 재구성에서 그대로 쓴다.

핵심 회귀 조건: **실전 재구성 = 백테스트 정본**. 백테스트가 만든 체결을 그대로 원장에 넣고(태그 포함) 저녁 재구성을 돌리면
최종 로트(종류·익절가·수량)와 다음 주문표가 백테스트와 비트 동일해야 한다. 태그 없는 로트만 종전 근사를 쓴다.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.lots import consume_sell, lot_tag, plan_day_context, rebuild_lots, sell_tag
from app.main import app
from app.strategy.backtest import run_backtest
from app.strategy.params import Params, round_tick
from app.strategy.planner import K200, LEV, Lot, Portfolio, grid_ratio, plan, prepare
from app.strategy.regime import Regime

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

KST = timezone(timedelta(hours=9))
P = Params()
needs_db = pytest.mark.skipif(not DB_UP, reason="database not reachable")


# ── 단위: 태그 규칙 ──────────────────────────────────────────────────────────────

def test_lot_tag_mirrors_backtest_fill_rule():
    """K200: 상승장 계획 → core(익절 없음) / 그 외 → grid + 체결가×(1+계획일 Grid) 올림. 레버리지: 주문 종류 = 로트 종류."""
    assert lot_tag("grid1", "buy", "NEUTRAL", 0.015, 68950, P) == ("grid", round_tick(68950 * 1.015, 5, up=True))
    assert lot_tag("boot", "buy", "NEUTRAL", 0.02, 70000, P) == ("grid", 71400)
    assert lot_tag("grid2", "buy", "BULL", 0.015, 67900, P) == ("core", None)
    assert lot_tag("lev_tact1", "buy", "BULL", 0.01, 20000, P) == ("lev_tact1", None)
    assert lot_tag("lev_strat", "buy", "BULL", None, 20000, P) == ("lev_strat", None)
    # 태그할 수 없는 경우 — 수동·미상·Grid 없음·매도
    assert lot_tag("manual", "buy", "NEUTRAL", 0.01, 1, P) == (None, None)
    assert lot_tag(None, "buy", "NEUTRAL", 0.01, 1, P) == (None, None)
    assert lot_tag("grid1", "buy", "NEUTRAL", None, 1, P) == (None, None)
    assert lot_tag("grid1", "sell", "NEUTRAL", 0.01, 1, P) == (None, None)


def test_sell_tag_keeps_tp_price_for_lot_attribution():
    assert sell_tag("tp", 69985) == ("tp", 69985)
    assert sell_tag("reduce", None) == ("reduce", None)
    assert sell_tag("lev_tact_exit", None) == ("lev_tact_exit", None)
    assert sell_tag("manual", 100) == (None, None)


def _rows(*specs):
    """(qty, price, lot_kind, tp_price, day_offset) → 원장 로트 dict."""
    base = datetime(2026, 9, 1, 15, 30, tzinfo=KST)
    return [{"instrument_id": 1, "qty": q, "price": p, "opened_at": base + timedelta(days=d),
             "lot_kind": k, "tp_price": tp} for q, p, k, tp, d in specs]


def test_consume_sell_targets_the_tp_lot_first_then_fifo():
    """익절 매도는 그 익절가의 로트를 먼저 소진 — FIFO 로만 하면 먼저 산 다른 로트가 깎여 팔린 로트가 남는다 (A2)."""
    lots = _rows((100, 70000, "grid", 71050, 0), (60, 67000, "grid", 68010, 1))
    consume_sell(lots, 1, 60, datetime(2026, 9, 5, 15, 30, tzinfo=KST), "tp", 68010)
    assert [(l["qty"], l["tp_price"]) for l in lots] == [(100, 71050), (0, 68010)]
    # 태그 없는 매도(수동)는 종전 FIFO
    lots = _rows((100, 70000, "grid", 71050, 0), (60, 67000, "grid", 68010, 1))
    consume_sell(lots, 1, 60, datetime(2026, 9, 5, 15, 30, tzinfo=KST))
    assert [l["qty"] for l in lots] == [40, 60]
    # 매도 시각 이후 취득 로트는 대상 아님
    lots = _rows((10, 70000, None, None, 3))
    consume_sell(lots, 1, 10, datetime(2026, 9, 2, 15, 30, tzinfo=KST))
    assert lots[0]["qty"] == 10


def test_consume_sell_strategic_and_tactical_are_attributed_by_kind():
    """전략 트랙 매도는 lev_strat 로트만, 전술 이탈은 전술 로트만 먼저 — 백테스트 ledger.sell(kinds=) 과 같은 귀속 (A1)."""
    at = datetime(2026, 9, 5, 15, 30, tzinfo=KST)
    lots = _rows((300, 20000, "lev_tact1", None, 0), (700, 20500, "lev_strat", None, 1))
    consume_sell(lots, 1, 200, at, "lev_strat")
    assert [(l["lot_kind"], l["qty"]) for l in lots] == [("lev_tact1", 300), ("lev_strat", 500)]
    consume_sell(lots, 1, 300, at, "lev_tact_exit")
    assert [(l["lot_kind"], l["qty"]) for l in lots] == [("lev_tact1", 0), ("lev_strat", 500)]


# ── 단위: 재구성 + 레짐 전환 재생 ─────────────────────────────────────────────────

def _mk(close=70000.0, atr=1400.0, n=40):
    const = lambda v: [v] * n
    from app.strategy.planner import Market
    return Market(opens=const(close), highs=const(close), lows=const(close), closes=const(close),
                  ma20=const(close), ma60=const(close), ma200=const(close), ema20=const(close),
                  atr20=const(atr), sigma20=const(0.15), sigma_down=const(0.1), sigma_ref=const(0.1))


def _dates(n=40, start=date(2026, 7, 1)):
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def test_rebuild_keeps_tagged_tp_and_replays_regime_conversions():
    """태그 로트: 저장된 익절가 그대로(오늘 종가와 무관) → 상승장 전환에 core 로 → 이탈일 종가 기준 익절가로 다시 grid."""
    dates, m = _dates(), _mk()
    leg = lambda _iid: K200
    row = {"instrument_id": 1, "qty": 100, "price": 68950, "opened_at": datetime(2026, 7, 10, 15, 30, tzinfo=KST),
           "lot_kind": "grid", "tp_price": 69985}
    # (1) 전환 없음 — 익절가 보존, 근사값(approx_tp) 을 쓰지 않는다
    reg = {d: "NEUTRAL" for d in dates}
    lots = rebuild_lots([row], leg, dates, reg, m, P, 39, "NEUTRAL", approx_tp=99999)
    assert (lots[0].kind, lots[0].tp_price) == ("grid", 69985)
    # (2) 체결 뒤 상승장 전환 → core, 익절 제거
    reg2 = {d: ("NEUTRAL" if i < 20 else "BULL") for i, d in enumerate(dates)}
    lots = rebuild_lots([row], leg, dates, reg2, m, P, 39, "BULL", approx_tp=99999)
    assert (lots[0].kind, lots[0].tp_price) == ("core", None)
    # (3) 상승장 → 중립 이탈 — 이탈 계획일(i=29) 종가 × (1+그날 Grid) 로 다시 grid
    reg3 = {d: ("NEUTRAL" if i < 20 else ("BULL" if i < 30 else "NEUTRAL")) for i, d in enumerate(dates)}
    lots = rebuild_lots([row], leg, dates, reg3, m, P, 39, "NEUTRAL", approx_tp=99999)
    g = grid_ratio(1400.0, 70000.0, P)
    assert (lots[0].kind, lots[0].tp_price) == ("grid", round_tick(70000 * (1 + g), 5, up=True))
    # (4) 체결일 이전 전환은 무관 — 체결일부터 재생
    reg4 = {d: ("BULL" if i < 5 else "NEUTRAL") for i, d in enumerate(dates)}
    lots = rebuild_lots([row], leg, dates, reg4, m, P, 39, "NEUTRAL", approx_tp=99999)
    assert (lots[0].kind, lots[0].tp_price) == ("grid", 69985)


def test_rebuild_untagged_lots_keep_the_approximation_and_lev_tags_survive():
    """태그 없는 K200 은 종전 근사(approx_tp / 상승장 core), 레버리지는 전술 태그가 그대로 살아 has1/has2 판정을 살린다 (A1)."""
    dates, m = _dates(), _mk()
    rows = [
        {"instrument_id": 1, "qty": 10, "price": 60000, "opened_at": datetime(2026, 7, 3, 15, 30, tzinfo=KST), "lot_kind": None, "tp_price": None},
        {"instrument_id": 2, "qty": 5, "price": 20000, "opened_at": datetime(2026, 7, 3, 15, 30, tzinfo=KST), "lot_kind": "lev_tact2", "tp_price": None},
        {"instrument_id": 2, "qty": 7, "price": 20000, "opened_at": datetime(2026, 7, 3, 15, 30, tzinfo=KST), "lot_kind": None, "tp_price": None},
    ]
    leg = lambda iid: LEV if iid == 2 else K200
    reg = {d: "NEUTRAL" for d in dates}
    lots = rebuild_lots(rows, leg, dates, reg, m, P, 39, "NEUTRAL", approx_tp=71050)
    assert (lots[0].kind, lots[0].tp_price) == ("grid", 71050)
    assert (lots[1].kind, lots[2].kind) == ("lev_tact2", "lev_strat")   # 태그 없음 → 종전처럼 전략 트랙
    lots = rebuild_lots(rows[:1], leg, dates, reg, m, P, 39, "BULL", approx_tp=71050)
    assert (lots[0].kind, lots[0].tp_price) == ("core", None)


# ── 동일성: 백테스트 체결 → 원장 → 재구성 = 백테스트 최종 상태·다음 주문표 ──────────────

def _synthetic_bars(n=700, seed=11, start=70000.0, vol=0.012):
    """결정론적 랜덤워크 일봉 — 레짐 전환·그리드 체결·익절·레버리지가 모두 나오도록 넉넉히."""
    import random
    rnd = random.Random(seed)
    out, px, d = [], start, date(2023, 1, 2)
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        o = px * (1 + rnd.gauss(0, vol / 3))
        c = o * (1 + rnd.gauss(0.0003, vol))
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2)))
        lo = min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
        out.append({"date": d.isoformat(), "open": round(o), "high": round(h), "low": round(lo), "close": round(c), "volume": 1})
        px = c
        d += timedelta(days=1)
    return out


def _lev_from(bars, mult=2.0, start=20000.0):
    """1배 봉에서 2배 봉을 만든다(일간 수익률 ×2) — 짝 종목 정합만 필요."""
    out, prev_c, px = [], None, start
    for b in bars:
        r = 0.0 if prev_c is None else (b["close"] / prev_c - 1) * mult
        o = px * (1 + (0.0 if prev_c is None else (b["open"] / prev_c - 1) * mult))
        c = px * (1 + r)
        out.append({"date": b["date"], "open": round(o), "high": round(max(o, c) * 1.004), "low": round(min(o, c) * 0.996),
                    "close": round(c), "volume": 1})
        prev_c, px = b["close"], c
    return out


def test_live_rebuild_from_tagged_ledger_equals_backtest_final_state_and_plan():
    """회귀 기준: 태그가 붙은 원장을 재구성하면 백테스트 최종 로트(종류·익절가·수량)와 다음 주문표가 비트 동일."""
    b200 = _synthetic_bars()
    blev = _lev_from(b200)
    cap = 100_000_000.0
    r = run_backtest(b200, blev, cap, P, collect_plans=True, plan_final=True)
    kinds = {f.kind for f in r.fills}
    assert {"grid1", "tp"} <= kinds, f"합성 봉에 그리드·익절 체결이 없다: {kinds}"

    def mk(bars):
        return prepare([float(b["open"]) for b in bars], [float(b["high"]) for b in bars],
                       [float(b["low"]) for b in bars], [float(b["close"]) for b in bars], P)
    m200, mlev = mk(b200), mk(blev)
    dates = [b["date"] for b in b200]
    reg_by_date = dict(zip(r.dates, r.regimes))
    plans_by_date = dict(zip(r.dates, r.plans[:len(r.dates)]))   # plans[-1] 은 plan_final

    # 백테스트 체결 → 태그 원장 (실전에서는 체결 가져오기·체결 등록이 같은 규칙으로 만든다)
    iid = {K200: 1, LEV: 2}
    lots: list[dict] = []
    for f in r.fills:
        at = datetime.combine(date.fromisoformat(f.date), time(15, 30), tzinfo=KST)
        if f.side == "buy":
            preg, pgrid = plan_day_context(dates, reg_by_date, m200, P, date.fromisoformat(f.date))
            kind, tp = lot_tag(f.kind, "buy", preg, pgrid, f.price, P)
            assert kind is not None, f"태그 실패 {f}"
            lots.append({"instrument_id": iid[f.instrument], "qty": f.qty, "price": f.price, "opened_at": at,
                         "lot_kind": kind, "tp_price": tp})
        else:
            tp_px = None
            if f.kind == "tp":
                cands = [o for o in plans_by_date[f.date].orders if o.kind == "tp"]
                hit = [o for o in cands if o.price == f.price] or [o for o in cands if o.qty == f.qty] or cands
                tp_px = hit[0].price
            kind, tp = sell_tag(f.kind, tp_px)
            consume_sell(lots, iid[f.instrument], f.qty, at, kind, tp)
            lots = [l for l in lots if l["qty"] > 0]

    last = len(dates) - 1
    regime_now = r.regimes[-1]
    approx_tp = round_tick(m200.closes[last] * (1 + grid_ratio(m200.atr20[last], m200.closes[last], P)), P.tick, up=True)
    rebuilt = rebuild_lots(lots, lambda i: LEV if i == 2 else K200, dates, reg_by_date, m200, P, last, regime_now, approx_tp)

    want = sorted((l["instrument"], l["kind"], l["tp_price"], l["qty"], l["price"]) for l in r.final_lots)
    got = sorted((l.instrument, l.kind, l.tp_price, l.qty, l.price) for l in rebuilt)
    assert got == want

    # 다음 주문표도 같다 — 실전이 이 상태로 plan() 을 부르면 백테스트의 plan_final 과 같은 주문
    live_plan = plan(last, m200, mlev, Regime(regime_now), Portfolio(cash=r.cash_curve[-1], lots=rebuilt), P,
                     days_since_start=None)
    bt_orders = [o for o in r.plans[-1].orders]
    assert [(o.instrument, o.side, o.otype, o.qty, o.price, o.kind) for o in live_plan.orders] == \
           [(o.instrument, o.side, o.otype, o.qty, o.price, o.kind) for o in bt_orders]


# ── 통합: 체결 등록 API 와 증권사 체결 가져오기가 태그를 남긴다 ───────────────────────────

def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"lt{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


@needs_db
def test_register_transaction_persists_strategy_tags():
    """주문표 줄에서 온 체결 등록(strategy_kind·plan_grid·plan_regime) → 거래 행에 lot_kind·tp_price. 임의 등록은 NULL."""
    from sqlalchemy import select
    from app.models import TradeTransaction
    from app.services.ingest import get_or_create_instrument

    with SessionLocal() as s:
        get_or_create_instrument(s, "069500", "KODEX 200", "KOSPI")
        get_or_create_instrument(s, "122630", "KODEX 레버리지", "KOSPI")
        s.commit()
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "태그", "market": "KR", "code_200": "069500"}, headers=h).json()["id"]
    at = "2026-09-01T15:30:00+09:00"
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 50_000_000, "executed_at": at}, headers=h)
    r1 = c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 10, "price": 68950,
                                    "executed_at": at, "strategy_kind": "grid1", "plan_grid": 0.015, "plan_regime": "NEUTRAL"}, headers=h)
    r2 = c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "122630", "qty": 5, "price": 20000,
                                    "executed_at": at, "strategy_kind": "lev_tact1", "plan_grid": 0.015, "plan_regime": "BULL"}, headers=h)
    r3 = c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 3, "price": 70000,
                                    "executed_at": at}, headers=h)
    r4 = c.post("/positions", json={"portfolio_id": pid, "kind": "sell", "code": "069500", "qty": 4, "price": 70000,
                                    "executed_at": "2026-09-02T15:30:00+09:00", "strategy_kind": "tp", "plan_price": 69985}, headers=h)
    assert (r1.status_code, r2.status_code, r3.status_code, r4.status_code) == (201, 201, 201, 201)
    with SessionLocal() as s:
        rows = s.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id == pid,
                                                       TradeTransaction.kind.in_(("buy", "sell")))
                         .order_by(TradeTransaction.id)).all()
    assert [(t.lot_kind, t.tp_price) for t in rows] == [
        ("grid", round_tick(68950 * 1.015, 5, up=True)), ("lev_tact1", None), (None, None), ("tp", 69985)]


@needs_db
def test_import_fills_tags_from_matching_broker_order(monkeypatch):
    """증권사 체결 가져오기: 주문번호가 우리 BrokerOrder 와 맞으면 그 주문의 종류·계획 Grid·레짐으로 태그, 아니면 NULL."""
    from sqlalchemy import select
    import app.broker as bk
    from app.models import BrokerCredential, BrokerOrder, TradeTransaction, TradePortfolio
    from app.services.ingest import get_or_create_instrument
    from app.services.kis_client import Execution

    with SessionLocal() as s:
        get_or_create_instrument(s, "069500", "KODEX 200", "KOSPI")
        get_or_create_instrument(s, "122630", "KODEX 레버리지", "KOSPI")
        s.commit()
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "가져오기태그", "market": "KR", "code_200": "069500"}, headers=h).json()["id"]
    d = date.today() - timedelta(days=1)
    with SessionLocal() as s:
        pf = s.get(TradePortfolio, pid)
        cred = BrokerCredential(user_id=pf.user_id, label="t", env="prod", app_key="k", app_secret="s", account_no="1", acnt_prdt_cd="01")
        s.add(cred); s.flush()
        pf.broker_credential_id = cred.id
        s.add_all([
            BrokerOrder(portfolio_id=pid, broker_credential_id=cred.id, plan_date=d, line_key="a", code="069500", instrument="K200",
                        kind="grid1", side="buy", otype="limit", qty=10, price=68950, order_no="G1", status="filled",
                        plan_grid=0.015, plan_regime="NEUTRAL"),
            BrokerOrder(portfolio_id=pid, broker_credential_id=cred.id, plan_date=d, line_key="b", code="122630", instrument="LEV",
                        kind="lev_tact2", side="buy", otype="market", qty=5, price=None, order_no="T2", status="filled",
                        plan_grid=0.015, plan_regime="BULL"),
        ])
        s.commit()
        cred_id = cred.id

    fake = [Execution(order_no="G1", trade_date=d, code="069500", side="buy", filled_qty=10, avg_price=68900, order_qty=10, remain_qty=0),
            Execution(order_no="T2", trade_date=d, code="122630", side="buy", filled_qty=5, avg_price=20100, order_qty=5, remain_qty=0),
            Execution(order_no="HTS9", trade_date=d, code="069500", side="buy", filled_qty=2, avg_price=69000, order_qty=2, remain_qty=0)]

    class FakeClient:
        def fetch_executions(self, start, end, only_filled=True):
            return fake

    monkeypatch.setattr(bk, "_client", lambda cred: FakeClient())
    with SessionLocal() as s:
        cred = s.get(BrokerCredential, cred_id)
        out = bk.import_fills_for_portfolio(s, pid, cred, days=3, dry_run=False)
        s.commit()
    assert out["added"] == 3
    with SessionLocal() as s:
        rows = s.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id == pid).order_by(TradeTransaction.id)).all()
    by_ref = {t.broker_ref.split(":")[0]: (t.lot_kind, t.tp_price) for t in rows}
    assert by_ref["G1"] == ("grid", round_tick(68900 * 1.015, 5, up=True))   # 체결가 기준 익절가 (지정가 아닌 실제 체결가)
    assert by_ref["T2"] == ("lev_tact2", None)
    assert by_ref["HTS9"] == (None, None)                                       # 우리 주문이 아님 → 태그 없음
