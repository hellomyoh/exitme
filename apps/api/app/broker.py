"""증권사 조회 연동 (2026-09-05 지시) — 체결 자동 가져오기 + 주문표 대조 경고.

원칙:
- **조회 전용**: 주문 TR 은 사용하지 않는다(자동 발주 미도입 — docs/market-research/10-broker-apis.md).
- **자동 수정 금지**: 대조 결과는 경고로만 노출하고 원장을 임의로 고치지 않는다(사용자 지시).
- 자격(앱키·시크릿·계좌번호)은 앱 레벨 AES-GCM 암호화 컬럼에 저장하고 응답에는 마스킹만 내보낸다.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import BrokerCredential, Instrument, TradeTransaction

logger = logging.getLogger(__name__)
router = APIRouter()
KST = timezone(timedelta(hours=9))


def _owned(session: Session, pid: int, user_id: int):
    from app.models import TradePortfolio

    pf = session.get(TradePortfolio, pid)
    if pf is None or pf.user_id != user_id:
        raise HTTPException(status_code=404, detail="portfolio not found")
    return pf


def _cred(session: Session, pid: int, user_id: int) -> BrokerCredential:
    _owned(session, pid, user_id)
    row = session.scalar(select(BrokerCredential).where(BrokerCredential.portfolio_id == pid))
    if row is None:
        raise HTTPException(status_code=409, detail="증권사 연동이 설정되지 않았습니다")
    return row


def _mask(s: str) -> str:
    return s[:4] + "****" + s[-2:] if len(s) > 8 else "****"


class BrokerIn(BaseModel):
    app_key: str = Field(min_length=10, max_length=200)
    app_secret: str = Field(min_length=10, max_length=400)
    account_no: str = Field(min_length=6, max_length=20)     # 종합계좌 8자리
    acnt_prdt_cd: str = Field(default="01", pattern=r"^\d{2}$")
    env: str = Field(default="prod", pattern="^(prod|vps)$")


@router.get("/portfolio/{pid}/broker")
def get_broker(pid: int, user_id: int = Depends(current_user_id),
               session: Session = Depends(get_session)) -> dict:
    _owned(session, pid, user_id)
    row = session.scalar(select(BrokerCredential).where(BrokerCredential.portfolio_id == pid))
    if row is None:
        return {"linked": False}
    return {"linked": True, "env": row.env, "acnt_prdt_cd": row.acnt_prdt_cd,
            "app_key": _mask(row.app_key), "account_no": _mask(row.account_no),
            "last_import_at": row.last_import_at.isoformat() if row.last_import_at else None}


@router.put("/portfolio/{pid}/broker")
def put_broker(pid: int, body: BrokerIn, user_id: int = Depends(current_user_id),
               session: Session = Depends(get_session)) -> dict:
    _owned(session, pid, user_id)
    row = session.scalar(select(BrokerCredential).where(BrokerCredential.portfolio_id == pid))
    if row is None:
        row = BrokerCredential(user_id=user_id, portfolio_id=pid)
        session.add(row)
    row.app_key, row.app_secret = body.app_key.strip(), body.app_secret.strip()
    row.account_no, row.acnt_prdt_cd, row.env = body.account_no.strip(), body.acnt_prdt_cd, body.env
    session.commit()
    return {"linked": True}


@router.delete("/portfolio/{pid}/broker")
def delete_broker(pid: int, user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> dict:
    row = _cred(session, pid, user_id)
    session.delete(row)
    session.commit()
    return {"deleted": True}


def _client(cred: BrokerCredential):
    from app.services.kis_auth import KisAuth
    from app.services.kis_client import KisTradingClient

    auth = KisAuth(cred.app_key, cred.app_secret, cred.env)
    return KisTradingClient(auth, cano=cred.account_no, acnt_prdt_cd=cred.acnt_prdt_cd)


@router.post("/portfolio/{pid}/import-fills")
def import_fills(pid: int, days: int = 7, dry_run: bool = True,
                 user_id: int = Depends(current_user_id),
                 session: Session = Depends(get_session)) -> dict:
    """증권사 체결 내역을 가져와 거래 원장에 등록 (기본은 미리보기).

    멱등: broker_ref = "주문번호:일자" 유니크 — 재실행해도 중복 등록되지 않는다.
    """
    cred = _cred(session, pid, user_id)
    end = datetime.now(KST).date()
    start = end - timedelta(days=max(1, min(days, 90)) - 1)
    try:
        execs = _client(cred).fetch_executions(start, end)
    except Exception as exc:  # noqa: BLE001 — 자격 오류·유량 등 원인을 사용자에게 그대로 전달
        logger.warning("import-fills failed pid=%s: %s", pid, exc)
        raise HTTPException(status_code=502, detail=f"증권사 조회 실패: {str(exc)[:200]}")

    known = {t.broker_ref for t in session.scalars(
        select(TradeTransaction).where(TradeTransaction.portfolio_id == pid,
                                       TradeTransaction.broker_ref.is_not(None))).all()}
    items, added, skipped, unknown = [], 0, 0, []
    for e in execs:
        ref = f"{e.order_no}:{e.trade_date.isoformat()}"
        inst = session.scalar(select(Instrument).where(Instrument.code == e.code))
        row = {"broker_ref": ref, "date": e.trade_date.isoformat(), "code": e.code,
               "name": inst.name if inst else e.name, "side": e.side,
               "qty": e.filled_qty, "price": e.avg_price,
               "amount": e.filled_qty * e.avg_price}
        if ref in known:
            row["status"] = "이미 등록됨"
            skipped += 1
        elif inst is None:
            row["status"] = "미시딩 종목 — 건너뜀"
            unknown.append(e.code)
        else:
            row["status"] = "등록 예정" if dry_run else "등록됨"
            if not dry_run:
                session.add(TradeTransaction(
                    portfolio_id=pid, kind=e.side, instrument_id=inst.id,
                    qty=e.filled_qty, price=e.avg_price, broker_ref=ref,
                    executed_at=datetime.combine(e.trade_date, time(15, 30), tzinfo=KST),
                    memo="증권사 자동 가져오기"))
                added += 1
        items.append(row)
    if not dry_run:
        # 로트·FIFO·실현손익은 원장 재생으로 일관성 유지 (수동 등록 경로와 동일 회계)
        from app.portfolios import _rebuild_ledger

        session.flush()
        _rebuild_ledger(session, pid)
        cred.last_import_at = datetime.now(KST)
        session.commit()
    return {"range": [start.isoformat(), end.isoformat()], "dry_run": dry_run,
            "fetched": len(execs), "added": added, "skipped": skipped,
            "unknown_codes": sorted(set(unknown)), "items": items}


def reconcile_plan(planned: list[dict], fills: list[dict]) -> list[dict]:
    """주문표(계획) vs 등록된 체결 대조 — 경고 목록만 만든다(자동 수정 없음).

    순수 함수(테스트 대상): planned=[{kind, instrument, side, qty, price}],
    fills=[{code_leg, side, qty, price}] 를 종목레그·방향으로 묶어 비교한다.
    """
    def key(leg: str, side: str) -> tuple[str, str]:
        return (leg, side)

    plan_by: dict[tuple[str, str], int] = {}
    for o in planned:
        plan_by[key(o["instrument"], o["side"])] = plan_by.get(key(o["instrument"], o["side"]), 0) + int(o["qty"])
    fill_by: dict[tuple[str, str], int] = {}
    for f in fills:
        fill_by[key(f["leg"], f["side"])] = fill_by.get(key(f["leg"], f["side"]), 0) + int(f["qty"])

    ko = {"K200": "200 ETF", "LEV": "레버리지"}
    side_ko = {"buy": "매수", "sell": "매도"}
    out: list[dict] = []
    for k in sorted(set(plan_by) | set(fill_by)):
        p, f = plan_by.get(k, 0), fill_by.get(k, 0)
        leg, side = k
        label = f"{ko.get(leg, leg)} {side_ko.get(side, side)}"
        if p and not f:
            out.append({"level": "info", "text": f"{label} 계획 {p}주 — 등록된 체결 없음(미이행 또는 미등록)"})
        elif f and not p:
            out.append({"level": "warn", "text": f"{label} {f}주 등록 — 이날 계획에 없던 거래"})
        elif p != f:
            out.append({"level": "warn", "text": f"{label} 계획 {p}주 ≠ 등록 {f}주 ({f - p:+d}주)"})
    return out


def reconcile_for_portfolio(session: Session, pid: int, market: str = "KR") -> dict | None:
    """실행일이 도래한 최신 계획 vs 그날 등록된 체결 — 주문표 경고용 (2026-09-05 지시).

    반환: {"date": ISO, "items": [{level, text}]} — 차이가 없으면 items 는 빈 목록.
    """
    from app.dashboard import kst_today
    from app.models import PortfolioPlan

    today = kst_today()
    plan = session.scalars(
        select(PortfolioPlan).where(PortfolioPlan.portfolio_id == pid,
                                    PortfolioPlan.trade_date <= today)
        .order_by(PortfolioPlan.trade_date.desc()).limit(1)).first()
    if plan is None:
        return None
    d = plan.trade_date
    lev_codes = {"122630", "QLD", "TQQQ"}
    fills = []
    for t in session.scalars(select(TradeTransaction)
                             .where(TradeTransaction.portfolio_id == pid)).all():
        if t.kind not in ("buy", "sell") or t.instrument_id is None:
            continue
        ts = t.executed_at.astimezone(KST) if t.executed_at.tzinfo else t.executed_at
        if ts.date() != d:
            continue
        code = session.get(Instrument, t.instrument_id).code
        fills.append({"leg": "LEV" if code in lev_codes else "K200",
                      "side": t.kind, "qty": t.qty or 0, "price": t.price or 0})
    items = reconcile_plan(plan.payload.get("orders", []), fills)
    return {"date": d.isoformat(), "items": items}
