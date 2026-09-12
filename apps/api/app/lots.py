"""실전 로트의 전략 메타데이터 — 체결 시점에 붙이고(태그) 저녁 재구성에서 그대로 쓴다 (감사 A1·A2, 2026-09-12).

백테스트(strategy/backtest.py)는 체결 순간에 로트 종류(core/grid/lev_strat/lev_tact1/lev_tact2)와 익절가 스냅샷을
정하고, 레짐이 바뀌면 apply_regime_conversion 으로 재분류한다. 실전 주문표는 매일 저녁 원장(거래 행)에서 로트를
다시 만들기 때문에 그 정보를 거래 행에 두지 않으면 매일 잃는다 — 전술 레버리지가 전략 보유로 둔갑해 EMA 회복
청산이 영구히 죽고(A1), 익절가가 매일 종가를 따라 움직인다(A2). 이 모듈이 두 경로를 같은 규칙으로 잇는다.

태그가 없는 로트(시딩·수동 등록·구형 행)는 종전 근사(익절가 = 최근 종가 × (1+오늘 Grid))를 그대로 쓴다
(docs/manual-holdings-tp-review-20260903.md). 태그가 붙은 새 로트가 쌓일수록 실전은 백테스트로 수렴한다.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import date, datetime

from app.strategy.params import Params, round_tick
from app.strategy.planner import K200, LEV, Lot, grid_ratio

K200_BUY_KINDS = frozenset({"boot", "grid1", "grid2", "grid3"})
LEV_BUY_KINDS = frozenset({"lev_strat", "lev_tact1", "lev_tact2"})
TACTICAL = frozenset({"lev_tact1", "lev_tact2"})


def lot_tag(order_kind: str | None, side: str, plan_regime: str | None, plan_grid: float | None,
            fill_px: int | float, params: Params) -> tuple[str | None, int | None]:
    """매수 주문 종류 + 계획일 레짐·Grid + 체결가 → (lot_kind, tp_price). 백테스트 체결 블록과 같은 규칙.

    K200(boot·grid1~3): 상승장 계획이면 core(익절 없음), 아니면 grid + 익절가 = 체결가 × (1+계획일 Grid) 올림.
    레버리지: 주문 종류가 곧 로트 종류. 그 밖(수동·미상)은 태그 없음.
    """
    if side != "buy" or not order_kind:
        return None, None
    if order_kind in LEV_BUY_KINDS:
        return order_kind, None
    if order_kind in K200_BUY_KINDS:
        if plan_regime == "BULL" and params.flags.f1_no_tp_in_bull:
            return "core", None
        if plan_grid is None:
            return None, None
        return "grid", round_tick(float(fill_px) * (1 + float(plan_grid)), params.tick, up=True)
    return None, None


def sell_tag(order_kind: str | None, order_price: int | None) -> tuple[str | None, int | None]:
    """매도 거래에 남길 귀속 정보 — 종류(tp·reduce·lev_strat·lev_tact_exit·lev_liq)와 익절 지정가.

    백테스트는 익절을 그 익절가의 로트에, 전략·전술 매도를 그 종류의 로트에 귀속시킨다(ledger.sell 의 lot=/kinds=).
    """
    if not order_kind:
        return None, None
    if order_kind == "tp":
        return "tp", int(order_price) if order_price else None
    if order_kind in ("reduce", "lev_strat", "lev_tact_exit", "lev_liq"):
        return order_kind, None
    return None, None


def _prefers(sell_kind: str | None, tp_price: int | None):
    """매도 종류별 우선 소진 조건 — 없으면 순수 FIFO."""
    if sell_kind == "tp" and tp_price:
        return lambda l: l.get("tp_price") == tp_price
    if sell_kind == "lev_strat":
        return lambda l: l.get("lot_kind") == "lev_strat"
    if sell_kind == "lev_tact_exit":
        return lambda l: l.get("lot_kind") in TACTICAL
    return None


def consume_sell(lots: list[dict], instrument_id: int, qty: int, executed_at: datetime,
                 sell_kind: str | None = None, tp_price: int | None = None) -> None:
    """매도 거래를 로트 목록(dict, 시간순)에 반영 — 귀속 조건에 맞는 로트를 먼저, 나머지는 FIFO. 제자리 수정."""
    def eligible(l: dict) -> bool:
        return l["instrument_id"] == instrument_id and l["qty"] > 0 and not (l["opened_at"] > executed_at)

    pref = _prefers(sell_kind, tp_price)
    order = [l for l in lots if eligible(l)]
    if pref is not None:
        order = [l for l in order if pref(l)] + [l for l in order if not pref(l)]
    remaining = qty
    for l in order:
        if remaining <= 0:
            break
        take = min(l["qty"], remaining)
        l["qty"] -= take
        remaining -= take


def rebuild_lots(lot_rows: list[dict], leg_of, dates: list[str], reg_by_date: dict[str, str],
                 m200, params: Params, last: int, regime_now: str, approx_tp: int) -> list[Lot]:
    """원장 로트 행 → 플래너 Lot.

    태그 있는 행: 저장된 종류·익절가에서 출발해 체결일 이후 레짐 전환을 재생(apply_regime_conversion 과 같은 규칙)한다.
      - → BULL 전환: grid → core, 익절 제거
      - BULL → 비상승 전환: core → grid, 익절가 = 전환일 종가 × (1+전환일 Grid) 올림
      체결일 d 의 로트는 d 종가 계획에서 결정된 전환부터 적용받는다(백테스트: 체결 → 같은 날 종가 plan → 전환).
    태그 없는 행: 종전 근사 — 상승장이면 core, 아니면 grid + approx_tp(최근 종가 × (1+오늘 Grid)).
    leg_of(instrument_id) → "K200" | "LEV".
    """
    out: list[Lot] = []
    for row in lot_rows:
        leg = leg_of(row["instrument_id"])
        kind, tp = row.get("lot_kind"), row.get("tp_price")
        if leg == LEV:
            out.append(Lot(LEV, row["qty"], row["price"], kind if kind in LEV_BUY_KINDS else "lev_strat", None, 0))
            continue
        if kind not in ("grid", "core"):
            if regime_now == "BULL" and params.flags.f1_no_tp_in_bull:
                out.append(Lot(K200, row["qty"], row["price"], "core", None, 0))
            else:
                out.append(Lot(K200, row["qty"], row["price"], "grid", approx_tp, 0))
            continue
        # 태그 로트 — 체결일부터 어제 계획일까지 레짐 전환 재생
        opened = row["opened_at"]
        d0 = (opened.date() if isinstance(opened, datetime) else opened).isoformat()
        i = bisect_left(dates, d0)
        while i < last:
            old, new = reg_by_date.get(dates[i]), reg_by_date.get(dates[i + 1])
            if old and new and old != new:
                if new == "BULL" and params.flags.f1_no_tp_in_bull and kind == "grid":
                    kind, tp = "core", None
                elif old == "BULL" and new != "BULL" and kind == "core":
                    g = grid_ratio(m200.atr20[i], m200.closes[i], params) if m200.atr20[i] is not None else None
                    if g is not None:
                        kind, tp = "grid", round_tick(m200.closes[i] * (1 + g), params.tick, up=True)
            i += 1
        out.append(Lot(K200, row["qty"], row["price"], kind, tp, 0))
    return out


def plan_day_context(dates: list[str], reg_by_date: dict[str, str], m200, params: Params,
                     fill_date: date) -> tuple[str | None, float | None]:
    """체결일 → 그 체결을 낳은 계획일(직전 봉)의 레짐·Grid. 백테스트 체결에서 태그를 만들 때(동일성 테스트) 쓴다."""
    i = bisect_left(dates, fill_date.isoformat())
    if i <= 0 or i >= len(dates) or dates[i] != fill_date.isoformat():
        return None, None
    reg = reg_by_date.get(dates[i])              # 계획일 종가에 결정된 상태 = 체결일 키에 기록된 레짐
    g = grid_ratio(m200.atr20[i - 1], m200.closes[i - 1], params) if m200.atr20[i - 1] is not None else None
    return reg, g
