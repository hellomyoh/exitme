"""읽기 전용 RAVG 체결률 연구. 제품 규칙 변경 없이 독립 프로세스에서 실행.

python -m scripts.fill_rate_audit [--windows] [--sensitivity]
결과는 stdout JSONL. 날짜/지표/매도 규칙을 고정한 매수 가격 실험 포함.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics as st
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import date
from unittest.mock import patch

from sqlalchemy import text

from app.backtests import load_aligned_bars
from app.db import SessionLocal
from app.strategy import backtest as bt
from app.strategy.params import Params, round_tick
from app.strategy.planner import K200, LEV, plan as original_plan

CAPITAL = 100_000_000
CONFIGS = {
    "current": ({}, None),
    "no_boot": ({"boot_frac": 0}, None),
    "entry_half": ({}, 0.5),
    "entry_close": ({}, 0.0),
    "grid_two_thirds": ({"grid_coef": 0.5, "grid_min": 0.008 * 2 / 3,
                          "grid_max": 0.025 * 2 / 3}, None),
    "boot15_always": ({"boot_days": 100_000}, None),
    "boot100_limit": ({"boot_days": 100_000, "boot_frac": 1.0, "boot_bear_mult": 0}, None),
    "boot100_market": ({"boot_days": 100_000, "boot_frac": 1.0, "boot_bear_mult": 0,
                        "boot_otype": "market", "boot_market_slippage": 0.001}, None),
}


def emit(tag, value):
    print(json.dumps({"tag": tag, **value}, ensure_ascii=False, default=str), flush=True)


def simulate(a, b, params, scale=None, start=None, prior=None):
    def wrapped(i, m200, mlev, prev, pf, p, days_since_start=None):
        if start is not None and i == start and prior is not None:
            prev = prior
        result = original_plan(i, m200, mlev, prev, pf, p, days_since_start)
        if scale is None:
            return result
        orders = []
        for od in result.orders:
            if od.side == "buy" and od.kind.startswith("grid"):
                close = m200.closes[i]
                px = round_tick(close - scale * (close - od.price), p.tick, up=False)
                qty = int(od.qty * od.price // px)  # 기존 주문 명목예산 이하, 매도 Grid 불변
                assert qty * px <= od.qty * od.price
                if qty:
                    orders.append(replace(od, price=px, qty=qty))
            else:
                orders.append(od)
        return replace(result, orders=tuple(orders))

    with patch.object(bt, "plan", wrapped):
        return bt.run_backtest(a, b, CAPITAL, params, start_index=start, collect_plans=True)


def measure(r, a, b, params, start=0):
    bydate = defaultdict(list)
    for f in r.fills:
        bydate[f.date].append(f)
    cats = defaultdict(Counter)
    day_counts = Counter()
    active = []
    for j, p in enumerate(r.plans):
        if p.status != "OK":
            continue
        active.append(j)
        i = start + j
        nxt = a[i + 1]
        buys = [o for o in p.orders if o.side == "buy"]
        fills = [f for f in bydate[nxt["date"]] if f.side == "buy"]
        keys = Counter((f.instrument, f.kind) for f in fills)
        day_counts["active"] += 1
        day_counts["buy_order_days"] += bool(buys)
        day_counts["buy_fill_days"] += bool(fills)
        day_counts["any_fill_days"] += bool(bydate[nxt["date"]])
        if not buys:
            day_counts["no_buy_" + p.regime.value] += 1
        gap = (params.flags.f5_gap_filter and p.gap_cancel_exact is not None
               and nxt["open"] <= p.gap_cancel_exact)
        for od in buys:
            cat = cats[od.kind]
            cat["orders"] += 1
            reference_px = od.price if od.price is not None else (a[i]["close"] if od.instrument == K200 else b[i]["close"])
            cat["ordered_qty"] += od.qty
            cat["ordered_reference_value"] += od.qty * reference_px
            key = (od.instrument, od.kind)
            if keys[key]:
                keys[key] -= 1
                cat["fills"] += 1
                cat["filled_qty"] += od.qty  # 현재 매수 실행기는 전량 또는 미체결
                cat["filled_reference_value"] += od.qty * reference_px
            elif od.instrument == K200 and gap:
                cat["gap_cancel"] += 1
            elif od.otype == "limit" and nxt["low"] > od.price:
                cat["price_not_reached"] += 1
            else:
                cat["cash_skip"] += 1
        assert not any(keys.values()), "체결-주문 연결 실패"
    totals = Counter()
    for cat in cats.values():
        assert cat["orders"] == sum(cat[k] for k in ("fills", "gap_cancel", "price_not_reached", "cash_skip"))
        totals.update(cat)
    n = len(active)
    first = active[0]
    # fills 가격은 원 단위 반올림됨: 회전율·수수료 추산치(정확 원장비용 아님).
    turnover = sum(f.price * f.qty for f in r.fills) / st.mean(r.equity[first:]) / (n / 252)
    exposure = [(r.qty_200[j] * a[start+j+1]["close"] + 2*r.qty_lev[j] * b[start+j+1]["close"])
                / r.equity[j] for j in active]
    k200_orders = sum(c["orders"] for k, c in cats.items() if k.startswith("grid") or k == "boot")
    k200_fills = sum(c["fills"] for k, c in cats.items() if k.startswith("grid") or k == "boot")
    kcats = [c for k,c in cats.items() if k.startswith("grid") or k == "boot"]
    ordered_qty = sum(c["ordered_qty"] for c in kcats)
    ordered_value = sum(c["ordered_reference_value"] for c in kcats)
    first_k200 = next((j-first+1 for j in active if any(f.side == "buy" and f.instrument == K200
                       for f in bydate[r.dates[j]])), None)
    return {"kpi": r.kpi, "days": dict(day_counts), "buy_reasons": dict(totals),
            "kinds": {k: dict(v) for k, v in cats.items()}, "k200_fill_rate": k200_fills/k200_orders if k200_orders else None,
            "k200_qty_fill_rate": sum(c["filled_qty"] for c in kcats)/ordered_qty if ordered_qty else None,
            "k200_reference_value_fill_rate": sum(c["filled_reference_value"] for c in kcats)/ordered_value if ordered_value else None,
            "fills_per_year": len(r.fills)/(n/252), "turnover_two_way_per_year": turnover,
            "commission_estimate": sum(f.price*f.qty for f in r.fills)*params.commission,
            "avg_cash_ratio": st.mean(r.cash_curve[j]/r.equity[j] for j in active),
            "avg_actual_exposure": st.mean(exposure), "avg_target_exposure": st.mean(r.plans[j].e_target for j in active),
            "first_k200_fill_day": first_k200, "start": r.dates[first], "end": r.dates[-1]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", action="store_true")
    parser.add_argument("--sensitivity", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as s:
        s.execute(text("SET TRANSACTION READ ONLY"))
        a, b, fp = load_aligned_bars(s, date(2017, 1, 2), date(2026, 9, 11))
        s.rollback()
    assert all(x["date"] == y["date"] for x, y in zip(a, b))
    sha = hashlib.sha256(json.dumps([a, b], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    emit("data", {"rows": len(a), "from": a[0]["date"], "to": a[-1]["date"], "fingerprint": fp,
                  "sha256": sha, "params": asdict(Params()), "configs": CONFIGS})
    base = bt.run_backtest(a, b, CAPITAL, Params(), collect_plans=True)
    check = simulate(a, b, Params())
    assert asdict(base) == asdict(check), "현재 설정 wrapper가 원본 결과를 변경함"
    # 실제 생성된 현행 Grid 가격과 호가 정규화 검산.
    checked = 0
    for i, p in enumerate(base.plans):
        for od in p.orders:
            if od.kind.startswith("grid"):
                k = int(od.kind[4:])
                expected = round_tick(a[i]["close"]*(1-p.indicators["grid"]*k), Params().tick, up=False)
                assert od.price == expected
                checked += 1
    emit("checks", {"baseline_identity": True, "grid_prices_verified": checked})
    for name, (cfg, scale) in CONFIGS.items():
        p = replace(Params(), **cfg)
        r = simulate(a, b, p, scale)
        emit("full", {"variant": name, **measure(r, a, b, p)})
    # 동일 상태/같은 날짜의 가격 도달률: 전략별 분모 변화를 제거한 참고 지표.
    reach = defaultdict(Counter)
    for i, p in enumerate(base.plans):
        if p.status != "OK" or p.regime.value == "BEAR":
            continue
        gap = a[i+1]["open"] <= a[i]["close"] - 1.5*p.indicators["atr20"]
        for k in (0, 0.5, 1, 2, 3):
            px = round_tick(a[i]["close"]*(1-k*p.indicators["grid"]), 5, up=False)
            reach[str(k)]["days"] += 1
            reach[str(k)]["price_touch"] += a[i+1]["low"] <= px
            reach[str(k)]["touch_after_gap_filter"] += not gap and a[i+1]["low"] <= px
    emit("same_day_reach", {"levels": {k: dict(v) for k,v in reach.items()}})
    if args.sensitivity:
        for cost_name, cost in {"no_extra_fund_fee": {"fee_200": 0, "fee_lev": 0},
                               "double_commission": {"commission": 0.0003}}.items():
            for name, (cfg, scale) in CONFIGS.items():
                p = replace(Params(), **(cfg | cost))
                r = simulate(a, b, p, scale)
                emit("sensitivity", {"cost": cost_name, "variant": name, "kpi": r.kpi})
        costs = dict(commission=0, lev_tax=0, fee_200=0, fee_lev=0,
                     slippage_market=0, boot_market_slippage=0)
        for name in ("current", "entry_half", "entry_close", "boot100_market"):
            cfg, scale = CONFIGS[name]
            r = simulate(a, b, replace(Params(), **(cfg | costs)), scale)
            emit("zero_cost", {"variant": name, "kpi": r.kpi})
    if args.windows:
        # 252거래일 창, 21일 간격(겹침) 및 252일 간격(비중첩)을 구분. 전일 시장 레짐만 이어받고 계좌는 현금으로 재시작.
        starts = sorted(set(range(300, len(a)-252, 21)) | set(range(300, len(a)-252, 252)))
        samples = defaultdict(list)
        for sn, s0 in enumerate(starts):
            aa, bb = a[:s0+253], b[:s0+253]
            prior = base.plans[s0-1].regime
            for name, (cfg, scale) in CONFIGS.items():
                p = replace(Params(), **cfg)
                r = simulate(aa, bb, p, scale, s0, prior)
                m = measure(r, aa, bb, p, s0)
                samples[name].append({"signal_date": a[s0]["date"],
                                      "nonoverlap": (s0-300)%252 == 0, "regime": r.plans[0].regime.value,
                                      "ret": r.kpi["total_return"], "mdd": r.kpi["mdd"],
                                      "first_k200": m["first_k200_fill_day"], "fill_rate": m["k200_fill_rate"]})
            if sn % 10 == 0:
                emit("progress", {"windows_done": sn+1, "windows_total": len(starts)})
        for name, rows in samples.items():
            for group in ("all", "nonoverlap", "BULL", "NEUTRAL", "BEAR", "pre2023", "from2023"):
                pairs = [(x,y) for x,y in zip(samples["current"], rows)
                         if group == "all" or (group == "nonoverlap" and x["nonoverlap"])
                         or group == x["regime"] or (group == "pre2023" and x["signal_date"] < "2023")
                         or (group == "from2023" and x["signal_date"] >= "2023")]
                if not pairs:
                    continue
                ds = [y["ret"]-x["ret"] for x,y in pairs]
                firsts = [y["first_k200"] for _,y in pairs if y["first_k200"] is not None]
                emit("window_summary", {"variant": name, "group": group, "n": len(pairs),
                     "delta_mean": st.mean(ds), "delta_median": st.median(ds), "delta_min": min(ds), "delta_max": max(ds),
                     "wins": sum(d > 1e-10 for d in ds), "losses": sum(d < -1e-10 for d in ds),
                     "mdd_worse": sum(y["mdd"] < x["mdd"]-1e-10 for x,y in pairs),
                     "first_k200_median": st.median(firsts) if firsts else None,
                     "no_k200_in_5days": sum(y["first_k200"] is None or y["first_k200"] > 5 for _,y in pairs),
                     "no_k200_ever": sum(y["first_k200"] is None for _,y in pairs),
                     "nonoverlap_deltas": ds if group == "nonoverlap" else None})


if __name__ == "__main__":
    main()
