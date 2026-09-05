"""증권사 연동 (2026-09-05 지시) — 체결 자동 가져오기 + 주문표 대조 경고 + **예약주문 접수**.

원칙:
- **주문은 예약주문만, 사용자 클릭으로만**: 장 마감 후 주문표에서 사용자가 버튼을 눌러 접수한다(자동 발주 없음,
  정규 장중 주문 TR 미사용). 2026-09-05 지시로 "조회 전용" 원칙을 이렇게 바꿨다.
- **자동 수정 금지**: 대조 결과는 경고로만 노출하고 원장을 임의로 고치지 않는다(사용자 지시).
- 자격(앱키·시크릿·계좌번호)은 앱 레벨 AES-GCM 암호화 컬럼에 저장하고 응답에는 마스킹만 내보낸다.
- 장 마감 후(15:45) 배치가 연결 계좌의 체결을 가져와 원장·통계를 갱신하고 예약주문 상태를 확정한다.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import BrokerCredential, BrokerOrder, Instrument, TradeTransaction

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


# KIS 원문 오류 → 사용자가 바로 조치할 수 있는 안내 (2026-09-05)
_ERROR_HINTS = [
    ("INVALID_CHECK_ACNO", "계좌번호가 올바르지 않습니다 — 종합계좌 8자리와 상품코드(01·22 등)를 확인하세요"),
    ("EGW00103", "앱키가 유효하지 않습니다 — KIS Developers 에서 발급한 값을 다시 확인하세요"),
    ("EGW00133", "토큰 발급이 분당 1회로 제한됩니다 — 1분 뒤 다시 시도하세요"),
    ("EGW00121", "앱시크릿이 올바르지 않습니다"),
    ("EGW00105", "앱시크릿이 올바르지 않습니다 — 전체(180자 내외)를 다시 복사해 붙여넣으세요"),
    ("EGW00304", "앱시크릿이 올바르지 않습니다 — 전체(180자 내외)를 다시 복사해 붙여넣으세요"),
    ("모의", "모의투자 계좌는 환경을 '모의투자'로 선택해야 합니다"),
]


def humanize_kis_error(msg: str) -> str:
    for key, hint in _ERROR_HINTS:
        if key in msg:
            return f"{hint} (원문: {msg[:80]})"
    return msg


# KIS 자격 길이 (앱키 36자·앱시크릿 180자 내외) — 잘려 붙여넣은 값을 저장 전에 거른다 (2026-09-05).
# 짧은 시크릿은 토큰은 캐시로 넘어가고 조회에서만 EGW00304 로 실패해 원인 파악이 어렵다.
KIS_KEY_MIN, KIS_SECRET_MIN = 20, 40


def check_credential(app_key: str, app_secret: str) -> tuple[str, str]:
    k, sec = app_key.strip(), app_secret.strip()
    if len(k) < KIS_KEY_MIN:
        raise HTTPException(status_code=422,
                            detail=f"앱키가 너무 짧습니다({len(k)}자) — KIS 앱키는 36자입니다. 전체를 복사해 붙여넣으세요")
    if len(sec) < KIS_SECRET_MIN:
        raise HTTPException(status_code=422,
                            detail=f"앱시크릿이 너무 짧습니다({len(sec)}자) — KIS 앱시크릿은 180자 내외입니다. 전체를 복사해 붙여넣으세요")
    return k, sec


def _mask(s: str, head: int = 4, tail: int = 2) -> str:
    """중간 자릿수 마스킹 (2026-09-05 지시) — 앞뒤 일부만 남겨 어떤 값인지 식별 가능하게."""
    if not s:
        return ""
    if len(s) <= head + tail:
        return "*" * len(s)
    hidden = min(max(len(s) - head - tail, 1), 8)  # 실제 가려진 길이(최대 8) — 원본보다 길어 보이지 않게
    return f"{s[:head]}{'*' * hidden}{s[-tail:]}"


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
    app_key: str = Field(min_length=1, max_length=200)      # 길이 안내는 check_credential 이 담당
    app_secret: str = Field(min_length=1, max_length=400)
    account_no: str = Field(min_length=6, max_length=20)
    acnt_prdt_cd: str = Field(default="01", pattern=r"^\d{2}$")
    env: str = Field(default="prod", pattern="^(prod|vps)$")


def _acct_out(row: BrokerCredential, linked_names: list[str] | None = None) -> dict:
    return {"id": row.id, "label": row.label or f"{_mask(row.account_no)}",
            "env": row.env, "acnt_prdt_cd": row.acnt_prdt_cd,
            # 저장된 값은 항상 마스킹해서만 내보낸다 (수정 화면에서 현재 값 식별용)
            "app_key": _mask(row.app_key), "app_secret": _mask(row.app_secret, 2, 2),
            "account_no": _mask(row.account_no, 4, 2),
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
    key, secret = check_credential(body.app_key, body.app_secret)
    row = BrokerCredential(user_id=user_id, label=body.label.strip() or f"{cano}-{prdt}",
                           env=body.env, app_key=key,
                           app_secret=secret, account_no=cano, acnt_prdt_cd=prdt)
    session.add(row)
    session.commit()
    return _acct_out(row)


class AccountUpdate(BaseModel):
    """계좌 수정 (2026-09-05 지시) — 키는 비워두면 기존 값을 유지(회전 시에만 입력)."""

    label: str | None = Field(default=None, max_length=40)
    account_no: str | None = Field(default=None, max_length=20)
    acnt_prdt_cd: str | None = Field(default=None, pattern=r"^\d{2}$")
    env: str | None = Field(default=None, pattern="^(prod|vps)$")
    app_key: str | None = Field(default=None, max_length=200)
    app_secret: str | None = Field(default=None, max_length=400)


@router.put("/broker/accounts/{aid}")
def update_account(aid: int, body: AccountUpdate, user_id: int = Depends(current_user_id),
                   session: Session = Depends(get_session)) -> dict:
    row = session.get(BrokerCredential, aid)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="account not found")
    if body.account_no is not None:
        cano, prdt = split_account(body.account_no, body.acnt_prdt_cd or row.acnt_prdt_cd)
        if len(cano) != 8:
            raise HTTPException(status_code=422,
                                detail="계좌번호는 종합계좌 8자리(예: 12345678) 또는 12345678-01 형식이어야 합니다")
        row.account_no, row.acnt_prdt_cd = cano, prdt
    elif body.acnt_prdt_cd:
        row.acnt_prdt_cd = body.acnt_prdt_cd
    if body.label is not None:
        row.label = body.label.strip() or row.label
    if body.env:
        row.env = body.env
    # 비우면 기존 값 유지 — 입력했다면 저장된 짝과 함께 길이를 검사한다
    new_key = body.app_key.strip() if body.app_key else ""
    new_secret = body.app_secret.strip() if body.app_secret else ""
    if new_key or new_secret:
        key, secret = check_credential(new_key or row.app_key, new_secret or row.app_secret)
        row.app_key, row.app_secret = key, secret
    session.commit()
    return _acct_out(row)


@router.post("/broker/accounts/{aid}/test")
def test_account(aid: int, user_id: int = Depends(current_user_id),
                 session: Session = Depends(get_session)) -> dict:
    """저장된 자격으로 실제 조회해 연결 상태를 확인한다 (2026-09-05 — 잘못 등록된 키 조기 발견)."""
    from app.services.kis_auth import KisAuth
    from app.services.kis_client import KisTradingClient

    row = session.get(BrokerCredential, aid)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="account not found")
    auth = KisAuth(row.app_key, row.app_secret, row.env, wait_on_rate_limit=False)
    client = KisTradingClient(auth, cano=row.account_no, acnt_prdt_cd=row.acnt_prdt_cd)
    try:
        info = client.probe_balance()
    except Exception as exc:  # noqa: BLE001 — 사유를 그대로 보여준다(잘못된 키·환경·계좌)
        msg = humanize_kis_error(str(exc)[:200])
        # 계좌(상품코드) 오류로 보이면 후보를 훑어 되는 코드를 제안한다 (2026-09-05)
        suggest = None
        if "ACNO" in msg or "계좌" in msg:
            for cd in PRDT_CANDIDATES:
                if cd == row.acnt_prdt_cd:
                    continue
                try:
                    ok = client.probe_balance(prdt=cd)
                except Exception:  # noqa: BLE001 — 후보 실패는 정상 흐름
                    continue
                suggest = {"acnt_prdt_cd": cd, "holdings": ok["holdings"], "deposit": ok["deposit"]}
                break
        # 같은 앱키가 다른 계좌에도 등록돼 있으면 그것이 원인일 가능성이 가장 크다 (2026-09-05 실사례:
        # 연금계좌용 앱키로 위탁계좌를 조회 → 모든 상품코드에서 INVALID_CHECK_ACNO). KIS 앱키는 발급 시 고른 계좌에만 유효.
        if suggest is None and ("ACNO" in msg or "계좌" in msg):
            twin = next((o for o in session.scalars(select(BrokerCredential)
                                                    .where(BrokerCredential.user_id == user_id,
                                                           BrokerCredential.id != row.id)).all()
                         if o.app_key == row.app_key and o.account_no != row.account_no), None)
            if twin is not None:
                msg = (f"이 앱키는 '{twin.label}'({_mask(twin.account_no, 4, 2)}-{twin.acnt_prdt_cd})에 등록된 것과 같습니다. "
                       "KIS 앱키는 발급할 때 고른 계좌 하나에만 유효하므로, 이 계좌용 앱키·시크릿을 "
                       "KIS Developers 에서 따로 발급해 입력하세요. " + msg)
        return {"ok": False, "message": msg, "suggest": suggest}
    return {"ok": True, "holdings": info["holdings"], "deposit": info["deposit"],
            "total_eval": info["total_eval"]}


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

    # 사용자가 화면에서 누른 요청 — 분당 제한이어도 기다리지 않고 즉시 안내 (2026-09-05)
    auth = KisAuth(cred.app_key, cred.app_secret, cred.env, wait_on_rate_limit=False)
    return KisTradingClient(auth, cano=cred.account_no, acnt_prdt_cd=cred.acnt_prdt_cd)


class ProbeIn(BaseModel):
    """계좌 확인 — 저장 전 자격·계좌 유효성 검사 (2026-09-05 지시).

    KIS 는 앱키로 '계좌 목록'을 주는 API 가 없다(요청마다 CANO+상품코드 필수). 그래서 목록 대신
    입력한 계좌를 실제 조회해 확인하고, 상품코드를 모르면 후보를 훑어 되는 것을 찾아 제시한다.
    """

    app_key: str = Field(min_length=1, max_length=200)      # 길이 안내는 check_credential 이 담당
    app_secret: str = Field(min_length=1, max_length=400)
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
    key, secret = check_credential(body.app_key, body.app_secret)
    auth = KisAuth(key, secret, body.env, wait_on_rate_limit=False)
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
                            detail="계좌 확인 실패 — " + humanize_kis_error(errors[0] if errors else "조회 결과 없음"))
    return {"accounts": found, "tried": candidates, "errors": errors}


class BrokerFetchError(RuntimeError):
    """증권사 조회 실패 — 라우트는 502 로, 배치는 기록으로 처리한다."""


@router.post("/portfolio/{pid}/import-fills")
def import_fills(pid: int, days: int = 7, dry_run: bool = True,
                 user_id: int = Depends(current_user_id),
                 session: Session = Depends(get_session)) -> dict:
    """증권사 체결 내역을 가져와 거래 원장에 등록 (기본은 미리보기).

    멱등: broker_ref = "주문번호:일자" 유니크 — 재실행해도 중복 등록되지 않는다.
    """
    cred = _cred(session, pid, user_id)
    try:
        return import_fills_for_portfolio(session, pid, cred, days=days, dry_run=dry_run)
    except BrokerFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def import_fills_for_portfolio(session: Session, pid: int, cred: BrokerCredential,
                               days: int = 7, dry_run: bool = True) -> dict:
    """체결 가져오기 본체 — 화면(라우트)과 장 마감 후 배치가 같이 쓴다 (2026-09-05)."""
    end = datetime.now(KST).date()
    start = end - timedelta(days=max(1, min(days, 90)) - 1)
    try:
        execs = _client(cred).fetch_executions(start, end)
    except Exception as exc:  # noqa: BLE001 — 자격 오류·유량 등 원인을 사용자에게 그대로 전달
        logger.warning("import-fills failed pid=%s: %s", pid, exc)
        raise BrokerFetchError(f"증권사 조회 실패 — {humanize_kis_error(str(exc)[:200])}")

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


# ── 예약주문 접수·취소·상태 (0019, 2026-09-05 지시) ─────────────────────────────────
# 흐름: 장 마감 후 주문표 → [증권사에 예약주문 접수] 클릭 → 확인 → KIS 예약주문(CTSC0008U) 줄 단위 접수 →
#       화면에 등록완료 표시 → 장 시작 시 KIS 가 주문 → 15:45 배치가 체결 가져오기 + 상태(체결/미체결) 확정.

STATUS_KO = {"reserved": "등록완료", "cancelled": "취소됨", "filled": "체결", "partial": "일부 체결",
             "unfilled": "미체결", "failed": "접수 실패", "duplicate": "이미 접수됨", "mismatch": "주문표 불일치"}


def reservation_window(now: datetime | None = None, session: Session | None = None) -> dict:
    """예약주문 접수 가능 여부 — KIS 규정: 15:40 ~ 다음 영업일 07:30, 서버 초기화(23:40~00:10) 제외.

    휴장일(주말·공휴일)은 하루 종일 창 안이다(전 영업일 15:40 ~ 다음 영업일 07:30).
    """
    now = now or datetime.now(KST)
    t = now.time()
    if t >= time(23, 40) or t < time(0, 10):
        return {"open": False, "reason": "KIS 서버 초기화 시간(23:40~00:10)에는 예약주문을 받지 않습니다"}
    trading_day = now.weekday() < 5
    if trading_day and session is not None:
        from app.models import TradingCalendar

        cal = session.get(TradingCalendar, now.date())
        if cal is not None and not cal.is_open:
            trading_day = False
    if not trading_day:
        return {"open": True, "reason": "휴장일 — 다음 영업일 장 시작 시 주문됩니다"}
    if t >= time(15, 40):
        return {"open": True, "reason": "예약주문 접수 시간 — 다음 영업일 장 시작 시 주문됩니다"}
    if t < time(7, 30):
        return {"open": True, "reason": "예약주문 접수 시간 — 오늘 장 시작 시 주문됩니다"}
    return {"open": False, "reason": "예약주문은 장 마감 후 15:40 ~ 다음 영업일 07:30 에만 접수됩니다 — 지금은 장중입니다"}


def line_key(o: dict) -> str:
    """주문표 한 줄의 식별자 — signals 의 병합 키(instrument, side, otype, price, kind)와 같은 정보."""
    price = o.get("price")
    return f"{o.get('kind')}:{o.get('instrument')}:{o.get('side')}:{o.get('otype')}:{int(price) if price else 'mkt'}"


def _resolve_codes(session: Session, pf) -> tuple[str, str]:
    """주문표 레그(K200/LEV) → 종목코드. signals._portfolio_orders 와 같은 규칙(보유 종목 → 포트 설정 → KODEX)."""
    from app.models import PositionLot

    if pf.market == "US":
        raise HTTPException(status_code=409, detail="미국 포트는 예약주문을 지원하지 않습니다 (KIS 국내주식 전용)")
    held = {session.get(Instrument, l.instrument_id).code
            for l in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pf.id)).all()}
    pref = (pf.params or {}).get("code_200") if pf.params else None
    if "102110" in held and "069500" not in held:
        code_200 = "102110"
    elif "069500" in held:
        code_200 = "069500"
    else:
        code_200 = pref or "069500"
    return code_200, "122630"


def _order_out(o: BrokerOrder) -> dict:
    return {"id": o.id, "plan_date": o.plan_date.isoformat(), "line_key": o.line_key, "code": o.code,
            "instrument": o.instrument, "kind": o.kind, "side": o.side, "otype": o.otype,
            "qty": o.qty, "price": o.price, "rsvn_ord_seq": o.rsvn_ord_seq, "order_no": o.order_no,
            "filled_qty": o.filled_qty, "status": o.status, "status_ko": STATUS_KO.get(o.status, o.status),
            "message": o.message,
            "created_at": o.created_at.isoformat() if o.created_at else None}


def sync_orders(session: Session, cred: BrokerCredential, rows: list[BrokerOrder],
                today: date, now: datetime | None = None) -> int:
    """KIS 예약주문 조회로 활성 행의 상태를 갱신한다. 반환: 상태가 바뀐 건수. 조회 실패는 0 (화면을 막지 않음)."""
    active = [r for r in rows if r.status in ("reserved", "partial")]
    if not active:
        return 0
    now = now or datetime.now(KST)
    # KIS 조회의 일자 범위는 **주문일자(실행일)** 기준 — 접수일이 아니다 (실계좌 확인 2026-09-05:
    # 토요일 접수분이 ord_dt=다음 영업일로 조회됨). 접수일~실행일을 모두 덮는다.
    start = min((r.created_at.astimezone(KST).date() if r.created_at else today) for r in active)
    end = max(max(r.plan_date for r in active), today)
    try:
        remote = _client(cred).list_reserved_orders(start, end)
    except Exception as exc:  # noqa: BLE001 — 상태 조회 실패는 다음 동기화에서 재시도
        logger.warning("reserved-order sync failed cred=%s: %s", cred.id, exc)
        return 0
    by_seq = {str(r["rsvn_ord_seq"]): r for r in remote if r.get("rsvn_ord_seq")}
    changed = 0
    for r in active:
        m = by_seq.get(str(r.rsvn_ord_seq or ""))
        if m is None:
            continue
        r.order_no = m.get("order_no") or r.order_no
        r.filled_qty = int(m.get("filled_qty") or 0)
        day_over = r.plan_date < today or (r.plan_date == today and now.time() >= time(15, 30))
        if m.get("cancel_dt"):
            st = "cancelled"
        elif r.filled_qty >= r.qty:
            st = "filled"
        elif r.filled_qty > 0:
            st = "partial" if not day_over else "partial"
        elif day_over:
            st = "unfilled"     # 실행일 장이 끝났는데 체결 0 — 예약주문은 당일 유효라 사실상 소멸
        else:
            st = "reserved"
        if m.get("result"):
            r.message = str(m["result"])[:200]
        r.response = {**(r.response or {}), "sync": m}
        if st != r.status:
            r.status = st
            changed += 1
    return changed


class OrderLineIn(BaseModel):
    instrument: str = Field(pattern="^(K200|LEV)$")
    kind: str = Field(min_length=1, max_length=30)
    side: str = Field(pattern="^(buy|sell)$")
    otype: str = Field(pattern="^(limit|market)$")
    qty: int = Field(gt=0)
    price: int | None = Field(default=None, ge=0)


class ReserveIn(BaseModel):
    date: date                                   # 주문표 실행일 (signal.exec_day)
    lines: list[OrderLineIn] = Field(min_length=1, max_length=40)


@router.get("/portfolio/{pid}/orders")
def list_broker_orders(pid: int, date_: date | None = Query(default=None, alias="date"), refresh: bool = False,
                       user_id: int = Depends(current_user_id),
                       session: Session = Depends(get_session)) -> dict:
    """이 포트의 예약주문 접수 기록 (실행일 필터) + 접수 가능 창. refresh=1 이면 KIS 조회로 상태를 갱신한다."""
    from app.dashboard import kst_today

    pf = _owned(session, pid, user_id)
    q = select(BrokerOrder).where(BrokerOrder.portfolio_id == pid)
    if date_ is not None:
        q = q.where(BrokerOrder.plan_date == date_)
    rows = session.scalars(q.order_by(BrokerOrder.id)).all()
    if refresh and pf.broker_credential_id:
        cred = session.get(BrokerCredential, pf.broker_credential_id)
        if cred is not None and cred.user_id == user_id and sync_orders(session, cred, rows, kst_today()):
            session.commit()
    return {"window": reservation_window(session=session), "items": [_order_out(r) for r in rows]}


@router.post("/portfolio/{pid}/orders/reserve")
def reserve_broker_orders(pid: int, body: ReserveIn, user_id: int = Depends(current_user_id),
                          session: Session = Depends(get_session)) -> dict:
    """주문표 줄들을 KIS 예약주문으로 접수한다 — 사용자가 화면에서 확인한 뒤 누른 요청만.

    안전장치: 연결 계좌(실전)만 · 접수 창 안에서만 · 서버에 저장된 그날의 주문표 스냅샷과 줄이 정확히 일치해야 함 ·
    같은 줄의 활성 예약이 있으면 건너뜀(중복 접수 방지) · 줄 단위 실패는 기록하고 계속.
    """
    from app.dashboard import kst_today
    from app.models import PortfolioPlan

    pf = _owned(session, pid, user_id)
    cred = _cred(session, pid, user_id)
    if cred.env != "prod":
        raise HTTPException(status_code=409, detail="모의투자 계좌는 예약주문을 지원하지 않습니다 — 실전 계좌를 연결하세요")
    win = reservation_window(session=session)
    if not win["open"]:
        raise HTTPException(status_code=409, detail=win["reason"])
    today = kst_today()
    if body.date < today:
        raise HTTPException(status_code=409, detail="지난 실행일의 주문표는 접수할 수 없습니다 — 주문표를 새로 고치세요")
    plan = session.scalar(select(PortfolioPlan).where(PortfolioPlan.portfolio_id == pid,
                                                      PortfolioPlan.trade_date == body.date))
    if plan is None:
        raise HTTPException(status_code=404, detail="이 실행일의 주문표 스냅샷이 없습니다 — 주문표를 먼저 조회하세요")
    plan_lines = {line_key(o): o for o in (plan.payload or {}).get("orders", [])}
    code_200, code_lev = _resolve_codes(session, pf)
    active = {r.line_key for r in session.scalars(
        select(BrokerOrder).where(BrokerOrder.portfolio_id == pid, BrokerOrder.plan_date == body.date,
                                  BrokerOrder.status == "reserved")).all()}
    client = _client(cred)
    items, ok, failed = [], 0, 0
    for ln in body.lines:
        o = ln.model_dump()
        key = line_key(o)
        code = code_200 if ln.instrument == "K200" else code_lev
        price = ln.price if ln.otype == "limit" else None
        pending = {"id": None, "plan_date": body.date.isoformat(), "line_key": key, "code": code,
                   "instrument": ln.instrument, "kind": ln.kind, "side": ln.side, "otype": ln.otype,
                   "qty": ln.qty, "price": price, "rsvn_ord_seq": None, "order_no": None, "filled_qty": 0,
                   "created_at": None}
        pl = plan_lines.get(key)
        if pl is None or int(pl.get("qty") or 0) != ln.qty:
            items.append({**pending, "status": "mismatch", "status_ko": STATUS_KO["mismatch"],
                          "message": "화면의 주문표가 서버에 저장된 계획과 다릅니다 — 새로 고친 뒤 다시 접수하세요"})
            failed += 1
            continue
        if key in active:
            items.append({**pending, "status": "duplicate", "status_ko": STATUS_KO["duplicate"],
                          "message": "같은 줄의 예약주문이 이미 접수돼 있습니다"})
            continue
        row = BrokerOrder(portfolio_id=pid, broker_credential_id=cred.id, plan_date=body.date, line_key=key,
                          code=code, instrument=ln.instrument, kind=ln.kind, side=ln.side, otype=ln.otype,
                          qty=ln.qty, price=price)
        try:
            r = client.reserve_order(code, ln.side, ln.qty, price)
            row.rsvn_ord_seq = r["rsvn_ord_seq"] or None
            row.status = "reserved"
            row.message = r["msg"] or None
            row.response = r["raw"] if isinstance(r["raw"], dict) else {}
            active.add(key)
            ok += 1
            logger.info("reserved order pid=%s %s %s %s x%s @%s seq=%s", pid, key, code, ln.side, ln.qty, price, row.rsvn_ord_seq)
        except Exception as exc:  # noqa: BLE001 — 줄 단위 실패는 기록하고 다음 줄 계속
            row.status = "failed"
            row.message = humanize_kis_error(str(exc)[:200])
            failed += 1
            logger.warning("reserve failed pid=%s %s: %s", pid, key, exc)
        session.add(row)
        session.flush()
        items.append(_order_out(row))
    session.commit()
    return {"date": body.date.isoformat(), "reserved": ok, "failed": failed, "items": items}


@router.post("/portfolio/{pid}/orders/{oid}/cancel")
def cancel_broker_order(pid: int, oid: int, user_id: int = Depends(current_user_id),
                        session: Session = Depends(get_session)) -> dict:
    """접수된 예약주문 취소 (정정은 없음 — 취소 후 재접수)."""
    _owned(session, pid, user_id)
    row = session.get(BrokerOrder, oid)
    if row is None or row.portfolio_id != pid:
        raise HTTPException(status_code=404, detail="order not found")
    if row.status not in ("reserved", "partial"):
        raise HTTPException(status_code=409, detail=f"취소할 수 없는 상태입니다 ({STATUS_KO.get(row.status, row.status)})")
    cred = _cred(session, pid, user_id)
    created = row.created_at or datetime.now(KST)
    ord_dt = (created.astimezone(KST) if created.tzinfo else created).date()
    try:
        r = _client(cred).cancel_reserved_order(row.rsvn_ord_seq or "", ord_dt,
                                                orgno=str((row.response or {}).get("RSVN_ORD_ORGNO") or ""))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"예약주문 취소 실패 — {humanize_kis_error(str(exc)[:200])}")
    row.status = "cancelled"
    row.message = r["msg"] or "취소됨"
    session.commit()
    return _order_out(row)


def run_post_close_sync(session: Session, now: datetime | None = None) -> dict:
    """장 마감 후 자동 동기화 (2026-09-05 지시 3항) — 워커 15:45 (17:10 재시도, 멱등).

    연결 계좌마다: ① 당일(2일치) 체결 가져오기 → 원장 재생으로 보유·수익률·통계 갱신,
    ② 예약주문 상태 확정(체결/일부/미체결/취소), ③ 연결된 매매일지 체결 가져오기. 계좌별 실패는 기록만 하고 계속.
    """
    from app.mjournal import import_journal_fills_for
    from app.models import ManualJournal, TradePortfolio

    now = now or datetime.now(KST)
    today = now.date()
    out: dict = {"date": today.isoformat(), "portfolios": [], "journals": []}
    for pf in session.scalars(select(TradePortfolio).where(TradePortfolio.broker_credential_id.is_not(None))).all():
        cred = session.get(BrokerCredential, pf.broker_credential_id)
        if cred is None or cred.env != "prod":
            continue
        rec: dict = {"portfolio_id": pf.id, "name": pf.name}
        try:
            r = import_fills_for_portfolio(session, pf.id, cred, days=2, dry_run=False)
            rec.update(fetched=r["fetched"], added=r["added"], skipped=r["skipped"])
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            rec["error"] = str(exc)[:200]
        try:
            rows = session.scalars(select(BrokerOrder).where(
                BrokerOrder.portfolio_id == pf.id, BrokerOrder.status.in_(("reserved", "partial")))).all()
            rec["orders_changed"] = sync_orders(session, cred, rows, today, now)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            rec["orders_error"] = str(exc)[:200]
        out["portfolios"].append(rec)
    for j in session.scalars(select(ManualJournal).where(ManualJournal.broker_credential_id.is_not(None))).all():
        cred = session.get(BrokerCredential, j.broker_credential_id)
        if cred is None or cred.env != "prod":
            continue
        rec = {"journal_id": j.id, "name": j.name}
        try:
            r = import_journal_fills_for(session, j, cred, days=2, dry_run=False)
            rec.update(fetched=r["fetched"], added=r["added"], skipped=r["skipped"])
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            rec["error"] = str(exc)[:200]
        out["journals"].append(rec)
    logger.info("post-close sync: %s", out)
    return out
