"""계좌 예수금 연동 (2026-09-06 지시) — 시작 시 잔고 불러오기 + 장 마감 예수금 대조(경고·원클릭 보정).

1. **시작 시 불러오기** — 새 실전매매 시작 패널에서 계좌를 고르면 `GET /broker/accounts/{aid}/balance` 로 잔고 요약을 받아
   초기 입금(D+2 예수금 = 가수도정산금액)·보유분(전략 종목)을 미리 채운다. 값은 **사용자가 확인·수정한 뒤** 시작한다 —
   계좌를 매매일지·다른 포트와 함께 쓰면 예수금 전체가 이 전략 몫이 아닐 수 있어 자동 확정하지 않는다.
   시작 요청의 `credential_id` 로 계좌가 함께 연결된다(portfolios.create_portfolio).
2. **예수금 대조** — 15:45/17:10 동기화(체결 가져오기 뒤)와 화면의 '지금 대조'에서 앱 원장 현금 vs 계좌 D+2 예수금을 비교해
   `params.cash_check` 에 저장한다. 허용 오차(수수료·분배금 범위)를 넘으면 warn → 주문표 위 배너. **자동 수정은 하지 않고**
   "차액을 입출금으로 등록" 한 번으로 맞춘다. 09:01 무인 실행 사전 대조는 보유 수량만 본다 — 현금은 결제 시차(T+2)로
   오탐이 잦다 (ADR-008 ⑪).

왜 D+2 예수금인가: 예수금총액(dnca_tot_amt)은 아직 결제되지 않은 당일·전일 매수·매도가 섞여 있어 거래 직후 원장 현금과
어긋난다. 가수도정산금액(prvs_rcdl_excc_amt)은 모든 미결제 거래가 정산된 뒤의 현금이라 "입금 − 출금 − 매수 + 매도" 인
원장 현금과 같은 정의다. 남는 차이는 수수료·세금·분배금·예탁금 이용료·앱 밖 입출금이다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.broker import _cred, _owned, humanize_kis_error
from app.db import get_session
from app.models import BrokerCredential, PositionLot, TradePortfolio, TradeTransaction

logger = logging.getLogger(__name__)
router = APIRouter()
KST = timezone(timedelta(hours=9))

# 허용 오차 — 이 안의 차이는 수수료(편도 0.015% 내외)·분배금·이용료로 보고 경고하지 않는다(정보로만 표시·차액 등록은 가능).
CASH_TOL_KRW = 10_000
CASH_TOL_PCT = 0.001        # 원장 총자산(현금 + 보유 원가) 대비 0.1%
# 시작 패널이 보유분으로 채우는 전략 종목 — 나머지는 '전략 외' 로 표시만 한다
STRATEGY_CODES = {"KR": ("102110", "069500", "122630"), "US": ("QQQ", "QLD", "TQQQ")}


def _kis(cred: BrokerCredential):
    """broker._client 를 호출 시점에 찾는다 — 테스트가 app.broker._client 를 바꿔 끼우면 여기도 따라간다."""
    from app import broker

    return broker._client(cred)


# ── 원장 ──────────────────────────────────────────────────────────────────────────

def ledger_cash(session: Session, pid: int) -> int:
    """앱 원장 현금 = 입금 − 출금 − 매수금액 + 매도금액 (portfolio_summary 와 같은 정의, 시점 제한 없음)."""
    cash = 0
    for t in session.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id == pid)).all():
        if t.kind == "deposit":
            cash += int(t.amount or 0)
        elif t.kind == "withdraw":
            cash -= int(t.amount or 0)
        elif t.kind == "buy":
            cash -= int(t.qty or 0) * int(t.price or 0)
        elif t.kind == "sell":
            cash += int(t.qty or 0) * int(t.price or 0)
    return cash


def _ledger_cost(session: Session, pid: int) -> int:
    """보유 원가(잔여 로트) — 허용 오차의 분모(총자산 근사)에 쓴다."""
    return int(sum(int(l.qty_open) * int(l.price) for l in session.scalars(
        select(PositionLot).where(PositionLot.portfolio_id == pid, PositionLot.qty_open > 0)).all()))


# ── 잔고 요약 (시작 패널) ──────────────────────────────────────────────────────────

def balance_view(bal: dict, market: str) -> dict:
    """KisTradingClient.fetch_balance() 결과 → 시작 패널용 요약. 전략 종목을 앞에, 나머지는 평가액 순."""
    strat = set(STRATEGY_CODES.get(market, ()))
    holdings = [{**h, "strategy": h.get("code") in strat} for h in bal.get("holdings", [])]
    holdings.sort(key=lambda h: (not h["strategy"], -int(h.get("eval_amount") or 0)))
    deposit = int(bal.get("deposit") or 0)
    d2 = bal.get("deposit_d2")
    return {"deposit": deposit,
            "deposit_d2": int(d2) if d2 is not None else deposit,   # 구형 응답(D+2 없음)은 총액으로
            "deposit_d1": int(bal.get("deposit_d1") or 0),
            "total_eval": int(bal.get("total_eval") or 0),
            "holdings": holdings,
            "strategy_count": sum(1 for h in holdings if h["strategy"])}


@router.get("/broker/accounts/{aid}/balance")
def account_balance(aid: int, market: str = "KR", user_id: int = Depends(current_user_id),
                    session: Session = Depends(get_session)) -> dict:
    """등록된 계좌의 현재 잔고 요약 — 새 실전매매 시작 패널 '계좌에서 불러오기'. 저장하지 않는다(미리 채우기 전용)."""
    cred = session.get(BrokerCredential, aid)
    if cred is None or cred.user_id != user_id:
        raise HTTPException(status_code=404, detail="account not found")
    try:
        bal = _kis(cred).fetch_balance()
    except Exception as exc:  # noqa: BLE001 — 자격·유량 오류를 사용자 문구로
        logger.warning("account balance failed aid=%s: %s", aid, exc)
        raise HTTPException(status_code=502, detail=f"증권사 조회 실패 — {humanize_kis_error(str(exc)[:200])}")
    return {"date": datetime.now(KST).date().isoformat(), "env": cred.env, "market": market,
            **balance_view(bal, market),
            "note": "D+2 예수금(가수도정산금액)을 초기 현금으로, 전략 종목 보유를 보유분으로 채웁니다. 값은 확인·수정 후 시작하세요."}


# ── 예수금 대조 (params.cash_check) ────────────────────────────────────────────────

def pf_cash_check(pf: TradePortfolio) -> dict | None:
    return (pf.params or {}).get("cash_check")


def _store(pf: TradePortfolio, check: dict) -> None:
    params = dict(pf.params or {})
    params["cash_check"] = check
    pf.params = params  # JSONB 변경 감지 — 재할당 필수


def compute_cash_check(session: Session, pf: TradePortfolio, bal: dict, now: datetime | None = None) -> dict:
    """원장 현금 vs 계좌 D+2 예수금. diff = 계좌 − 원장 (양수 = 계좌에 더 있음 → 입금으로 보정)."""
    now = now or datetime.now(KST)
    ledger = ledger_cash(session, pf.id)
    d2 = bal.get("deposit_d2")
    acct = int(d2) if d2 is not None else int(bal.get("deposit") or 0)
    diff = acct - ledger
    tol = max(CASH_TOL_KRW, int((ledger + _ledger_cost(session, pf.id)) * CASH_TOL_PCT))
    return {"date": now.date().isoformat(), "at": now.isoformat(timespec="minutes"),
            "ledger_cash": ledger, "account_cash": acct, "account_deposit": int(bal.get("deposit") or 0),
            "diff": diff, "tolerance": tol, "warn": abs(diff) > tol}


def refresh_cash_check(session: Session, pf: TradePortfolio, cred: BrokerCredential,
                       now: datetime | None = None, client=None) -> dict:
    """잔고 조회 → 대조 → params 저장 (commit 은 호출자). 국내 잔고 TR 이라 국내 포트만 의미가 있다."""
    bal = (client or _kis(cred)).fetch_balance()
    check = compute_cash_check(session, pf, bal, now)
    _store(pf, check)
    if check["warn"]:
        logger.warning("cash-check pid=%s ledger=%s account=%s diff=%s", pf.id, check["ledger_cash"], check["account_cash"], check["diff"])
    return check


@router.get("/portfolio/{pid}/cash-check")
def get_cash_check(pid: int, refresh: bool = False, user_id: int = Depends(current_user_id),
                   session: Session = Depends(get_session)) -> dict:
    """저장된 예수금 대조 결과. refresh=true 면 지금 계좌를 조회해 다시 대조하고 저장한다."""
    pf = _owned(session, pid, user_id)
    if not refresh:
        return {"cash_check": pf_cash_check(pf)}
    if pf.market != "KR":
        raise HTTPException(status_code=409, detail="예수금 대조는 국내 포트만 지원합니다 (국내 잔고 조회 TR)")
    cred = _cred(session, pid, user_id)
    try:
        check = refresh_cash_check(session, pf, cred)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("cash-check refresh failed pid=%s: %s", pid, exc)
        raise HTTPException(status_code=502, detail=f"증권사 조회 실패 — {humanize_kis_error(str(exc)[:200])}")
    session.commit()
    return {"cash_check": check}


@router.post("/portfolio/{pid}/cash-check/align", status_code=201)
def align_cash(pid: int, user_id: int = Depends(current_user_id),
               session: Session = Depends(get_session)) -> dict:
    """차액을 입금/출금 한 건으로 등록해 원장 현금을 계좌 D+2 예수금에 맞춘다 — 사용자가 버튼으로만.

    오늘 대조 결과가 있고, 대조 이후 원장이 바뀌지 않았을 때만 등록한다(낡은 차액으로 보정하는 사고 방지).
    """
    pf = _owned(session, pid, user_id)
    check = pf_cash_check(pf)
    now = datetime.now(KST)
    if not check:
        raise HTTPException(status_code=409, detail="먼저 예수금 대조를 실행하세요 ('지금 대조')")
    if check.get("date") != now.date().isoformat():
        raise HTTPException(status_code=409, detail="오늘의 대조 결과가 아닙니다 — '지금 대조'로 다시 확인한 뒤 등록하세요")
    if ledger_cash(session, pf.id) != int(check["ledger_cash"]):
        raise HTTPException(status_code=409, detail="대조 이후 원장이 바뀌었습니다 — '지금 대조'로 다시 확인한 뒤 등록하세요")
    diff = int(check["diff"])
    if diff == 0:
        return {"added": False, "cash_check": check}
    kind = "deposit" if diff > 0 else "withdraw"
    tx = TradeTransaction(
        portfolio_id=pf.id, kind=kind, amount=abs(diff), executed_at=now, tags=["cash_check"],
        memo=f"계좌 예수금 대조 보정 — 증권사 D+2 예수금 {int(check['account_cash']):,}원에 맞춤 (수수료·분배금·앱 밖 입출금 차액)")
    session.add(tx)
    new_check = {**check, "ledger_cash": int(check["account_cash"]), "diff": 0, "warn": False,
                 "aligned_at": now.isoformat(timespec="minutes"), "aligned_amount": diff}
    _store(pf, new_check)
    session.flush()   # 보정 거래 행을 DB 에 반영한 뒤 계산 — autoflush=False (2026-09-09, 등록 경로와 같은 결함)
    from app.dashboard import compute_user_snapshot, kst_today  # 당일 스냅샷 즉시 반영 (수동 등록 경로와 동일)

    compute_user_snapshot(session, user_id, kst_today())
    from app.activity import log_event  # 로그 페이지 (2026-09-06)

    log_event(session, user_id, "cash_check.align",
              f"예수금 보정 — {'입금' if kind == 'deposit' else '출금'} {abs(diff):,}원 등록, 원장 현금을 계좌 D+2 예수금 {int(check['account_cash']):,}원에 맞춤",
              portfolio_id=pf.id, data={"tx_id": None, "kind": kind, "amount": abs(diff), "account_cash": check["account_cash"]}, at=now)
    session.commit()
    logger.info("cash-check align pid=%s %s %s", pf.id, kind, abs(diff))
    return {"added": True, "tx_id": tx.id, "kind": kind, "amount": abs(diff), "cash_check": new_check}
