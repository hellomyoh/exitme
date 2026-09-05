"""수동 주식 매매일지 API (2026-09-05 지시).

일지 생성 시 이름·종목·증권사·요율을 받고, 매일 입력은 구분·수량·단가(+날짜·이유)만.
실현손익·수익률·보유기간·비용·합계는 FIFO 로 서버가 계산해 내려준다 (스프레드시트 대체).
규약: 매수 비용 = 매수금×수수료율(매수 행 비용), 매도 비용 = 매도금×(수수료율+제세금율),
실현손익 = 매도금 − 매도비용 − FIFO 매수원가(매수 수수료 미배분 — 행 비용으로 별도 표기).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.broker import _mask, humanize_kis_error
from app.db import get_session
from app.models import BrokerCredential, ManualJournal, ManualJournalEntry

logger = logging.getLogger(__name__)
router = APIRouter()
KST = timezone(timedelta(hours=9))


def _norm(s: str) -> str:
    """종목명 매칭 키 — 대소문자·공백 무시 ('kodex 200' ≡ 'KODEX 200'). 검토 문서 2-2."""
    return re.sub(r"\s+", "", s or "").lower()


def _owned(session: Session, jid: int, user_id: int) -> ManualJournal:
    j = session.get(ManualJournal, jid)
    if j is None or j.user_id != user_id:
        raise HTTPException(status_code=404, detail="journal not found")
    return j


class JournalIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    symbol: str = Field(min_length=1, max_length=60)
    broker: str = Field(default="", max_length=60)
    fee_rate: float = Field(default=0.00015, ge=0, le=0.05)   # 비율 (0.015% = 0.00015)
    tax_rate: float = Field(default=0.0023, ge=0, le=0.05)


class EntryIn(BaseModel):
    side: str = Field(pattern="^(buy|sell)$")
    qty: int = Field(gt=0)
    price: int = Field(gt=0)
    trade_date: date | None = None
    reason: str | None = Field(default=None, max_length=200)
    symbol: str | None = Field(default=None, max_length=60)  # 생략 = 일지 기본 종목 (0015)


@router.get("/mjournals")
def list_journals(user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> dict:
    rows = session.scalars(select(ManualJournal).where(ManualJournal.user_id == user_id)
                           .order_by(ManualJournal.id)).all()
    return {"items": [{"id": r.id, "name": r.name, "symbol": r.symbol, "broker": r.broker,
                       "closed_at": r.closed_at.isoformat() if r.closed_at else None} for r in rows]}


@router.post("/mjournals", status_code=201)
def create_journal(body: JournalIn, user_id: int = Depends(current_user_id),
                   session: Session = Depends(get_session)) -> dict:
    j = ManualJournal(user_id=user_id, name=body.name.strip(), symbol=body.symbol.strip(),
                      broker=body.broker.strip(), fee_rate=body.fee_rate, tax_rate=body.tax_rate)
    session.add(j)
    session.commit()
    return {"id": j.id, "name": j.name}


@router.delete("/mjournals/{jid}")
def delete_journal(jid: int, user_id: int = Depends(current_user_id),
                   session: Session = Depends(get_session)) -> dict:
    j = _owned(session, jid, user_id)
    session.delete(j)  # 항목은 FK CASCADE
    session.commit()
    return {"deleted": True}


def _compute(j: ManualJournal, entries: list[ManualJournalEntry]) -> dict:
    """종목별 FIFO (0015) — entry.symbol 이 없으면 일지 기본 종목으로 귀속."""
    fee, tax = float(j.fee_rate), float(j.tax_rate)
    lots_by: dict[str, list[dict]] = {}
    rows: list[dict] = []
    total_buy = total_sell = total_cost = total_realized = total_matched = 0
    realized_by: dict[str, int] = {}
    matched_by: dict[str, int] = {}
    # 종목별 누적 실현손익 추이 (2026-09-05 지시: 현황 그래프는 이 일지 것만) — 같은 날 다건은 마지막 누적값
    series_by: dict[str, dict[str, int]] = {}
    for e in sorted(entries, key=lambda x: (x.trade_date, x.id)):
        sym = (e.symbol or j.symbol).strip()
        lots = lots_by.setdefault(sym, [])
        amount = e.qty * e.price
        base = {"source": "broker" if e.broker_ref else "manual", "code": e.code}  # 증권사 가져오기 표시 (0018)
        if e.side == "buy":
            cost = round(amount * fee)
            lots.append({"qty": e.qty, "price": e.price, "date": e.trade_date})
            total_buy += amount
            total_cost += cost
            rows.append({**base, "id": e.id, "symbol": sym, "side": "buy", "buy_date": e.trade_date.isoformat(),
                         "sell_date": None, "hold_days": None, "realized": None, "return_pct": None,
                         "price": e.price, "qty": e.qty, "cost": cost, "amount": amount,
                         "reason": e.reason})
        else:
            held = sum(l["qty"] for l in lots)
            if e.qty > held:
                rows.append({**base, "id": e.id, "symbol": sym, "side": "sell", "buy_date": None,
                             "sell_date": e.trade_date.isoformat(), "hold_days": None,
                             "realized": None, "return_pct": None, "price": e.price, "qty": e.qty,
                             "cost": None, "amount": amount, "reason": e.reason,
                             "error": f"보유({held}주)보다 많은 매도"})
                continue
            remaining = e.qty
            matched_cost = 0
            first_date = None
            for l in lots:
                if remaining <= 0:
                    break
                take = min(l["qty"], remaining)
                if take > 0 and first_date is None:
                    first_date = l["date"]
                matched_cost += take * l["price"]
                l["qty"] -= take
                remaining -= take
            lots_by[sym] = [l for l in lots if l["qty"] > 0]
            cost = round(amount * (fee + tax))
            realized = amount - cost - matched_cost
            total_sell += amount
            total_cost += cost
            total_realized += realized
            total_matched += matched_cost
            realized_by[sym] = realized_by.get(sym, 0) + realized
            matched_by[sym] = matched_by.get(sym, 0) + matched_cost
            series_by.setdefault(sym, {})[e.trade_date.isoformat()] = realized_by[sym]
            rows.append({**base, "id": e.id, "symbol": sym, "side": "sell",
                         "buy_date": first_date.isoformat() if first_date else None,
                         "sell_date": e.trade_date.isoformat(),
                         "hold_days": (e.trade_date - first_date).days if first_date else None,
                         "realized": realized,
                         "return_pct": (realized / matched_cost) if matched_cost > 0 else None,
                         "price": e.price, "qty": e.qty, "cost": cost, "amount": amount,
                         "reason": e.reason})
    holdings = []
    for sym, lots in lots_by.items():
        q = sum(l["qty"] for l in lots)
        if q > 0:
            c = sum(l["qty"] * l["price"] for l in lots)
            m = matched_by.get(sym, 0)
            holdings.append({"symbol": sym, "qty": q, "avg_price": round(c / q), "cost": c,
                             "realized": realized_by.get(sym, 0), "matched": m,
                             "return_pct": (realized_by.get(sym, 0) / m) if m > 0 else None})
    holdings.sort(key=lambda h: -h["cost"])
    symbols = sorted({(e.symbol or j.symbol).strip() for e in entries} | {j.symbol.strip()})
    return {
        "rows": list(reversed(rows)),  # 최신이 위
        "summary": {"realized": total_realized, "sell_amount": total_sell,
                    "buy_amount": total_buy, "cost": total_cost,
                    "matched_cost": total_matched,
                    "return_pct": (total_realized / total_matched) if total_matched > 0 else None},
        "holdings": holdings, "symbols": symbols,
        "series": {sym: [{"date": d, "value": v} for d, v in sorted(m.items())]
                   for sym, m in series_by.items()},
    }


@router.get("/mjournals/{jid}")
def get_journal(jid: int, user_id: int = Depends(current_user_id),
                session: Session = Depends(get_session)) -> dict:
    j = _owned(session, jid, user_id)
    entries = session.scalars(select(ManualJournalEntry)
                              .where(ManualJournalEntry.journal_id == jid)).all()
    c = enrich_valuation(session, j, entries, _compute(j, entries))  # 현재가 평가 (2026-09-06)
    return {"id": j.id, "name": j.name, "symbol": j.symbol, "broker": j.broker,
            "fee_rate": float(j.fee_rate), "tax_rate": float(j.tax_rate),
            "linked_account": _linked_out(session, j),
            "closed_at": j.closed_at.isoformat() if j.closed_at else None,
            **c}


@router.post("/mjournals/{jid}/entries", status_code=201)
def add_entry(jid: int, body: EntryIn, user_id: int = Depends(current_user_id),
              session: Session = Depends(get_session)) -> dict:
    j = _owned(session, jid, user_id)
    if j.closed_at is not None:
        raise HTTPException(status_code=409, detail="청산된 일지입니다 — 기록을 추가하려면 먼저 '다시 열기'를 하세요")
    d = body.trade_date or date.today()
    sym = (body.symbol or j.symbol).strip()
    if body.side == "sell":
        entries = session.scalars(select(ManualJournalEntry)
                                  .where(ManualJournalEntry.journal_id == jid)).all()
        held = 0
        for e in entries:
            if e.trade_date <= d and (e.symbol or j.symbol).strip() == sym:
                held += e.qty if e.side == "buy" else -e.qty
        if body.qty > held:
            raise HTTPException(status_code=422,
                                detail=f"{sym} 매도 수량({body.qty})이 해당일 보유({max(held, 0)}주)를 초과합니다")
    session.add(ManualJournalEntry(journal_id=j.id, side=body.side, trade_date=d,
                                   qty=body.qty, price=body.price, symbol=sym,
                                   reason=(body.reason or "").strip() or None))
    session.commit()
    return {"saved": True}


@router.delete("/mjournals/{jid}/entries/{eid}")
def delete_entry(jid: int, eid: int, user_id: int = Depends(current_user_id),
                 session: Session = Depends(get_session)) -> dict:
    _owned(session, jid, user_id)
    e = session.get(ManualJournalEntry, eid)
    if e is None or e.journal_id != jid:
        raise HTTPException(status_code=404, detail="entry not found")
    session.delete(e)
    session.commit()
    return {"deleted": True}


# ── 증권사 계좌 연결 + 체결 가져오기 (0018, 2026-09-05 지시) ───────────────────────────────
# 검토: THROUGHLINE/docs/mjournal-broker-link-review-20260905.md
# 원칙: 조회 전용(주문 TR 미사용), 수동 기록 자동 수정 금지 — 경고만. 가져온 행은 broker_ref 로 표시하고 삭제 가능.


def _linked_out(session: Session, j: ManualJournal) -> dict | None:
    if not j.broker_credential_id:
        return None
    c = session.get(BrokerCredential, j.broker_credential_id)
    if c is None or c.user_id != j.user_id:
        return None
    return {"id": c.id, "label": c.label or _mask(c.account_no),
            "account_no": f"{_mask(c.account_no, 4, 2)}-{c.acnt_prdt_cd}", "env": c.env}


class BrokerLinkIn(BaseModel):
    credential_id: int | None = None  # None = 연결 해제


@router.get("/mjournals/{jid}/broker")
def journal_broker(jid: int, user_id: int = Depends(current_user_id),
                   session: Session = Depends(get_session)) -> dict:
    j = _owned(session, jid, user_id)
    acct = _linked_out(session, j)
    return {"linked": acct is not None, "account": acct}


@router.put("/mjournals/{jid}/broker")
def link_journal_broker(jid: int, body: BrokerLinkIn, user_id: int = Depends(current_user_id),
                        session: Session = Depends(get_session)) -> dict:
    """설정에 등록된 계좌를 일지에 연결/해제 — 일지 1 : 계좌 1 (한 계좌를 여러 일지에 연결하는 것은 허용)."""
    j = _owned(session, jid, user_id)
    if body.credential_id is None:
        j.broker_credential_id = None
    else:
        c = session.get(BrokerCredential, body.credential_id)
        if c is None or c.user_id != user_id:
            raise HTTPException(status_code=404, detail="account not found")
        j.broker_credential_id = c.id
    session.commit()
    acct = _linked_out(session, j)
    return {"linked": acct is not None, "account": acct}


@router.post("/mjournals/{jid}/import-fills")
def import_journal_fills(jid: int, days: int = 30, dry_run: bool = True,
                         user_id: int = Depends(current_user_id),
                         session: Session = Depends(get_session)) -> dict:
    """연결 계좌의 체결을 일지 항목으로 가져온다 (기본은 미리보기).

    - 종목 매칭 3단계(검토 2-2): 기존 행의 종목코드 → 정규화 종목명(대소문자·공백 무시) → KIS 종목명으로 새 종목
    - 멱등: broker_ref="주문번호:일자" 가 이미 있으면 건너뜀
    - 경고만(검토 2-3·2-6): 해당일 보유 초과 매도, 같은 날 같은 종목·수량·단가의 수동 기록 → 등록은 하되 경고 표시
    - 비용은 일지 요율로 추정(검토 2-4) — 체결 응답에 수수료·세금이 없다
    """
    j = _owned(session, jid, user_id)
    cred = session.get(BrokerCredential, j.broker_credential_id) if j.broker_credential_id else None
    if cred is None or cred.user_id != user_id:
        raise HTTPException(status_code=409, detail="이 일지에 연결된 증권사 계좌가 없습니다 — 아래에서 계좌를 연결하세요")
    try:
        return import_journal_fills_for(session, j, cred, days=days, dry_run=dry_run)
    except _FetchFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc))


class _FetchFailed(RuntimeError):
    pass


def import_journal_fills_for(session: Session, j: ManualJournal, cred: BrokerCredential,
                             days: int = 30, dry_run: bool = True) -> dict:
    """체결 가져오기 본체 — 화면(라우트)과 장 마감 후 배치(app.broker.run_post_close_sync)가 같이 쓴다."""
    from app.services.kis_auth import KisAuth
    from app.services.kis_client import KisTradingClient

    jid = j.id
    end = datetime.now(KST).date()
    start = end - timedelta(days=max(1, min(days, 365)) - 1)
    try:
        auth = KisAuth(cred.app_key, cred.app_secret, cred.env, wait_on_rate_limit=False)
        execs = KisTradingClient(auth, cano=cred.account_no, acnt_prdt_cd=cred.acnt_prdt_cd) \
            .fetch_executions(start, end)
    except Exception as exc:  # noqa: BLE001 — 자격·유량 등 사유를 그대로 안내
        logger.warning("journal import-fills failed jid=%s: %s", jid, exc)
        raise _FetchFailed(f"증권사 조회 실패 — {humanize_kis_error(str(exc)[:200])}")

    entries = session.scalars(select(ManualJournalEntry)
                              .where(ManualJournalEntry.journal_id == jid)).all()

    def sym_of(e: ManualJournalEntry) -> str:
        return (e.symbol or j.symbol).strip()

    known = {e.broker_ref for e in entries if e.broker_ref}
    by_code = {e.code: sym_of(e) for e in entries if e.code}
    by_norm = {_norm(x): x for x in ({sym_of(e) for e in entries} | {j.symbol.strip()})}
    manual_keys = {(e.trade_date, _norm(sym_of(e)), e.side, e.qty, e.price) for e in entries if not e.broker_ref}
    batch: list[tuple[str, date, int]] = []  # 이번 배치에서 (미리보기 포함) 반영된 수량 — 보유 초과 판정용

    def position(sym: str, d: date) -> int:
        pos = sum((e.qty if e.side == "buy" else -e.qty) for e in entries
                  if sym_of(e) == sym and e.trade_date <= d)
        return pos + sum(q for (sy, dd, q) in batch if sy == sym and dd <= d)

    items, added, skipped, new_syms = [], 0, 0, set()
    for e in sorted(execs, key=lambda x: (x.trade_date, x.order_no)):
        ref = f"{e.order_no}:{e.trade_date.isoformat()}"
        if e.code in by_code:
            sym, how = by_code[e.code], "코드"
        elif _norm(e.name) in by_norm:
            sym, how = by_norm[_norm(e.name)], "이름"
        else:
            sym, how = (e.name.strip() or e.code), "새 종목"
        row = {"broker_ref": ref, "date": e.trade_date.isoformat(), "code": e.code, "name": e.name,
               "symbol": sym, "match": how, "side": e.side, "qty": e.filled_qty, "price": e.avg_price,
               "amount": e.filled_qty * e.avg_price, "warnings": []}
        if ref in known:
            row["status"] = "이미 등록됨"
            skipped += 1
            items.append(row)
            continue
        if e.side == "sell":
            pos = position(sym, e.trade_date)
            if e.filled_qty > pos:
                row["warnings"].append(f"해당일 보유({max(pos, 0)}주)보다 많은 매도 — 가져오기 이전 매수분이 있으면 "
                                       "기초 보유(매수 1건)를 먼저 등록하세요")
        if (e.trade_date, _norm(sym), e.side, e.filled_qty, e.avg_price) in manual_keys:
            row["warnings"].append("같은 날 같은 종목·수량·단가의 수동 기록이 있습니다 — 중복이면 둘 중 하나를 삭제하세요")
        if how == "새 종목":
            new_syms.add(sym)
        row["status"] = "등록 예정" if dry_run else "등록됨"
        if not dry_run:
            session.add(ManualJournalEntry(journal_id=j.id, side=e.side, trade_date=e.trade_date,
                                           qty=e.filled_qty, price=e.avg_price, symbol=sym, code=e.code,
                                           broker_ref=ref, reason="증권사 자동 가져오기"))
            added += 1
        batch.append((sym, e.trade_date, e.filled_qty if e.side == "buy" else -e.filled_qty))
        by_code.setdefault(e.code, sym)
        by_norm.setdefault(_norm(sym), sym)
        items.append(row)
    if not dry_run:
        cred.last_import_at = datetime.now(KST)
        session.commit()
    return {"range": [start.isoformat(), end.isoformat()], "dry_run": dry_run,
            "fetched": len(execs), "added": added, "skipped": skipped,
            "new_symbols": sorted(new_syms), "items": items}


# ── 청산 (0020, 2026-09-05 지시) ───────────────────────────────────────────────────
# 전량 매도했거나 더 이상 거래하지 않는 일지는 청산으로 표시한다. 기록은 보존되고 조회 가능하지만
# 새 기록을 받지 않으며 대시보드(매매일지 자산·총자산)에서 빠진다. 되돌리기 = 다시 열기.


@router.post("/mjournals/{jid}/close")
def close_journal(jid: int, user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> dict:
    j = _owned(session, jid, user_id)
    entries = session.scalars(select(ManualJournalEntry).where(ManualJournalEntry.journal_id == jid)).all()
    held = _compute(j, entries)["holdings"]
    j.closed_at = datetime.now(KST)
    session.commit()
    return {"closed_at": j.closed_at.isoformat(),
            "warning": (f"보유 잔여 {len(held)}종목이 남아 있습니다 — 실제로 전량 매도했다면 매도 기록을 먼저 넣는 것이 정확합니다"
                        if held else None)}


@router.post("/mjournals/{jid}/reopen")
def reopen_journal(jid: int, user_id: int = Depends(current_user_id),
                   session: Session = Depends(get_session)) -> dict:
    j = _owned(session, jid, user_id)
    j.closed_at = None
    session.commit()
    return {"closed_at": None}


def journal_assets(session: Session, user_id: int) -> list[dict]:
    """대시보드용 매매일지 자산 (2026-09-05 지시 ②) — 진행 중 일지만, 보유는 **취득원가** 평가(시세 미연동).

    같은 증권사 계좌가 실전매매 포트에도 연결돼 있으면 두 번 세지 않도록 counted=False 로 표시만 한다.
    """
    from app.models import TradePortfolio

    linked_by_ports = {p.broker_credential_id for p in session.scalars(
        select(TradePortfolio).where(TradePortfolio.user_id == user_id,
                                     TradePortfolio.broker_credential_id.is_not(None))).all()}
    out = []
    for j in session.scalars(select(ManualJournal).where(ManualJournal.user_id == user_id,
                                                         ManualJournal.closed_at.is_(None))
                             .order_by(ManualJournal.id)).all():
        entries = session.scalars(select(ManualJournalEntry).where(ManualJournalEntry.journal_id == j.id)).all()
        c = enrich_valuation(session, j, entries, _compute(j, entries))  # 현재가 평가 (2026-09-06)
        cost = sum(h["cost"] for h in c["holdings"])
        s = c["summary"]
        # value = 총자산 합산에 쓰는 값: 전 종목 현재가가 있으면 평가액, 아니면 취득원가(시세 미연동 종목 보호)
        value = s["eval_total"] if s["priced"] else cost
        dup = j.broker_credential_id is not None and j.broker_credential_id in linked_by_ports
        out.append({"id": j.id, "name": j.name, "symbol": j.symbol, "cost": cost,
                    "value": value, "priced": s["priced"],
                    "unrealized": s["unrealized_total"] if s["priced"] else None,
                    "unrealized_pct": s["unrealized_pct"] if s["priced"] else None,
                    "realized": s["realized"], "return_pct": s["return_pct"],
                    "holdings": [{"symbol": h["symbol"], "qty": h["qty"], "cost": h["cost"],
                                  "price": h.get("price"), "eval": h.get("eval")} for h in c["holdings"]],
                    "entries": len(entries), "counted": not dup,
                    "note": "실전매매 포트와 같은 증권사 계좌 — 총자산에는 실전매매 쪽만 포함" if dup else None})
    return out


# ── 잔고 기준 기초 보유 등록 (2026-09-05 지시) ─────────────────────────────────────
# "체결 가져오기"는 기간 안의 체결만 가져오므로 그 전에 산 보유분(예: 삼성전자 11주)은 나오지 않는다.
# 검토 문서 2-3 의 권고대로 잔고 API 로 현재 보유를 읽어 부족분만 '기초 보유' 매수 행으로 넣는다.
# 잔고 평단은 이동평균이라 FIFO 로트로는 근사 — 실제 매수일·가격이 필요하면 체결 기간을 늘려 체결로 넣는 것이 정확.


def _symbol_matcher(j: ManualJournal, entries: list[ManualJournalEntry]):
    """종목 매칭 3단계(코드 → 정규화 이름 → 새 종목) — 체결 가져오기와 같은 규칙."""
    def sym_of(e: ManualJournalEntry) -> str:
        return (e.symbol or j.symbol).strip()

    by_code = {e.code: sym_of(e) for e in entries if e.code}
    by_norm = {_norm(x): x for x in ({sym_of(e) for e in entries} | {j.symbol.strip()})}

    def match(code: str, name: str) -> tuple[str, str]:
        if code in by_code:
            return by_code[code], "코드"
        if _norm(name) in by_norm:
            return by_norm[_norm(name)], "이름"
        return (name.strip() or code), "새 종목"
    return match


def _position_by_symbol(j: ManualJournal, entries: list[ManualJournalEntry]) -> dict[str, int]:
    pos: dict[str, int] = {}
    for e in entries:
        sym = (e.symbol or j.symbol).strip()
        pos[sym] = pos.get(sym, 0) + (e.qty if e.side == "buy" else -e.qty)
    return pos


def _cred_for_journal(session: Session, j: ManualJournal, user_id: int) -> BrokerCredential:
    cred = session.get(BrokerCredential, j.broker_credential_id) if j.broker_credential_id else None
    if cred is None or cred.user_id != user_id:
        raise HTTPException(status_code=409, detail="이 일지에 연결된 증권사 계좌가 없습니다 — 아래에서 계좌를 연결하세요")
    return cred


@router.get("/mjournals/{jid}/broker-holdings")
def journal_broker_holdings(jid: int, user_id: int = Depends(current_user_id),
                            session: Session = Depends(get_session)) -> dict:
    """연결 계좌의 현재 잔고와 일지 보유를 종목별로 대조 — 부족분(diff)이 등록 제안 수량."""
    from app.services.kis_auth import KisAuth
    from app.services.kis_client import KisTradingClient

    j = _owned(session, jid, user_id)
    cred = _cred_for_journal(session, j, user_id)
    try:
        auth = KisAuth(cred.app_key, cred.app_secret, cred.env, wait_on_rate_limit=False)
        rows = KisTradingClient(auth, cano=cred.account_no, acnt_prdt_cd=cred.acnt_prdt_cd).fetch_holdings()
    except Exception as exc:  # noqa: BLE001
        logger.warning("journal broker-holdings failed jid=%s: %s", jid, exc)
        raise HTTPException(status_code=502, detail=f"증권사 조회 실패 — {humanize_kis_error(str(exc)[:200])}")
    entries = session.scalars(select(ManualJournalEntry).where(ManualJournalEntry.journal_id == jid)).all()
    match = _symbol_matcher(j, entries)
    pos = _position_by_symbol(j, entries)
    items = []
    for r in rows:
        sym, how = match(r["code"], r["name"])
        jq = pos.get(sym, 0)
        items.append({**r, "symbol": sym, "match": how, "journal_qty": jq, "diff": max(r["qty"] - jq, 0)})
    items.sort(key=lambda x: -x["eval_amount"])
    return {"date": datetime.now(KST).date().isoformat(), "items": items,
            "note": "계좌 평단(이동평균)·등록일 기준 매수 1건으로 넣는 근사입니다. 실제 매수일·가격이 필요하면 "
                    "'체결 가져오기' 기간을 늘려 체결로 넣는 것이 정확합니다."}


class HoldingIn(BaseModel):
    code: str = Field(min_length=1, max_length=12)
    name: str = Field(default="", max_length=60)
    qty: int = Field(gt=0)
    price: int = Field(gt=0)


class ImportHoldingsIn(BaseModel):
    items: list[HoldingIn] = Field(min_length=1, max_length=100)
    trade_date: date | None = None   # 생략 = 오늘


@router.post("/mjournals/{jid}/import-holdings", status_code=201)
def import_journal_holdings(jid: int, body: ImportHoldingsIn, user_id: int = Depends(current_user_id),
                            session: Session = Depends(get_session)) -> dict:
    """선택한 잔고를 '기초 보유' 매수 행으로 등록. 같은 날 같은 종목은 한 번만(broker_ref=bal:코드:일자)."""
    j = _owned(session, jid, user_id)
    if j.closed_at is not None:
        raise HTTPException(status_code=409, detail="청산된 일지입니다 — 먼저 '다시 열기'를 하세요")
    d = body.trade_date or datetime.now(KST).date()
    entries = session.scalars(select(ManualJournalEntry).where(ManualJournalEntry.journal_id == jid)).all()
    match = _symbol_matcher(j, entries)
    known = {e.broker_ref for e in entries if e.broker_ref}
    added = skipped = 0
    items = []
    for it in body.items:
        ref = f"bal:{it.code}:{d.isoformat()}"
        sym, how = match(it.code, it.name)
        row = {"code": it.code, "symbol": sym, "match": how, "qty": it.qty, "price": it.price, "date": d.isoformat()}
        if ref in known:
            skipped += 1
            items.append({**row, "status": "이미 등록됨"})
            continue
        session.add(ManualJournalEntry(journal_id=j.id, side="buy", trade_date=d, qty=it.qty, price=it.price,
                                       symbol=sym, code=it.code, broker_ref=ref, reason="증권사 잔고 기초 보유"))
        known.add(ref)
        added += 1
        items.append({**row, "status": "등록됨"})
    session.commit()
    return {"added": added, "skipped": skipped, "date": d.isoformat(), "items": items}


# ── 현재가 평가 (2026-09-06 지시: 매수가 vs 현재가 수익률) ────────────────────────────
# 시세 출처 우선순위: ① 연결 계좌 잔고의 현재가(prpr, 종목코드 → 이름 순 매칭) ② 우리 DB 일봉 종가(코드가 있는 종목)
# ③ 없으면 None(취득원가로만 표시). 잔고 조회는 자격별 120초 캐시 — 화면·대시보드·배치가 KIS 를 반복 호출하지 않게.
_PRICE_CACHE: dict[int, tuple[float, dict]] = {}
PRICE_TTL_SEC = 120.0


def _broker_price_map(session: Session, j: ManualJournal) -> dict:
    import time

    if not j.broker_credential_id:
        return {}
    cred = session.get(BrokerCredential, j.broker_credential_id)
    if cred is None or cred.user_id != j.user_id:
        return {}
    now = time.monotonic()
    hit = _PRICE_CACHE.get(cred.id)
    if hit and now - hit[0] < PRICE_TTL_SEC:
        return hit[1]
    try:
        from app.services.kis_auth import KisAuth
        from app.services.kis_client import KisTradingClient

        rows = KisTradingClient(KisAuth(cred.app_key, cred.app_secret, cred.env, wait_on_rate_limit=False),
                                cano=cred.account_no, acnt_prdt_cd=cred.acnt_prdt_cd).fetch_holdings()
    except Exception as exc:  # noqa: BLE001 — 시세는 보조 정보, 실패해도 일지는 떠야 한다
        logger.warning("journal price lookup failed cred=%s: %s", cred.id, exc)
        return hit[1] if hit else {}
    m: dict = {}
    for r in rows:
        m[r["code"]] = r
        m["name:" + _norm(r["name"])] = r
    _PRICE_CACHE[cred.id] = (now, m)
    return m


def _close_price_map(session: Session, codes: set[str]) -> dict[str, int]:
    from app.models import Instrument, OhlcvDaily

    out: dict[str, int] = {}
    for code in codes:
        inst = session.scalar(select(Instrument).where(Instrument.code == code))
        if inst is None:
            continue
        row = session.scalar(select(OhlcvDaily).where(OhlcvDaily.instrument_id == inst.id)
                             .order_by(OhlcvDaily.trade_date.desc()).limit(1))
        if row is not None:
            out[code] = int(row.close_raw)
    return out


def enrich_valuation(session: Session, j: ManualJournal, entries: list[ManualJournalEntry], computed: dict) -> dict:
    """holdings 에 code·price·price_source·eval·unrealized·unrealized_pct, summary 에
    eval_total·unrealized_total·unrealized_pct(가격 있는 종목 원가 대비)·total_pnl(실현+평가)·priced·priced_count."""
    code_of: dict[str, str] = {}
    for e in entries:
        if e.code:
            code_of.setdefault((e.symbol or j.symbol).strip(), e.code)
    broker = _broker_price_map(session, j) if computed["holdings"] else {}
    need = {code_of[s] for s in code_of if code_of[s] not in broker}
    closes = _close_price_map(session, need) if need else {}
    eval_total = unreal_total = cost_priced = 0
    for h in computed["holdings"]:
        code = code_of.get(h["symbol"])
        r = broker.get(code) if code else None
        if r is None:
            r = broker.get("name:" + _norm(h["symbol"]))
        px, src = (int(r["price"]), "증권사 잔고") if r and r.get("price") else (None, None)
        if px is None and code and code in closes:
            px, src = closes[code], "종가"
        h["code"], h["price"], h["price_source"] = code, px, src
        if px is None:
            h["eval"] = h["unrealized"] = h["unrealized_pct"] = None
            continue
        ev = px * h["qty"]
        un = ev - h["cost"]
        h["eval"], h["unrealized"] = ev, un
        h["unrealized_pct"] = (un / h["cost"]) if h["cost"] > 0 else None
        eval_total += ev
        unreal_total += un
        cost_priced += h["cost"]
    s = computed["summary"]
    priced_count = sum(1 for h in computed["holdings"] if h["price"] is not None)
    s["eval_total"] = eval_total
    s["unrealized_total"] = unreal_total
    s["unrealized_pct"] = (unreal_total / cost_priced) if cost_priced > 0 else None
    s["total_pnl"] = s["realized"] + unreal_total
    s["priced"] = bool(computed["holdings"]) and priced_count == len(computed["holdings"])
    s["priced_count"] = priced_count
    return computed
