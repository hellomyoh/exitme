"""과거 매수 거래에 전략 태그 소급 (0027 이전 기록, 2026-09-13 지시).

0027 이 `trade_transactions.lot_kind/tp_price` 를 만들기 전에 쌓인 행은 태그가 비어 있다.
태그 없는 행은 `lots.rebuild_lots` 의 **종전 근사**로 떨어진다:
  · K200 → 지금 상승장이면 core, 아니면 grid + 익절가 = **최근 종가**×(1+오늘 Grid)  ← 체결가와 무관해서
    감사(docs/kodex-linkage-audit-20260912.md)가 "그리드 체결의 22%가 원가 아래 익절가"로 잡은 바로 그 경로.
  · 레버리지 → 전부 `lev_strat` → 전술 로트를 구분할 수 없어 **전술 이탈 매도가 나가지 않는다**.
즉 감사 수정(0027)은 새 체결에만 적용되고, 이미 들고 있는 물량은 옛 경로로 남아 있다.

이 스크립트가 채우는 값 (백테스트 체결 블록·`lots.lot_tag` 와 같은 규칙):
  · **증권사 주문 기록이 있으면** 그 주문의 종류로 정확히 태그한다(우리가 낸 주문).
  · **없으면**(수동 등록·HTS 직접 주문·보유분 입력) 체결일의 **계획일 레짐·Grid** 를 재생해 K200 을 태그한다 —
    상승장 계획이면 core(익절 없음), 아니면 grid + 익절가 = **체결가**×(1+계획일 Grid).
  · 레버리지는 `lev_strat` 로 명시한다. 수동 로트에는 전술 진입의 근거가 없고, 전략으로 두는 쪽이
    현재 폴백과 같은 동작이라 이 스크립트로 매매가 바뀌지 않는다(추측으로 전술 태그를 달면 의도치 않은 매도가 난다).
  · **이미 지나간 익절가는 붙이지 않는다** (2026-09-13): 재생한 익절가가 최근 종가 이하면 그 태그는
    "다음 계획에서 전량 시장가 매도"를 뜻한다(지정가 매도는 시장가 위에 있으면 즉시 체결). 메타데이터 소급이
    청산을 일으켜서는 안 되므로 그런 로트는 `core`(익절가 없음)로 둔다 — 태그 전 폴백과 같은 동작이다.
    2026-08-28·09-03 검토가 지적한 "평단 역계산 → 즉시 전량 매도" 함정과 같은 것이다.
  · 매도 행은 증권사 주문 기록이 있을 때만 태그한다. 없으면 종전대로 순수 FIFO 로 소진된다.

사용:
  docker compose exec -T api python -m scripts.retag_legacy_lots --dry-run
  docker compose exec -T api python -m scripts.retag_legacy_lots --portfolio 3
  docker compose exec -T api python -m scripts.retag_legacy_lots            # 실제 기록
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.lots import lot_tag, sell_tag
from app.models import BrokerOrder, Instrument, TradePortfolio, TradeTransaction

KST = timezone(timedelta(hours=9))
LEV_CODES = {"122630", "QLD", "TQQQ"}
K200_STANDIN = "grid1"   # K200 매수 주문 종류는 모두 같은 규칙 — lot_tag 에 넘길 대표값


def _kdate(dt: datetime):
    return (dt.astimezone(KST) if dt.tzinfo else dt.replace(tzinfo=KST)).date()


def retag_portfolio(session, pf: TradePortfolio, dry_run: bool) -> dict:
    """포트 하나의 태그 없는 거래를 채운다. 반환: 건수·표본."""
    from app.lots import plan_day_context
    from app.signals import market_context

    stat = {"portfolio_id": pf.id, "name": pf.name, "market": pf.market,
            "buys": 0, "tagged_order": 0, "tagged_replay": 0, "lev": 0, "sells": 0,
            "no_bar": 0, "unsupported": 0, "passed_tp": 0, "samples": []}
    txs = session.scalars(select(TradeTransaction)
                          .where(TradeTransaction.portfolio_id == pf.id,
                                 TradeTransaction.kind.in_(("buy", "sell")))
                          .order_by(TradeTransaction.executed_at, TradeTransaction.id)).all()
    todo = [t for t in txs if t.lot_kind is None]
    if not todo:
        return stat

    ctx = market_context(session, pf, pf.user_id)
    params = ctx["params"]
    last_close = float(ctx["m200"].closes[-1])     # 이미 지나간 익절가 판정 기준
    dates = [b["date"] for b in ctx["bars_200"]]
    reg_by_date = dict(zip(ctx["result"].dates, ctx["result"].regimes))
    code_of = {}

    for t in todo:
        if t.instrument_id is None:
            continue
        code = code_of.get(t.instrument_id) or (code_of.setdefault(t.instrument_id, session.get(Instrument, t.instrument_id).code))
        if code not in set(ctx["codes"]):
            stat["unsupported"] += 1          # 이 포트의 페어가 아닌 종목 — 주문표도 계산하지 못하는 상태라 건드리지 않는다
            continue
        bo = None
        if t.broker_ref:                       # 증권사 자동 가져오기 행 — 주문번호로 우리가 낸 주문을 찾는다
            order_no = str(t.broker_ref).split(":")[0]
            bo = session.scalar(select(BrokerOrder).where(BrokerOrder.portfolio_id == pf.id,
                                                          BrokerOrder.order_no == order_no))
        if t.kind == "sell":
            if bo is None:
                continue                       # 귀속 근거 없음 → 종전 FIFO 유지
            kind, tp = sell_tag(bo.kind, bo.price)
            if kind is None:
                continue
            stat["sells"] += 1
        elif code in LEV_CODES:
            kind, tp = (bo.kind if bo is not None and bo.kind in ("lev_strat", "lev_tact1", "lev_tact2") else "lev_strat"), None
            stat["lev"] += 1
            stat["buys"] += 1
        else:
            day = _kdate(t.executed_at)
            regime = grid = None
            if bo is not None and bo.plan_regime:
                regime, grid = bo.plan_regime, (float(bo.plan_grid) if bo.plan_grid is not None else None)
                stat["tagged_order"] += 1
            else:
                regime, grid = plan_day_context(dates, reg_by_date, ctx["m200"], params, day)
                if regime is None:
                    stat["no_bar"] += 1        # 체결일 봉이 없다(휴장일 등록·시세 미적재) → 태그 없이 둔다
                    continue
                stat["tagged_replay"] += 1
            kind, tp = lot_tag(K200_STANDIN, "buy", regime, grid, t.price, params)
            if kind is None:
                stat["no_bar"] += 1
                continue
            if kind == "grid" and tp is not None and tp <= last_close:
                # 이미 지나간 익절가 → 붙이면 다음 계획에서 전량 매도가 된다. 익절 없는 core 로 (폴백과 동일)
                kind, tp = "core", None
                stat["passed_tp"] += 1
            stat["buys"] += 1
            if len(stat["samples"]) < 5:
                stat["samples"].append({"date": day.isoformat(), "code": code, "qty": t.qty, "price": t.price,
                                        "regime": regime, "kind": kind, "tp_price": tp,
                                        "tp_vs_cost": (round(tp / t.price - 1, 4) if tp else None)})
        if not dry_run:
            t.lot_kind, t.tp_price = kind, tp
    return stat


def run(portfolio_id: int | None = None, dry_run: bool = False) -> dict:
    out: dict = {"portfolios": [], "totals": {"buys": 0, "sells": 0, "tagged_order": 0, "tagged_replay": 0,
                                              "lev": 0, "no_bar": 0, "unsupported": 0, "passed_tp": 0}}
    with SessionLocal() as s:
        q = select(TradePortfolio).order_by(TradePortfolio.id)
        if portfolio_id:
            q = q.where(TradePortfolio.id == portfolio_id)
        for pf in s.scalars(q).all():
            try:
                st = retag_portfolio(s, pf, dry_run)
            except Exception as exc:  # noqa: BLE001 — 포트 하나가 막혀도 나머지는 진행 (예: 대상 외 종목 보유)
                out["portfolios"].append({"portfolio_id": pf.id, "name": pf.name, "error": str(exc)[:160]})
                s.rollback()
                continue
            if st["buys"] or st["sells"] or st["no_bar"] or st["unsupported"] or st["passed_tp"]:
                out["portfolios"].append(st)
                for k in out["totals"]:
                    out["totals"][k] += st[k]
        if not dry_run:
            s.commit()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="0027 이전 거래에 전략 태그 소급")
    ap.add_argument("--portfolio", type=int, default=None, help="이 포트만")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 건수만")
    a = ap.parse_args()
    res = run(a.portfolio, a.dry_run)
    head = "[dry-run] " if a.dry_run else ""
    print(head + " · ".join(f"{k}={v}" for k, v in res["totals"].items()))
    for p in res["portfolios"]:
        if "error" in p:
            print(f"  #{p['portfolio_id']} {p['name']}: 오류 — {p['error']}")
            continue
        print(f"  #{p['portfolio_id']} {p['name']} ({p['market']}): 매수 {p['buys']}"
              f"(주문기록 {p['tagged_order']} · 재생 {p['tagged_replay']} · 레버리지 {p['lev']})"
              f" · 매도 {p['sells']} · 봉없음 {p['no_bar']} · 대상외 {p['unsupported']}"
              f" · 익절가 지나감→core {p['passed_tp']}")
        for smp in p["samples"]:
            tp = f"{smp['tp_price']:,}원({smp['tp_vs_cost']:+.2%})" if smp["tp_price"] else "익절없음"
            print(f"      {smp['date']} {smp['code']} {smp['qty']}주 @{smp['price']:,} · {smp['regime']} → {smp['kind']} · {tp}")
