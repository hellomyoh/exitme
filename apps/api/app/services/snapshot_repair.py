"""과거 날짜 스냅샷 보정 — 원장을 그 날짜 기준(as-of)으로 재생해 포트 스냅샷(주식·현금)과 사용자 스냅샷 총액을 다시 계산한다.

배경 (2026-09-09): 거래 등록 경로가 flush 없이 당일 스냅샷을 재계산해 "로트는 매도 후, 현금은 매도 전" 반쪽 값이 저장됐다
(09-08 M-신한-ETF, 매도 대금 3,420,000원 누락 → 다음 날 '전일 대비'가 그만큼 부풀었다). 코드는 flush 로 고쳤고, 이미 저장된
날짜는 이 도구로 되돌린다. 매매일지(journal)·기타 자산(other)은 저장값을 그대로 두고 국내 포트의 주식·현금만 다시 계산한다.

    docker compose exec -T api python -m app.services.snapshot_repair --date 2026-09-08            # dry-run: 전/후 출력만
    docker compose exec -T api python -m app.services.snapshot_repair --date 2026-09-08 --apply    # 저장
    옵션 --user <id|email> 로 한 사용자만.

as-of 규칙: 그 날짜에 체결(executed_at)된 거래까지 포함한 FIFO 로트·현금(`signals._state_before`, 등록 경로와 같은 회계),
시세는 그 날짜 이하의 마지막 종가(없으면 취득가). 미국 포트는 다루지 않는다(센트 단위·환산 별도).
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AssetSnapshot, Instrument, OhlcvDaily, PortfolioSnapshot, TradePortfolio, User

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


def _close_asof(session: Session, instrument_id: int, day: date) -> float | None:
    row = session.scalar(select(OhlcvDaily).where(OhlcvDaily.instrument_id == instrument_id, OhlcvDaily.trade_date <= day)
                         .order_by(OhlcvDaily.trade_date.desc()).limit(1))
    return float(row.close_raw) * float(row.adj_factor) if row is not None else None


def portfolio_state_asof(session: Session, pid: int, day: date) -> tuple[int, int]:
    """(stock_value, cash) — day 에 체결된 거래까지 반영한 로트를 day 이하 마지막 종가로 평가. 시세 없으면 취득가."""
    from app.signals import _state_before

    lots, cash = _state_before(session, pid, datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=KST))
    stock = 0.0
    for lot in lots:
        px = _close_asof(session, lot["instrument_id"], day)
        stock += lot["qty"] * (px if px is not None else lot["price"])
    return round(stock), int(cash)


def recompute_snapshots_asof(session: Session, user_id: int, day: date, apply: bool = False) -> dict:
    """사용자의 국내 포트 스냅샷(day)을 as-of 원장으로 다시 계산하고 사용자 스냅샷 총액을 갱신한다. apply=False 면 계산만.

    반환 {"date", "user_id", "portfolios": [{name, before{stock,cash}, after{stock,cash}, diff}], "asset": {before_total, after_total}, "applied"}.
    """
    pfs = session.scalars(select(TradePortfolio).where(TradePortfolio.user_id == user_id, TradePortfolio.market == "KR")
                          .order_by(TradePortfolio.id)).all()
    out: dict = {"date": day.isoformat(), "user_id": user_id, "portfolios": [], "asset": None, "applied": False}
    kr_stock = kr_cash = 0
    for pf in pfs:
        stock, cash = portfolio_state_asof(session, pf.id, day)
        row = session.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == pf.id, PortfolioSnapshot.snap_date == day))
        before = {"stock": int(row.stock_value), "cash": int(row.cash)} if row is not None else None
        out["portfolios"].append({"portfolio_id": pf.id, "name": pf.name, "before": before, "after": {"stock": stock, "cash": cash},
                                  "diff": (stock + cash - (before["stock"] + before["cash"])) if before else None})
        kr_stock += stock
        kr_cash += cash
        if apply:
            if row is None:
                session.add(PortfolioSnapshot(portfolio_id=pf.id, snap_date=day, equity=stock + cash, stock_value=stock, cash=cash, currency="KRW"))
            else:
                row.equity, row.stock_value, row.cash = stock + cash, stock, cash
    snap = session.scalar(select(AssetSnapshot).where(AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date == day))
    if snap is not None:
        other, journal = int(snap.other or 0), int(snap.journal or 0)
        after_total = kr_stock + kr_cash + other + journal
        out["asset"] = {"before": {"total": int(snap.total), "stock": int(snap.stock), "cash": int(snap.cash), "other": other, "journal": journal},
                        "after": {"total": after_total, "stock": kr_stock, "cash": kr_cash, "other": other, "journal": journal},
                        "diff": after_total - int(snap.total)}
        if apply:
            snap.stock, snap.cash, snap.total = kr_stock, kr_cash, after_total
    if apply:
        session.flush()
        out["applied"] = True
    return out


def _fmt(res: dict) -> str:
    lines = [f"[user {res['user_id']}] {res['date']} — {'적용' if res['applied'] else 'dry-run'}"]
    for p in res["portfolios"]:
        b = p["before"]
        lines.append(f"  {p['name']}: " + (f"저장 stock={b['stock']:,} cash={b['cash']:,} → " if b else "저장 없음 → ")
                     + f"as-of stock={p['after']['stock']:,} cash={p['after']['cash']:,}" + (f"  (차이 {p['diff']:+,})" if p["diff"] is not None else ""))
    a = res["asset"]
    if a:
        lines.append(f"  사용자 총액: {a['before']['total']:,} → {a['after']['total']:,} (차이 {a['diff']:+,}; other {a['after']['other']:,}·journal {a['after']['journal']:,} 유지)")
    else:
        lines.append("  사용자 스냅샷 없음 — 포트 스냅샷만 계산")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from app.db import SessionLocal

    p = argparse.ArgumentParser(description="과거 날짜 스냅샷을 as-of 원장으로 보정 (기본 dry-run)")
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--user", default=None, help="사용자 id 또는 이메일 (생략 = 전체)")
    p.add_argument("--apply", action="store_true", help="저장 (없으면 계산 결과만 출력)")
    a = p.parse_args(argv)
    day = date.fromisoformat(a.date)
    with SessionLocal() as s:
        q = select(User)
        if a.user:
            q = q.where(User.id == int(a.user)) if a.user.isdigit() else q.where(User.email == a.user)
        users = s.scalars(q.order_by(User.id)).all()
        if not users:
            print("사용자 없음"); return 1
        for u in users:
            print(_fmt(recompute_snapshots_asof(s, u.id, day, apply=a.apply)))
        if a.apply:
            s.commit()
            print("저장 완료")
        else:
            print("dry-run — 저장하지 않았습니다 (--apply 로 적용)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
