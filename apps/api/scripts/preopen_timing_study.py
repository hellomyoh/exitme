"""09:01 발주 vs 08:30 동시호가 참여 — 분봉으로 체결 차이를 직접 잰다 (2026-09-15 사용자 질문).

질문: "09:01 에 주문 넣지 말고 08:30~09:00 사이에 넣는 방안" · "09:00 폭락 시 장 시작 전 예상가로 판단 가능한가".

현행(A, ADR-009): 09:00 실제 시가를 보고 **갭 취소**(시가 ≤ 종가−1.5×ATR 이면 그리드 매수 생략)를 판정한 뒤 09:01 에 발주한다.
  → 09:00 단일가와 09:00~09:01 구간에는 우리 주문이 **없다**.
제안(B): 08:30~09:00 동시호가에 지정가를 넣는다. 09:00 단일가에 참여하므로 단일가 ≤ 지정가면 **단일가에** 체결된다.
  → 시가를 보기 전에 넣으므로 **갭 취소를 적용할 수 없다**.

여기서 재는 것:
  ① B 만 얻는 체결(09:01 이후로는 다시 닿지 않는 날) 이 얼마나 되나 — 제안의 이득
  ② 그 체결의 사후 성과(당일 종가·5일 뒤) — 이득이 실제로 돈이 되나
  ③ 갭 취소를 잃는 비용 — 갭일에 B 가 사게 되는 수량과 그 사후 성과
  ④ 백테스트의 체결 가정이 A 와 B 중 어느 쪽인가 (일봉 low ≤ 지정가 = 하루 종일 주문이 살아 있다는 가정)

실행: docker compose exec -T api python -m scripts.preopen_timing_study
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import mean, median

from sqlalchemy import select

from app.backtests import load_aligned_bars
from app.db import SessionLocal
from app.models import Instrument, OhlcvIntraday
from app.strategy.backtest import run_backtest
from app.strategy.params import Params

KST = timezone(timedelta(hours=9))
CODE_200 = "069500"
CAP = 100_000_000.0


def load_minutes(session, code: str) -> dict[str, list[tuple[datetime, int, int, int, int]]]:
    """거래일(ISO 문자열 — 일봉의 date 표기와 맞춘다) → [(KST 시각, o, h, l, c)] 시간순. 09:00 봉이 첫 원소."""
    inst = session.scalar(select(Instrument).where(Instrument.code == code))
    rows = session.execute(
        select(OhlcvIntraday.ts, OhlcvIntraday.open_raw, OhlcvIntraday.high_raw,
               OhlcvIntraday.low_raw, OhlcvIntraday.close_raw)
        .where(OhlcvIntraday.instrument_id == inst.id)
        .order_by(OhlcvIntraday.ts)
    ).all()
    out: dict[str, list] = defaultdict(list)
    for ts, o, h, l, c in rows:
        k = ts.astimezone(KST)
        out[k.date().isoformat()].append((k, int(o), int(h), int(l), int(c)))
    return dict(out)


def main() -> int:
    with SessionLocal() as s:
        mins = load_minutes(s, CODE_200)
        days = sorted(mins)
        print(f"분봉: {len(days)}일 {days[0]} ~ {days[-1]} (일 평균 {mean(len(mins[d]) for d in days):.0f}봉)")
        # 일봉은 워밍업 포함해 넉넉히
        b200, blev, _fp = load_aligned_bars(s, date(2024, 1, 2), date(2026, 12, 31))

    params = Params()
    res = run_backtest(b200, blev, CAP, params, collect_plans=True)
    dates = [b["date"] for b in b200]
    closes = {b["date"]: float(b["close"]) for b in b200}

    # plan[j] 는 bars 인덱스 first+j 에서 계산되어 다음 날(+1)에 실행된다
    first = len(dates) - 1 - len(res.plans)
    rows = []
    for j, p in enumerate(res.plans):
        i = first + j
        if i + 1 >= len(dates):
            break
        exec_day = dates[i + 1]
        if exec_day not in mins:
            continue
        if getattr(p, "status", None) != "OK":
            continue
        buys = [o for o in p.orders if o.side == "buy" and o.otype == "limit" and o.price
                and (o.kind.startswith("grid") or o.kind == "boot")]
        if not buys:
            continue
        bars = mins[exec_day]
        auction = bars[0][1]                       # 09:00 봉의 시가 = 단일가
        first_low = bars[0][3]                     # 09:00~09:00:59
        rest = [b for b in bars[1:]]
        rest_low = min((b[3] for b in rest), default=None)
        if rest_low is None:
            continue
        gap_exact = p.gap_cancel_exact
        gap_hit = bool(gap_exact and auction <= float(gap_exact))
        rows.append({"day": exec_day, "auction": auction, "first_low": first_low, "rest_low": rest_low,
                     "gap": gap_hit, "buys": [(o.kind, int(o.qty), int(o.price)) for o in buys],
                     "close": closes[exec_day], "prev_close": closes[dates[i]]})

    print(f"평가 대상: 그리드 매수가 있는 {len(rows)}일 (갭일 {sum(r['gap'] for r in rows)}일)")

    fwd = {}
    for n, d in enumerate(dates):
        fwd[d] = closes[dates[min(n + 5, len(dates) - 1)]]

    stat = defaultdict(int)
    b_only, gap_buys, price_gap = [], [], []
    for r in rows:
        bars = mins[r["day"]]
        open_0901 = bars[1][1] if len(bars) > 1 else None      # 09:01 봉 시가 = 실행기가 발주하는 순간의 값
        for kind, qty, px in r["buys"]:
            stat["lines"] += 1
            a_fill = (not r["gap"]) and r["rest_low"] <= px
            b_auction = r["auction"] <= px
            b_fill = b_auction or r["first_low"] <= px or r["rest_low"] <= px
            stat["A_fill"] += a_fill
            stat["B_fill"] += b_fill
            if b_fill and not a_fill:
                stat["B_only"] += 1
                rec = {"day": r["day"], "qty": qty, "px": min(r["auction"], px) if b_auction else px,
                       "limit": px, "gap": r["gap"], "close": r["close"], "fwd5": fwd[r["day"]]}
                b_only.append(rec)
                if r["gap"]:
                    gap_buys.append(rec)
            if a_fill and not b_fill:
                stat["A_only"] += 1
            # ② 둘 다 체결되는 비갭일의 **가격 차이** — B 는 09:00 단일가, A 는 09:01 시점 시장가(없으면 지정가)
            if a_fill and b_auction and not r["gap"] and open_0901:
                b_px = r["auction"]
                a_px = open_0901 if open_0901 <= px else px
                price_gap.append({"day": r["day"], "limit": px, "b": b_px, "a": a_px,
                                  "diff": (a_px - b_px) / b_px})

    print(f"\n[체결 줄 수] 전체 {stat['lines']} · 현행 A {stat['A_fill']} · 제안 B {stat['B_fill']}"
          f" · B만 {stat['B_only']} · A만 {stat['A_only']}")
    print(f"  └ B만 {stat['B_only']}건 중 갭일 {len(gap_buys)}건 · 비갭일 {stat['B_only'] - len(gap_buys)}건")

    if price_gap:
        d = [x["diff"] for x in price_gap]
        worse = sum(1 for v in d if v > 0)
        print(f"\n[② 비갭일 가격 차이] 둘 다 체결되지만 단일가가 지정가 아래로 열린 {len(price_gap)}줄")
        print(f"  A(09:01) 가 B(단일가) 보다 평균 {mean(d):+.3%} · 중앙 {median(d):+.3%} 비싸게 산다 "
              f"(A 가 더 비싼 날 {worse}/{len(price_gap)})")

    def outcome(items, label):
        if not items:
            print(f"[{label}] 없음")
            return
        d1 = [(x["close"] / x["px"] - 1) for x in items]
        d5 = [(x["fwd5"] / x["px"] - 1) for x in items]
        won = sum(1 for v in d5 if v > 0)
        print(f"[{label}] {len(items)}건 · 당일 종가 대비 평균 {mean(d1):+.3%} · "
              f"5일 뒤 평균 {mean(d5):+.3%} (이익 {won}/{len(items)})")

    print(f"\n[③ 갭일 — 갭 취소를 잃으면 사게 되는 것]  갭일 {sum(r['gap'] for r in rows)}일")
    outcome(gap_buys, "갭일 체결의 사후")
    outcome([x for x in b_only if not x["gap"]], "비갭일 B만 체결")

    print("\n[④ 백테스트가 가정하는 것] `_fill_limit_buy`: open ≤ 지정가면 **open 에** 체결 → "
          "주문이 09:00 단일가부터 살아 있다는 가정 = B. 갭 취소는 실제 시가로 판정 = A. "
          "즉 현행 백테스트는 A·B 의 좋은 쪽만 합친 혼합이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
