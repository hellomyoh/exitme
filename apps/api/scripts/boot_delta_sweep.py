# -*- coding: utf-8 -*-
"""초기 진입가 위치(boot_delta) 스윕 — 실제 엔진(run_backtest = 실전 플래너와 같은 코드)으로 콜드 스타트 창(250거래일)을
2017~2026 10년 데이터에서 5거래일 간격 시작(≈360개)으로 돌려 현행(δ0)·대안·부트스트랩 없음(그리드만)을 짝지어 비교한다.
δ: 지정가 = 종가 × (1 − δ·Grid). δ<0 은 종가 위 지정가(시가가 그 아래면 시가 체결 → 사실상 시가 매수에 가까움).
용법(api 컨테이너 안, 개발 DB 의 10년 일봉 사용 — 읽기만):
    docker compose exec -T api python scripts/boot_delta_sweep.py <stage> [step] [offset] [stage별 인자]
    stage 1 = δ 스윕(f.15 N10 bear.5) · 2 = f×N (인자 δ) · 3 = bear 배수 (인자 δ f N) · 4 = 후보 연도/레짐 분해 (인자 "δ,f,N,bear;…")
    · 5 = 최종 후보 7종 요약표.   step 5 = 시작 365개(5거래일 간격), step 50 = 준독립 37개.
결과·결론: THROUGHLINE/docs/boot-entry-price-study-20260909.md
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import statistics as st
import sys
from datetime import date

from app.backtests import load_aligned_bars
from app.db import SessionLocal
from app.strategy.backtest import run_backtest
from app.strategy.params import Params

CAPITAL, H = 100_000_000.0, 250
STAGE = sys.argv[1] if len(sys.argv) > 1 else "1"
STEP = int(sys.argv[2]) if len(sys.argv) > 2 else 5
OFF = int(sys.argv[3]) if len(sys.argv) > 3 else 0
with SessionLocal() as s:
    b200, blev, _ = load_aligned_bars(s, date(2010, 1, 1), date(2100, 1, 1), codes=("069500", "122630"))
n = len(b200)
dates = [b["date"] for b in b200]
idx_of = {d: i for i, d in enumerate(dates)}
close200 = [float(b["close"]) for b in b200]
closelev = [float(b["close"]) for b in blev]
starts = list(range(300 + OFF, n - H - 1, STEP))


def run(cfg: dict, s0: int) -> dict:
    p = Params(**cfg)
    r = run_backtest(b200[: s0 + H + 1], blev[: s0 + H + 1], CAPITAL, p, start_index=s0, collect_plans=True)
    eq = r.equity
    peak, worst = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); worst = min(worst, v / peak - 1)
    # 첫 매수 체결일(k, 1=시작 다음날), 노출 충족률(실효 노출/목표) 5·20일, t90
    first_fill = None
    first_px_rel = None
    boot_fills, boot_cost = 0, []
    buy_v = buy_q = 0.0     # 20일 안 K200 매수 평균 매입가 (시작일 종가 대비) — 진입가 품질
    for f in r.fills:
        if f.side != "buy":
            continue
        k = idx_of[f.date] - s0
        if first_fill is None:
            first_fill = k
        if f.instrument == "K200" and first_px_rel is None:
            first_px_rel = f.price / close200[s0] - 1      # K200 첫 매입가 vs 시작일 종가 (레버리지 체결은 가격대가 달라 제외)
        if f.instrument == "K200" and k <= 20:
            buy_v += f.price * f.qty; buy_q += f.qty
        if f.kind == "boot":
            boot_fills += 1
            boot_cost.append(f.price / close200[idx_of[f.date] - 1] - 1)   # 체결가 vs 계획일 종가
    boot_orders = sum(1 for pl in r.plans if any(o.kind == "boot" for o in pl.orders))
    ratios = []
    for k in range(min(H, len(eq))):
        i = s0 + k + 1
        eff = (r.qty_200[k] * close200[i] + 2.0 * r.qty_lev[k] * closelev[i]) / eq[k] if eq[k] else 0.0
        tgt = r.plans[k].e_target if k < len(r.plans) else 0.0
        ratios.append(1.0 if tgt <= 0.01 else min(eff / tgt, 1.5))
    t90 = next((k + 1 for k, x in enumerate(ratios) if x >= 0.9), None)
    t50 = next((k + 1 for k, x in enumerate(ratios) if x >= 0.5), None)
    return {"ret": eq[-1] / CAPITAL - 1, "mdd": worst, "ff": first_fill, "r5": ratios[4], "r20": ratios[19], "t50": t50, "t90": t90,
            "boot_orders": boot_orders, "boot_fills": boot_fills, "boot_cost": (st.mean(boot_cost) if boot_cost else None),
            "first_px_rel": first_px_rel, "cost20_rel": (buy_v / buy_q / close200[s0] - 1) if buy_q else None,
            "regime0": r.regimes[0] if r.regimes else None, "year": dates[s0][:4]}


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return xs[int(q * (len(xs) - 1))] if xs else None


BASE_CFG = {"boot_frac": 0.0}
base = [run(BASE_CFG, s0) for s0 in starts]


def evaluate(cfg: dict, label: str) -> dict:
    res = [run(cfg, s0) for s0 in starts]
    diff = [b["ret"] - a["ret"] for a, b in zip(base, res)]
    tot_orders = sum(x["boot_orders"] for x in res); tot_fills = sum(x["boot_fills"] for x in res)
    costs = [x["boot_cost"] for x in res if x["boot_cost"] is not None]
    import random
    random.seed(7)
    means = sorted(st.mean(random.choices(diff, k=len(diff))) for _ in range(1000))
    wins = sum(1 for x in diff if x > 0); losses = sum(1 for x in diff if x < 0)
    from math import comb
    m = wins + losses
    k_ = min(wins, losses)
    p_sign = min(1.0, 2 * sum(comb(m, j) for j in range(0, k_ + 1)) / 2 ** m) if m else 1.0
    c20 = [x["cost20_rel"] for x in res if x["cost20_rel"] is not None]
    c20_pair = [b["cost20_rel"] - a["cost20_rel"] for a, b in zip(base, res) if a["cost20_rel"] is not None and b["cost20_rel"] is not None]
    fpx = [x["first_px_rel"] for x in res if x["first_px_rel"] is not None]
    return {"label": label, "cfg": cfg, "n": len(res), "res": res, "diff": diff,
            "ci_lo": means[25], "ci_hi": means[974], "p_sign": p_sign,
            "cost20": (st.mean(c20) if c20 else None), "cost20_d": (st.mean(c20_pair) if c20_pair else None), "cost20_n": len(c20_pair),
            "first_px": (st.mean(fpx) if fpx else None),
            "d_med": st.median(diff), "d_mean": st.mean(diff), "d_p10": pct(diff, 0.10), "d_min": min(diff), "d_win": sum(1 for x in diff if x > 0),
            "d_tie": sum(1 for x in diff if x == 0),
            "ret_med": med([x["ret"] for x in res]), "ff_med": med([x["ff"] for x in res]), "ff_p90": pct([x["ff"] for x in res], 0.9),
            "nofill5": sum(1 for x in res if x["ff"] is None or x["ff"] > 5),
            "r5": med([x["r5"] for x in res]), "r20": med([x["r20"] for x in res]), "t50": med([x["t50"] for x in res]), "t90": med([x["t90"] for x in res]),
            "fill_rate": (tot_fills / tot_orders) if tot_orders else None, "boot_cost": (st.mean(costs) if costs else None),
            "mdd_med": med([x["mdd"] for x in res])}


def fmt(e: dict) -> str:
    fr = f"{e['fill_rate']:.0%}" if e["fill_rate"] is not None else "  - "
    bc = f"{e['boot_cost']:+.2%}" if e["boot_cost"] is not None else "   -  "
    return (f"{e['label']:<26} 첫체결 {e['ff_med']!s:>3}/{e['ff_p90']!s:>3} 5일내미체결 {e['nofill5']:>3} | t50 {e['t50']!s:>3} t90 {e['t90']!s:>3} 노출5일 {e['r5']:.0%} 20일 {e['r20']:.0%} | "
            f"부트 체결률 {fr} 진입가vs종가 {bc} | Δ250 중앙 {e['d_med']:+.3%} 평균 {e['d_mean']:+.3%}[95%CI {e['ci_lo']:+.2%},{e['ci_hi']:+.2%}] p10 {e['d_p10']:+.2%} 최악 {e['d_min']:+.2%} 우세 {e['d_win']}/{e['n']}(동률 {e['d_tie']}, 부호검정 p={e['p_sign']:.2f}) | "
            f"첫체결가vs시작종가 {e['first_px']:+.2%} · 20일 평균매입가vs시작종가 {e['cost20']:+.2%}(기준 대비 {e['cost20_d']:+.2%}, n={e['cost20_n']}) | MDD중앙 {e['mdd_med']:.1%}")


def breakdown(e: dict, key: str) -> str:
    groups: dict[str, list] = {}
    for x, d in zip(e["res"], e["diff"]):
        groups.setdefault(str(x[key]), []).append((d, x))
    parts = []
    for g in sorted(groups):
        ds = [d for d, _ in groups[g]]
        parts.append(f"{g} n={len(ds)} Δ중앙 {st.median(ds):+.2%} 우세 {sum(1 for d in ds if d > 0)}/{len(ds)} 첫체결 {med([x['ff'] for _, x in groups[g]])}")
    return " · ".join(parts)


print(f"[stage {STAGE}] 창 250일 · 시작 {len(starts)}개 ({dates[starts[0]]} ~ {dates[starts[-1]]}, {STEP}거래일 간격+{OFF}) · 기준: 부트스트랩 없음(그리드만)")
print(f"{'기준(그리드만)':<26} 첫체결 {med([x['ff'] for x in base])!s:>3}/{pct([x['ff'] for x in base], 0.9)!s:>3} 5일내미체결 {sum(1 for x in base if x['ff'] is None or x['ff'] > 5):>3} | t50 {med([x['t50'] for x in base])!s:>3} t90 {med([x['t90'] for x in base])!s:>3} 노출5일 {med([x['r5'] for x in base]):.0%} 20일 {med([x['r20'] for x in base]):.0%} | 250일 수익 중앙 {med([x['ret'] for x in base]):+.2%} MDD중앙 {med([x['mdd'] for x in base]):.1%}")
print("기준 시작 레짐:", {r: sum(1 for x in base if x["regime0"] == r) for r in ("BULL", "NEUTRAL", "BEAR")},
      "· 기준 첫체결가vs시작종가", f"{st.mean([x['first_px_rel'] for x in base if x['first_px_rel'] is not None]):+.2%}",
      "· 기준 20일 평균매입가vs시작종가", f"{st.mean([x['cost20_rel'] for x in base if x['cost20_rel'] is not None]):+.2%} (매수 있는 창 {sum(1 for x in base if x['cost20_rel'] is not None)}/{len(base)})")

CUR = {"boot_frac": 0.15, "boot_days": 10, "boot_bear_mult": 0.5}
results = []
if STAGE == "1":
    for d in (-0.5, -0.25, 0.0, 0.1, 0.25, 0.5, 0.75, 1.0):
        e = evaluate({**CUR, "boot_delta": d}, f"δ{d:+.2f} f0.15 N10 bear0.5"); results.append(e); print(fmt(e), flush=True)
elif STAGE == "2":
    d = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    for f_ in (0.10, 0.15, 0.25, 0.35):
        for N in (5, 10, 20):
            e = evaluate({"boot_frac": f_, "boot_days": N, "boot_bear_mult": 0.5, "boot_delta": d}, f"δ{d:+.2f} f{f_:.2f} N{N} bear0.5"); results.append(e); print(fmt(e), flush=True)
elif STAGE == "3":
    d = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    f_ = float(sys.argv[5]) if len(sys.argv) > 5 else 0.15
    N = int(sys.argv[6]) if len(sys.argv) > 6 else 10
    for bm in (0.0, 0.25, 0.5, 1.0):
        e = evaluate({"boot_frac": f_, "boot_days": N, "boot_bear_mult": bm, "boot_delta": d}, f"δ{d:+.2f} f{f_:.2f} N{N} bear{bm}"); results.append(e); print(fmt(e), flush=True)
elif STAGE == "5":
    cands = [(0.0, 0.15, 10, 0.5), (0.0, 0.15, 5, 0.5), (0.0, 0.15, 5, 0.25), (0.0, 0.15, 10, 0.0), (0.25, 0.15, 10, 0.5), (0.5, 0.15, 5, 0.25), (-0.25, 0.15, 10, 0.5)]
    labels = ["현행 δ0 f.15 N10 bear.5", "δ0 N5", "δ0 N5 bear.25", "δ0 bear0(하락장 없음)", "δ+.25", "δ+.5 N5 bear.25", "δ−.25(시가 근사)"]
    for (d, f_, N, bm), lab in zip(cands, labels):
        e = evaluate({"boot_frac": f_, "boot_days": N, "boot_bear_mult": bm, "boot_delta": d}, lab); results.append(e); print(fmt(e), flush=True)
    print("\n=== 요약표 ===")
    print(f"{'설정':<24} {'체결률':>5} {'5일미체결':>6} {'t50':>4} {'t90':>5} {'Δ중앙':>8} {'Δ평균':>8} {'95%CI':>16} {'우세':>8} {'p':>5} {'p10':>7} {'최악':>7} {'K200첫매입가':>9} {'20일평균매입가':>9}")
    for e in results:
        fr = f"{e['fill_rate']:.0%}" if e['fill_rate'] is not None else "-"
        print(f"{e['label']:<24} {fr:>5} {e['nofill5']:>6} {e['t50']!s:>4} {e['t90']!s:>5} {e['d_med']:>+8.3%} {e['d_mean']:>+8.3%} [{e['ci_lo']:+.2%},{e['ci_hi']:+.2%}] {e['d_win']:>3}/{e['n']:<3} {e['p_sign']:>5.2f} {e['d_p10']:>+7.2%} {e['d_min']:>+7.2%} {e['first_px']:>+9.2%} {e['cost20']:>+9.2%}")
elif STAGE == "4":
    cands = [(-0.25, 0.15, 10, 0.5), (0.0, 0.15, 10, 0.5), (0.25, 0.15, 10, 0.5), (0.5, 0.15, 10, 0.5)]
    if len(sys.argv) > 4:
        cands = [tuple(float(x) if j != 2 else int(float(x)) for j, x in enumerate(c.split(","))) for c in sys.argv[4].split(";")]
    for d, f_, N, bm in cands:
        e = evaluate({"boot_frac": f_, "boot_days": N, "boot_bear_mult": bm, "boot_delta": d}, f"δ{d:+.2f} f{f_:.2f} N{N} bear{bm}"); results.append(e)
        print(fmt(e)); print("   연도별:", breakdown(e, "year")); print("   레짐별:", breakdown(e, "regime0"), flush=True)
if results and STAGE in ("1", "2", "3"):
    print("\n=== Δ250 중앙 높은 순 ===")
    for e in sorted(results, key=lambda e: -e["d_med"]):
        print(f"  {e['label']:<26} Δ중앙 {e['d_med']:+.3%} 평균 {e['d_mean']:+.3%} 우세 {e['d_win']}/{e['n']} 첫체결 {e['ff_med']} t90 {e['t90']} 체결률 {e['fill_rate'] and f'{e['fill_rate']:.0%}'}")
