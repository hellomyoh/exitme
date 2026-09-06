"""LTM 미국 공식 — 잡 생성·디스패치·실전 전환·주문표 통합 (DB 필요, 2026-09-06).

구 미국 RAVG 쌍(QQQ_QLD/QQQ_TQQQ)은 신규 잡에서 거부되고, LTM_QLD 잡은 LTM 엔진으로 완주해 전환 포트의 주문표가 LTM 으로 나온다.
합성 QQQ/QLD 봉은 2021~2023 구간에만 넣고 모듈 종료 시 지운다 — test_dashboard 가 QQQ 2026-08-20 봉을 최신가로 기대하므로 겹치지 않게.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app
from app.services.ingest import get_or_create_instrument, upsert_daily_bars
from tests.test_backtest_api import DB_UP, make_user, run_job_inline

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]

SEED_FROM, SEED_TO = date(2021, 1, 4), date(2023, 12, 31)


def _seed_us(session, code: str, name: str, n: int, start: float, seed: int):
    inst = get_or_create_instrument(session, code, name, "NASDAQ")
    state = seed

    def rnd():
        nonlocal state
        state = (state * 1103515245 + 12345) % (2 ** 31)
        return state / (2 ** 31)

    bars, price, d = [], start, SEED_FROM
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        o = price
        c = max(price * (1 + (rnd() - 0.5) * 0.03 + 0.0005), 1000)
        bars.append({"trade_date": d, "open": round(o), "high": round(max(o, c) * 1.005),
                     "low": round(min(o, c) * 0.995), "close": round(c), "volume": 1000})
        price = c
        d += timedelta(days=1)
    assert d <= SEED_TO, "합성 구간이 정리 범위를 벗어남"
    upsert_daily_bars(session, inst.id, bars, source="pykrx")
    session.commit()
    return inst.id


@pytest.fixture(scope="module", autouse=True)
def us_market():
    with SessionLocal() as s:
        # 워밍업(MA200 + 12M 모멘텀 252) 뒤에도 거래 구간이 남도록 700 거래일 (2021-01 ~ 2023-10)
        ids = [_seed_us(s, "QQQ", "Invesco QQQ", 700, 40000.0, 21), _seed_us(s, "QLD", "ProShares Ultra QQQ", 700, 8000.0, 22)]
    yield
    with SessionLocal() as s:
        for i in ids:
            s.execute(text("DELETE FROM ohlcv_daily WHERE instrument_id = :i AND trade_date BETWEEN :a AND :b"),
                      {"i": i, "a": SEED_FROM, "b": SEED_TO})
        s.commit()


def test_legacy_us_ravg_pairs_rejected_and_ltm_job_runs():
    client = TestClient(app, base_url="https://testserver")
    h = {"Authorization": f"Bearer {make_user(client)}"}
    # date_to 는 마지막 합성 봉(≈2023-09-11) 이전이어야 전환 시드 거래(date_to 15:30)가 실행일 이전 상태에 잡힌다
    base = {"capital": 10_000_000, "date_from": "2023-01-03", "date_to": "2023-09-08"}
    for legacy in ("QQQ_QLD", "QQQ_TQQQ"):
        assert client.post("/backtests", json={**base, "etf": legacy}, headers=h).status_code == 422
    bt_id = client.post("/backtests", json={**base, "etf": "LTM_QLD"}, headers=h).json()["id"]
    assert run_job_inline(bt_id)["status"] == "DONE"
    got = client.get(f"/backtests/{bt_id}", headers=h).json()
    assert got["status"] == "DONE" and got["params"]["etf"] == "LTM_QLD"
    assert set(got["kpi"]) >= {"total_return", "mdd", "sharpe"}
    # 일지: LTM 주문 종류만 (RAVG 그리드 종류 없음), 노출 상한 2.0(+드리프트)
    journal = client.get(f"/backtests/{bt_id}/journal", headers=h).json()
    assert journal["items"], "일지 항목 없음"
    kinds = {o["kind"] for d in journal["items"] for o in (d["planned"] + d["fills"])}
    assert kinds and kinds <= {"ltm_entry", "ltm_exit", "ltm_lever_on", "ltm_lever_off", "ltm_rebal"}
    assert all(d["exposure"] <= 2.2 for d in journal["items"])

    # 실전 전환 → 미국 포트(params.etf=LTM_QLD) → 주문표 전략 LTM, 레버리지 종목명 QLD
    pf = client.post(f"/portfolios/from-backtest/{bt_id}", headers=h).json()
    sig = client.get(f"/signals/daily?portfolio_id={pf['id']}", headers=h).json()
    assert sig["strategy"] == "LTM" and sig["name_lev"] == "QLD" and sig["basis"] == "portfolio"
    assert sig["regime"] in ("BULL", "NEUTRAL", "BEAR") and sig["e_target"] in (0.0, 1.0, 2.0)
    for o in sig["orders"]:
        assert o["otype"] == "market" and o["kind"].startswith("ltm_") and o["instrument"] in ("K200", "LEV")
    acct = sig["account"]
    assert acct["equity"] > 0 and acct["qty_200"] >= 0 and acct["qty_lev"] >= 0
