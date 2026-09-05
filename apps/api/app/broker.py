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
    """포트에 연결된 증권사 계좌 — 설정에서 등록한 목록 중 선택된 것 (0017)."""
    pf = _owned(session, pid, user_id)
    row = session.get(BrokerCredential, pf.broker_credential_id) if pf.broker_credential_id else None
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=409,
                            detail="이 포트에 연결된 증권사 계좌가 없습니다 — 일반 설정에서 계좌를 등록하고 연결하세요")
    return row


def _mask(s: str) -> str:
    return s[:4] + "****" + s[-2:] if len(s) > 8 else "****"


def split_account(raw: str, prdt: str = "01") -> tuple[str, str]:
    """계좌번호 입력 정규화 (2026-09-05) — KIS 는 종합계좌 8자리(CANO)와 상품코드 2자리를 나눠 받는다.

    "12345678-01" / "12345678 01" / "1234567801" 처럼 붙여 입력해도 8+2 로 분리한다.
    8자리만 오면 상품코드는 입력값(기본 01)을 쓴다.
    """
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 10:
        return digits[:8], digits[8:10]
    return digits, prdt


class AccountIn(BaseModel):
    """설정에서 등록하는 증권사 계좌 (2026-09-05 지시)."""

    label: str = Field(default="", max_length=40)
    app_key: str = Field(min_length=10, max_length=200)
    app_secret: str = Field(min_length=10, max_length=400)
    account_no: str = Field(min_length=6, max_length=20)
    acnt_prdt_cd: str = Field(default="01", pattern=r"^\d{2}$")
    env: str = Field(default="prod", pattern="^(prod|vps)$")


def _acct_out(row: BrokerCredential, linked_names: list[str] | None = None) -> dict:
    return {"id": row.id, "label": row.label or f"{_mask(row.account_no)}",
            "env": row.env, "acnt_prdt_cd": row.acnt_prdt_cd,
            "app_key": _mask(row.app_key), "account_no": _mask(row.account_no),
            "last_import_at": row.last_import_at.isoformat() if row.last_import_at else None,
            "linked_portfolios": linked_names or []}


@router.get("/broker/accounts")
def list_accounts(user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> dict:
    """등록된 증권사 계좌 목록 (설정 화면) — 키는 마스킹, 연결된 포트 이름 포함."""
    from app.models import TradePortfolio

    rows = session.scalars(select(BrokerCredential).where(BrokerCredential.user_id == user_id)
                           .order_by(BrokerCredential.id)).all()
    ports = session.scalars(select(TradePortfolio).where(TradePortfolio.user_id == user_id)).all()
    by_cred: dict[int, list[str]] = {}
    for p in ports:
        if p.broker_credential_id:
            by_cred.setdefault(p.broker_credential_id, []).append(p.name)
    return {"items": [_acct_out(r, by_cred.get(r.id, [])) for r in rows]}


@router.post("/broker/accounts", status_code=201)
def create_account(body: AccountIn, user_id: int = Depends(current_user_id),
                   session: Session = Depends(get_session)) -> dict:
    cano, prdt = split_account(body.account_no, body.acnt_prdt_cd)
    if len(cano) != 8:
        raise HTTPException(status_code=422,
                            detail="계좌번호는 종합계좌 8자리(예: 12345678) 또는 12345678-01 형식이어야 합니다")
    row = BrokerCredential(user_id=user_id, label=body.label.strip() or f"{cano}-{prdt}",
                           env=body.env, app_key=body.app_key.strip(),
                           app_secret=body.app_secret.strip(), account_no=cano, acnt_prdt_cd=prdt)
    session.add(row)
    session.commit()
    return _acct_out(row)


@router.delete("/broker/accounts/{aid}")
def delete_account(aid: int, user_id: int = Depends(current_user_id),
                   session: Session = Depends(get_session)) -> dict:
    row = session.get(BrokerCredential, aid)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="account not found")
    session.delete(row)  # 포트 연결은 FK ON DELETE SET NULL 로 자동 해제
    session.commit()
    return {"deleted": True}


class LinkIn(BaseModel):
    credential_id: int | None = None  # None = 연결 해제


@router.get("/portfolio/{pid}/broker")
def get_broker(pid: int, user_id: int = Depends(current_user_id),
               session: Session = Depends(get_session)) -> dict:
    """포트에 연결된 계좌 상태 — 실전매매 화면용."""
    pf = _owned(session, pid, user_id)
    row = session.get(BrokerCredential, pf.broker_credential_id) if pf.broker_credential_id else None
    if row is None or row.user_id != user_id:
        return {"linked": False}
    return {"linked": True, **_acct_out(row)}


@router.put("/portfolio/{pid}/broker")
def link_broker(pid: int, body: LinkIn, user_id: int = Depends(current_user_id),
                session: Session = Depends(get_session)) -> dict:
    """설정에서 등록한 계좌를 이 포트에 연결/해제 (키 입력은 설정에서만)."""
    pf = _owned(session, pid, user_id)
    if body.credential_id is None:
        pf.broker_credential_id = None
        session.commit()
        return {"linked": False}
    row = session.get(BrokerCredential, body.credential_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="account not found")
    pf.broker_credential_id = row.id
    session.commit()
    return {"linked": True, **_acct_out(row)}


def _client(cred: BrokerCredential):
    from app.services.kis_auth import KisAuth
    from app.services.kis_client import KisTradingClient

    auth = KisAuth(cred.app_key, cred.app_secret, cred.env)
    return KisTradingClient(auth, cano=cred.account_no, acnt_prdt_cd=cred.acnt_prdt_cd)


class ProbeIn(BaseModel):
    """계좌 확인 — 저장 전 자격·계좌 유효성 검사 (2026-09-05 지시).

    KIS 는 앱키로 '계좌 목록'을 주는 API 가 없다(요청마다 CANO+상품코드 필수). 그래서 목록 대신
    입력한 계좌를 실제 조회해 확인하고, 상품코드를 모르면 후보를 훑어 되는 것을 찾아 제시한다.
    """

    app_key: str = Field(min_length=10, max_length=200)
    app_secret: str = Field(min_length=10, max_length=400)
    account_no: str = Field(min_length=6, max_length=20)
    acnt_prdt_cd: str | None = None      # None/빈값 = 후보 자동 탐색
    env: str = Field(default="prod", pattern="^(prod|vps)$")


# 상품코드 후보 — 01 종합위탁이 대부분, 나머지는 연금·ISA 등 (탐색 순서)
PRDT_CANDIDATES = ["01", "22", "29", "03", "12"]


@router.post("/broker/probe")
def probe_account(body: ProbeIn, _user: int = Depends(current_user_id)) -> dict:
    """입력한 자격으로 잔고를 조회해 계좌가 유효한지 확인한다(저장하지 않음).

    상품코드 미지정 시 후보를 순차 시도하고, 성공한 계좌들을 목록으로 돌려준다 —
    사용자는 그중 하나를 골라 저장하면 된다.
    """
    from app.services.kis_auth import KisAuth
    from app.services.kis_client import KisTradingClient

    cano, prdt = split_account(body.account_no, body.acnt_prdt_cd or "01")
    if len(cano) != 8:
        raise HTTPException(status_code=422, detail="계좌번호는 8자리(또는 12345678-01) 형식이어야 합니다")
    candidates = [prdt] if body.acnt_prdt_cd else PRDT_CANDIDATES
    auth = KisAuth(body.app_key.strip(), body.app_secret.strip(), body.env)
    found, errors = [], []
    for cd in candidates:
        try:
            info = KisTradingClient(auth, cano=cano, acnt_prdt_cd=cd).probe_balance()
            found.append({"account_no": cano, "acnt_prdt_cd": cd,
                          "label": f"{cano}-{cd}", "holdings": info["holdings"],
                          "deposit": info["deposit"], "total_eval": info["total_eval"]})
        except Exception as exc:  # noqa: BLE001 — 후보 실패는 정상 흐름(다음 후보 시도)
            errors.append(f"{cd}: {str(exc)[:120]}")
            if "EGW00" in str(exc) or "인증" in str(exc) or "401" in str(exc):
                break  # 자격 자체가 틀림 — 더 시도할 필요 없음
    if not found:
        raise HTTPException(status_code=502,
                            detail="계좌 확인 실패 — " + (errors[0] if errors else "조회 결과 없음"))
    return {"accounts": found, "tried": candidates, "errors": errors}


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
