"""제출된 결함 보고 2건 재현 (2026-09-15 지시 "분석하고 검토하세요").

① 주문표 레짐이 하루 늦다 — 응답·스냅샷·발주 행의 `plan_regime` 은 플래너 **입력**(기준일 전날 종가로 정한 값)인데,
   주문은 플래너가 기준일 종가로 새로 정한 레짐으로 만들어진다. 체결 태그가 백테스트와 갈린다.
② 시뮬레이터가 중간 날짜에서 시작하면 레짐 상태가 워밍업되지 않는다 — 지표는 이전 봉으로 데우지만
   레짐 상태 기계는 시작일에 NEUTRAL 에서 새로 출발한다(`backtest.py:164`).

실행: docker compose exec -T api python -m scripts.regime_timing_audit
"""
from __future__ import annotations

import sys
from datetime import date
from statistics import median

import app.strategy.backtest as bt
from app.backtests import load_aligned_bars
from app.db import SessionLocal
from app.strategy.backtest import run_backtest
from app.strategy.params import Params
from app.strategy.regime import Regime

CAP, WARM, HOR, STEP = 100_000_000.0, 260, 246, 20


class _RegimeShim:
    """`backtest.py:164` 의 `regime = Regime.NEUTRAL` 만 바꿔치기 — 워밍된 상태로 출발시킨다(측정용)."""

    def __init__(self, start: Regime):
        self.NEUTRAL = start
        self.BULL, self.BEAR = Regime.BULL, Regime.BEAR


def main() -> int:
    with SessionLocal() as s:
        b200, blev, fp = load_aligned_bars(s, date(2015, 1, 2), date(2026, 12, 31))
    params = Params()
    dates = [b["date"] for b in b200]
    print(f"일봉 {len(b200)}일 {dates[0]} ~ {dates[-1]} · fingerprint {fp[:12]}")

    # ── ① 표시 레짐 vs 주문 레짐 ─────────────────────────────────────────────
    res = run_backtest(b200, blev, CAP, params, collect_plans=True)
    first = len(dates) - 1 - len(res.plans)
    shown, ordered, exec_days = [], [], []
    cur = Regime.NEUTRAL
    for j, p in enumerate(res.plans):
        i = first + j
        if i + 1 >= len(dates):
            break
        if p.status != "OK":
            cur = Regime.NEUTRAL
            continue
        shown.append(cur)          # 화면·스냅샷·plan_regime 이 싣는 값 (플래너 입력)
        ordered.append(p.regime)   # 주문을 만든 값 = 백테스트가 태그에 쓰는 값
        exec_days.append(dates[i + 1])
        cur = p.regime

    trans = sum(1 for a, b in zip(shown, ordered) if a is not b)
    print(f"\n[①] 평가일 {len(shown)}일 · 레짐 전환 {trans}회 · 주문표가 백테스트와 다른 날 **{trans}일**")

    # 태그가 실제로 뒤집히는 날 = BULL 여부가 갈리는 날 (lot_tag 는 BULL 만 본다)
    flip_days = {d for a, b, d in zip(shown, ordered, exec_days) if (a is Regime.BULL) != (b is Regime.BULL)}
    k200_fills = [f for f in res.fills if f.instrument == bt.K200 and f.side == "buy"]
    bad = [f for f in k200_fills if f.date in flip_days]
    print(f"     태그가 뒤집히는 날 {len(flip_days)}일 · K200 매수 체결 {len(k200_fills)}건 중 **{len(bad)}건** 오분류")
    for f in bad[:8]:
        print(f"       {f.date}  {f.kind} {f.qty}주 @{f.price:,}")

    # ── ② 시뮬레이터 워밍업 ──────────────────────────────────────────────────
    reg_by_date = dict(zip(res.dates, res.regimes))
    starts = [i for i in range(WARM, len(b200) - HOR, STEP)]
    diff_first, conv, ret_diff = [], [], []
    for i0 in starts:
        i1 = i0 + HOR
        cold = run_backtest(b200[:i1], blev[:i1], CAP, params, start_index=i0)
        truth = [reg_by_date.get(d) for d in cold.dates]
        if truth[0] is None:
            continue
        if cold.regimes[0] != truth[0]:
            diff_first.append((cold.dates[0], cold.regimes[0], truth[0]))
            n = next((k for k in range(len(cold.regimes)) if cold.regimes[k] == truth[k]), None)
            conv.append(n if n is not None else len(cold.regimes))
            # 워밍된 상태로 출발시킨 대조군
            bt.Regime = _RegimeShim(Regime(truth[0]))
            try:
                warm = run_backtest(b200[:i1], blev[:i1], CAP, params, start_index=i0)
            finally:
                bt.Regime = Regime
            r_cold = cold.equity[-1] / cold.equity[0] - 1
            r_warm = warm.equity[-1] / warm.equity[0] - 1
            ret_diff.append((r_cold - r_warm) * 100.0)

    print(f"\n[②] 시작점 {len(starts)}개 (20거래일 간격) · 첫날 레짐이 전체 이력과 다른 시작점 "
          f"**{len(diff_first)}개 ({len(diff_first) / len(starts):.0%})**")
    if conv:
        print(f"     같아질 때까지 중앙 **{median(conv):.0f}거래일** · 최대 **{max(conv)}거래일**")
    if ret_diff:
        print(f"     1년 수익률 차이 (현행 콜드 − 워밍) 중앙 **{median(ret_diff):+.1f}%p** · "
              f"범위 **{min(ret_diff):+.1f} ~ {max(ret_diff):+.1f}%p**")
    for d, got, want in diff_first[:6]:
        print(f"       {d}  시뮬 {got} vs 전체이력 {want}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
