"""갭 필터 **단독** 절제 — 09:01 발주를 08:30 동시호가로 옮기면 잃는 것의 값 (2026-09-15).

`preopen_timing_study` 결과: 동시호가 참여(B)와 09:01 발주(A)의 차이는 **갭일에만** 생긴다
(비갭일 체결 59=59 동일, 가격은 A 가 0.183% 더 쌈). 그래서 제안의 비용은 곧 **갭 취소를 잃는 비용**이다.

기존 절제 리포트(2026-08-28)의 ⑤ 는 `f5_gap_filter` 를 통째로 꺼서 **갭 필터 + 잔여예산**을 함께 껐다.
여기서는 `gap_atr_mult` 만 거대하게 만들어(임계가 −∞) **갭 취소만** 무력화하고 잔여예산은 그대로 둔다.

시작일을 21거래일 간격으로 쓸어 각 1년(246거래일)을 짝으로 비교한다 — 단일 경로의 착시를 피한다
(docs/validation-methodology-20260913.md).

실행: docker compose exec -T api python -m scripts.gap_filter_ablation
"""
from __future__ import annotations

import sys
from datetime import date
from statistics import mean, median

from app.backtests import load_aligned_bars
from app.db import SessionLocal
from app.strategy.backtest import run_backtest
from app.strategy.params import Params

CAP, WARM, HOR, STEP = 100_000_000.0, 260, 246, 21


def mdd_of(eq):
    p, w = eq[0], 0.0
    for v in eq:
        p = max(p, v)
        w = min(w, v / p - 1)
    return w


def run(b200, blev, params, i0, i1):
    r = run_backtest(b200[:i1], blev[:i1], CAP, params, start_index=i0)
    eq = r.equity
    return (eq[-1] / eq[0] - 1) * 100.0, mdd_of(eq) * 100.0


def main() -> int:
    with SessionLocal() as s:
        b200, blev, fp = load_aligned_bars(s, date(2015, 1, 2), date(2026, 12, 31))
    print(f"일봉 {len(b200)}일 {b200[0]['date']} ~ {b200[-1]['date']} · fingerprint {fp[:12]}")

    on = Params()                       # 현행 — 갭 취소 있음 (= 09:01 발주)
    off = Params(gap_atr_mult=1e9)      # 제안 — 갭 취소 없음 (= 08:30 동시호가 참여), 잔여예산은 유지

    starts = list(range(WARM, len(b200) - HOR, STEP))
    diffs, mdiffs, win, lose, tie = [], [], 0, 0, 0
    for i0 in starts:
        i1 = i0 + HOR
        r_on, m_on = run(b200, blev, on, i0, i1)
        r_off, m_off = run(b200, blev, off, i0, i1)
        d = r_off - r_on                 # 제안 − 현행
        diffs.append(d)
        mdiffs.append(m_off - m_on)
        if abs(d) < 1e-9:
            tie += 1
        elif d > 0:
            win += 1
        else:
            lose += 1

    print(f"\n창 {len(starts)}개 (각 {HOR}거래일) · 시작일 {STEP}일 간격")
    print(f"[수익률 차이  제안(갭취소 없음) − 현행]  평균 {mean(diffs):+.3f}%p · 중앙 {median(diffs):+.3f}%p")
    print(f"                                        최악 {min(diffs):+.3f}%p · 최고 {max(diffs):+.3f}%p")
    print(f"                                        제안이 이김 {win} : 짐 {lose} : 동일 {tie}")
    print(f"[MDD 차이]                              평균 {mean(mdiffs):+.3f}%p · 최악 {min(mdiffs):+.3f}%p")
    worst = sorted(range(len(diffs)), key=lambda j: diffs[j])[:5]
    print("\n가장 나빴던 5개 창 (제안이 진 폭):")
    for j in worst:
        print(f"  {b200[starts[j]]['date']} ~ {b200[starts[j]+HOR-1]['date']}  {diffs[j]:+.2f}%p  MDD {mdiffs[j]:+.2f}%p")
    return 0


if __name__ == "__main__":
    sys.exit(main())
