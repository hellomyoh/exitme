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
    code: str | None = Field(default=None, pattern="^[0-9A-Z]{6}$")  # 종목코드(선택) — 있으면 시세·수익률 라인에 연결 (2026-09-06)


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
                                   reason=(body.reason or "").strip() or None, code=body.code))
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


def _port_holdings_by_cred(session: Session, user_id: int) -> dict[int, dict[str, str]]:
    """증권사 계좌(자격)별로, 그 계좌를 연결한 실전매매 포트가 **실제 보유 중인** 종목 {code: name} (2026-09-06)."""
    from app.models import Instrument, PositionLot, TradePortfolio

    out: dict[int, dict[str, str]] = {}
    ports = session.scalars(select(TradePortfolio).where(TradePortfolio.user_id == user_id,
                                                         TradePortfolio.broker_credential_id.is_not(None))).all()
    for p in ports:
        held = out.setdefault(p.broker_credential_id, {})
        for lot in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == p.id)).all():
            if lot.qty_open > 0:
                inst = session.get(Instrument, lot.instrument_id)
                if inst is not None:
                    held[inst.code] = inst.name
    return out


def journal_assets(session: Session, user_id: int) -> list[dict]:
    """대시보드용 매매일지 자산 (2026-09-05 지시 ②, 2026-09-06 종목 단위 중복 제외) — 진행 중 일지만.

    같은 증권사 계좌를 실전매매 포트도 쓰고 있으면 **그 포트가 실제로 보유한 종목만** 일지에서 빼고 나머지는
    총자산에 넣는다. 계좌 단위로 통째로 빼던 이전 규칙은 포트가 현금만 들고 있을 때 일지의 주식 전부를
    누락시켰다(2026-09-06 발견: 매매일지 0원). 같은 주식을 두 번 세는 일은 종목 매칭(코드 → 정규화 이름)으로 막는다.
    """
    port_held = _port_holdings_by_cred(session, user_id)
    out = []
    for j in session.scalars(select(ManualJournal).where(ManualJournal.user_id == user_id,
                                                         ManualJournal.closed_at.is_(None))
                             .order_by(ManualJournal.id)).all():
        entries = session.scalars(select(ManualJournalEntry).where(ManualJournalEntry.journal_id == j.id)).all()
        c = enrich_valuation(session, j, entries, _compute(j, entries))  # 현재가 평가 (2026-09-06)
        cost = sum(h["cost"] for h in c["holdings"])
        s = c["summary"]
        held = port_held.get(j.broker_credential_id, {}) if j.broker_credential_id else {}
        held_norm = {_norm(n) for n in held.values()}
        included_value = 0
        excluded: list[dict] = []
        for h in c["holdings"]:
            # value 규약: 전 종목 현재가가 있으면 평가액, 아니면 취득원가(시세 미연동 종목 보호)
            hv = (h.get("eval") if s["priced"] else None) or h["cost"]
            overlap = (h.get("code") and h["code"] in held) or (_norm(h["symbol"]) in held_norm)
            if overlap:
                excluded.append({"symbol": h["symbol"], "code": h.get("code"), "value": hv})
            else:
                included_value += hv
        all_excluded = bool(c["holdings"]) and len(excluded) == len(c["holdings"])
        note = None
        if excluded:
            names = ", ".join(x["symbol"] for x in excluded)
            note = (f"실전매매 포트가 같은 계좌로 보유 중인 종목 제외: {names}"
                    + (" — 총자산에는 실전매매 쪽만 포함" if all_excluded else ""))
        out.append({"id": j.id, "name": j.name, "symbol": j.symbol, "cost": cost,
                    "value": included_value, "priced": s["priced"],
                    "unrealized": s["unrealized_total"] if s["priced"] else None,
                    "unrealized_pct": s["unrealized_pct"] if s["priced"] else None,
                    "realized": s["realized"], "return_pct": s["return_pct"],
                    "holdings": [{"symbol": h["symbol"], "qty": h["qty"], "cost": h["cost"],
                                  "price": h.get("price"), "eval": h.get("eval")} for h in c["holdings"]],
                    "entries": len(entries), "counted": not all_excluded, "excluded": excluded, "note": note})
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

        cli = KisTradingClient(KisAuth(cred.app_key, cred.app_secret, cred.env, wait_on_rate_limit=False),
                               cano=cred.account_no, acnt_prdt_cd=cred.acnt_prdt_cd)
        if hasattr(cli, "fetch_balance"):
            bal = cli.fetch_balance()
            rows, deposit = bal["holdings"], bal.get("deposit", 0)
        else:  # 구형 클라이언트(테스트 대역 포함) — 보유만
            rows, deposit = cli.fetch_holdings(), 0
    except Exception as exc:  # noqa: BLE001 — 시세는 보조 정보, 실패해도 일지는 떠야 한다
        logger.warning("journal price lookup failed cred=%s: %s", cred.id, exc)
        return hit[1] if hit else {}
    m: dict = {}
    for r in rows:
        m[r["code"]] = r
        m["name:" + _norm(r["name"])] = r
    m["__account__"] = {"deposit": deposit, "rows": rows}  # 계좌 단위 정보 (예수금·전체 보유)
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
    # 계좌 평가금액 (2026-09-06 지시) — 이 일지가 계좌의 주식을 '전부' 담고 있을 때만 의미가 있다.
    # 일지 ≠ 계좌인데 예수금을 더하면 계좌 총액도 일지 총액도 아닌 값이 되고, 한 계좌를 여러 일지에
    # 연결하면 중복된다. 그래서 커버리지(계좌 보유 = 일지 보유, 수량 일치)를 확인해 표시 여부를 정하고,
    # 대시보드 총자산에는 넣지 않는다(일지 화면 참고 값).
    acct = broker.get("__account__") if broker else None
    s["account_deposit"] = acct["deposit"] if acct else None
    covered = False
    if acct is not None:
        jr = {h["symbol"]: h for h in computed["holdings"]}
        seen: set[str] = set()
        covered = True
        for r in acct["rows"]:
            sym = None
            for h in computed["holdings"]:
                if (h.get("code") and h["code"] == r["code"]) or _norm(h["symbol"]) == _norm(r["name"]):
                    sym = h["symbol"]
                    break
            if sym is None or jr[sym]["qty"] != r["qty"]:
                covered = False
                break
            seen.add(sym)
        if covered and seen != set(jr):
            covered = False  # 계좌에 없는 종목이 일지에 있다 (수동 기록·다른 계좌 종목)
    s["account_covered"] = covered
    s["account_total"] = (eval_total + (acct["deposit"] or 0)) if (covered and acct) else None
    return computed


# ── 보유 평단 대비 일별 수익률 (2026-09-06 지시) ─────────────────────────────────────────────────

def _kis_for_bars(session: Session, j: ManualJournal):
    """일봉 보충용 KIS 클라이언트 — 일지 연결 계좌 키 → .env 시세 키 순. 없으면 None (시세 미연동 표기)."""
    from app.config import get_settings
    from app.services.kis_auth import KisAuth
    from app.services.kis_client import KisClient

    settings = get_settings()
    if j.broker_credential_id:
        cred = session.get(BrokerCredential, j.broker_credential_id)
        if cred is not None and cred.user_id == j.user_id:
            return KisClient(KisAuth(cred.app_key, cred.app_secret, cred.env, wait_on_rate_limit=False))
    if settings.kis_app_key and settings.kis_app_secret:
        return KisClient(KisAuth(settings.kis_app_key, settings.kis_app_secret, settings.kis_env, wait_on_rate_limit=False))
    return None


def _ensure_daily_bars(session: Session, j: ManualJournal, codes: dict[str, str], start: date, end: date):
    """codes: code → 표시 이름. DB 일봉이 구간을 못 덮으면 KIS 일봉으로 보충해 적재한다(이후는 16:05 일봉 배치가 이어 붙임).
    반환: (code → {일자: 종가}, 안내 문구들). 6자리 국내 코드만 다룬다."""
    from dataclasses import asdict

    from app.models import Instrument, OhlcvDaily
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars

    out: dict[str, dict[date, int]] = {}
    notes: list[str] = []
    client = None
    client_tried = False
    stale_before = end - timedelta(days=4)  # 주말·휴장 여유 — 이보다 오래된 마지막 봉이면 꼬리를 보충

    def load(inst_id: int):
        return session.scalars(select(OhlcvDaily).where(OhlcvDaily.instrument_id == inst_id,
                                                        OhlcvDaily.trade_date >= start, OhlcvDaily.trade_date <= end)
                               .order_by(OhlcvDaily.trade_date)).all()

    for code, name in codes.items():
        if not (len(code) == 6 and code.isalnum()):
            continue
        inst = session.scalar(select(Instrument).where(Instrument.code == code))
        rows = load(inst.id) if inst is not None else []
        need: list[tuple[date, date]] = []
        if not rows:
            need.append((start, end))
        else:
            if rows[0].trade_date > start + timedelta(days=10):
                need.append((start, rows[0].trade_date - timedelta(days=1)))
            if rows[-1].trade_date < stale_before:
                need.append((rows[-1].trade_date + timedelta(days=1), end))
        if need:
            if not client_tried:
                client_tried = True
                try:
                    client = _kis_for_bars(session, j)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("journal bars: kis client init failed: %s", exc)
                    client = None
                if client is None:
                    notes.append("시세 키가 없어 일봉을 보충하지 못했습니다 — 설정에서 증권사 계좌를 연결하거나 .env 에 KIS 키를 넣으세요")
            if client is not None:
                if inst is None:
                    inst = get_or_create_instrument(session, code, name or code, "KOSPI", type_="STOCK")
                for a, b in need:
                    try:
                        bars = client.fetch_daily(code, a, b)
                    except Exception as exc:  # noqa: BLE001 — 시세는 보조 정보, 실패해도 있는 구간은 그린다
                        logger.warning("journal bars: fetch_daily %s %s~%s failed: %s", code, a, b, exc)
                        notes.append(f"{name or code} 일봉 조회 실패 — 있는 구간만 표시")
                        continue
                    if bars:
                        upsert_daily_bars(session, inst.id, [asdict(x) for x in bars], source="kis")
                session.commit()
                rows = load(inst.id)
        out[code] = {r.trade_date: int(r.close_raw) for r in rows}
    return out, notes


def _fifo_apply(lots: list[list], e: ManualJournalEntry) -> list[list]:
    """lots: [qty, price, buy_date] — FIFO 매도 차감. 남은 로트의 가장 이른 매수일이 '보유 시작일'."""
    if e.side == "buy":
        lots.append([e.qty, e.price, e.trade_date])
        return lots
    rem = e.qty
    for l in lots:
        take = min(l[0], rem)
        l[0] -= take
        rem -= take
        if rem <= 0:
            break
    return [l for l in lots if l[0] > 0]


@router.get("/mjournals/{jid}/return-series")
def journal_return_series(jid: int, user_id: int = Depends(current_user_id),
                          session: Session = Depends(get_session)) -> dict:
    """보유 평단 대비 일별 수익률 — 종목별 구간(보유 중인 날만) + 일지 종합 (2026-09-06 지시).

    평단 = 그날 시점 FIFO 잔여 로트 원가 ÷ 수량 (일지 보유 카드의 평단과 같은 값). 추가 매수·부분 매도로 바뀐 날부터 반영.
    시세는 DB 일봉(부족분은 KIS 로 보충·적재), 마지막 점은 연결 계좌 현재가가 있으면 오늘 값으로 덧붙인다.
    """
    from app.dashboard import kst_today

    j = _owned(session, jid, user_id)
    entries = sorted(session.scalars(select(ManualJournalEntry).where(ManualJournalEntry.journal_id == jid)).all(),
                     key=lambda e: (e.trade_date, e.id))
    if not entries:
        return {"symbols": {}, "total": [], "priced": False, "notes": [], "asof": None}

    def sym_of(e: ManualJournalEntry) -> str:
        return (e.symbol or j.symbol).strip()

    code_of: dict[str, str] = {}
    for e in entries:
        if e.code:
            code_of.setdefault(sym_of(e), e.code)
    today = kst_today()
    start = entries[0].trade_date - timedelta(days=10)
    closes, notes = _ensure_daily_bars(session, j, {c: s for s, c in code_of.items()}, start, today)
    no_code = sorted({sym_of(e) for e in entries} - set(code_of))
    if no_code:
        notes.append("종목 코드가 없어 시세를 붙일 수 없는 종목: " + ", ".join(no_code))
    live = _broker_price_map(session, j)

    symbols: dict[str, dict] = {}
    total_num: dict[date, float] = {}   # 일자별 보유 평가액 합 (보유 중이고 그날 종가가 있는 종목만)
    total_den: dict[date, float] = {}   # 일자별 보유 원가 합
    for sym in sorted({sym_of(e) for e in entries}):
        code = code_of.get(sym)
        px = closes.get(code, {}) if code else {}
        ents = [e for e in entries if sym_of(e) == sym]
        days = sorted(d for d in px if d >= ents[0].trade_date)
        lots: list[list] = []
        k = 0
        segments: list[list[dict]] = []
        cur: list[dict] = []
        for d in days:
            while k < len(ents) and ents[k].trade_date <= d:
                lots = _fifo_apply(lots, ents[k])
                k += 1
            qty = sum(l[0] for l in lots)
            if qty <= 0:
                if cur:
                    segments.append(cur)
                    cur = []
                continue
            cost = sum(l[0] * l[1] for l in lots)
            avg = cost / qty
            c = px[d]
            cur.append({"date": d.isoformat(), "pct": c / avg - 1, "close": c, "avg": round(avg), "qty": qty})
            total_num[d] = total_num.get(d, 0.0) + qty * c
            total_den[d] = total_den.get(d, 0.0) + cost
        if cur:
            segments.append(cur)
        lots_now: list[list] = []
        for e in ents:
            lots_now = _fifo_apply(lots_now, e)
        qty_now = sum(l[0] for l in lots_now)
        avg_now = (sum(l[0] * l[1] for l in lots_now) / qty_now) if qty_now > 0 else None
        since = min(l[2] for l in lots_now).isoformat() if lots_now else None  # 현재 보유의 시작일(남은 로트 중 최초 매수일)
        current_pct = None
        live_row = (live.get(code) if code else None) or live.get("name:" + _norm(sym))
        if qty_now > 0 and avg_now:
            if live_row and live_row.get("price"):
                lp = int(live_row["price"])
                current_pct = lp / avg_now - 1
                pt = {"date": today.isoformat(), "pct": current_pct, "close": lp, "avg": round(avg_now), "qty": qty_now, "live": True}
                if segments and segments[-1] and segments[-1][-1]["date"] == today.isoformat():
                    segments[-1][-1] = pt
                    total_num[today] = total_num.get(today, 0.0) - segments[-1][-1]["qty"] * 0  # 종가 점을 현재가로 대체 (아래에서 재합산)
                elif segments:
                    segments[-1].append(pt)
                else:
                    segments.append([pt])
            elif segments:
                current_pct = segments[-1][-1]["pct"]
        symbols[sym] = {"code": code, "held": qty_now > 0, "qty": qty_now, "avg": round(avg_now) if avg_now else None,
                        "since": since, "current_pct": current_pct, "segments": segments}
    # 오늘 종합: 현재가가 있는 보유 종목은 현재가로 다시 합산 (종가 점과 섞이지 않게 오늘 값은 전량 재계산)
    live_num = live_den = 0.0
    for sym, v in symbols.items():
        if v["held"] and v["segments"] and v["segments"][-1] and v["segments"][-1][-1].get("live"):
            pt = v["segments"][-1][-1]
            live_num += pt["qty"] * pt["close"]
            live_den += pt["qty"] * pt["avg"]
    if live_den > 0:
        total_num[today], total_den[today] = live_num, live_den
    total = [{"date": d.isoformat(), "pct": total_num[d] / total_den[d] - 1} for d in sorted(total_den) if total_den[d] > 0]
    held_syms = [s for s, v in symbols.items() if v["held"]]
    priced_syms = [s for s in held_syms if symbols[s]["segments"]]
    return {"symbols": symbols, "total": total, "priced": bool(held_syms) and len(priced_syms) == len(held_syms),
            "notes": notes, "asof": today.isoformat()}
