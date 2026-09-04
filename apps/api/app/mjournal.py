"""수동 주식 매매일지 API (2026-09-05 지시).

일지 생성 시 이름·종목·증권사·요율을 받고, 매일 입력은 구분·수량·단가(+날짜·이유)만.
실현손익·수익률·보유기간·비용·합계는 FIFO 로 서버가 계산해 내려준다 (스프레드시트 대체).
규약: 매수 비용 = 매수금×수수료율(매수 행 비용), 매도 비용 = 매도금×(수수료율+제세금율),
실현손익 = 매도금 − 매도비용 − FIFO 매수원가(매수 수수료 미배분 — 행 비용으로 별도 표기).
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import ManualJournal, ManualJournalEntry

router = APIRouter()


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
    return {"items": [{"id": r.id, "name": r.name, "symbol": r.symbol, "broker": r.broker} for r in rows]}


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
    total_buy = total_sell = total_cost = total_realized = 0
    for e in sorted(entries, key=lambda x: (x.trade_date, x.id)):
        sym = (e.symbol or j.symbol).strip()
        lots = lots_by.setdefault(sym, [])
        amount = e.qty * e.price
        if e.side == "buy":
            cost = round(amount * fee)
            lots.append({"qty": e.qty, "price": e.price, "date": e.trade_date})
            total_buy += amount
            total_cost += cost
            rows.append({"id": e.id, "symbol": sym, "side": "buy", "buy_date": e.trade_date.isoformat(),
                         "sell_date": None, "hold_days": None, "realized": None, "return_pct": None,
                         "price": e.price, "qty": e.qty, "cost": cost, "amount": amount,
                         "reason": e.reason})
        else:
            held = sum(l["qty"] for l in lots)
            if e.qty > held:
                rows.append({"id": e.id, "symbol": sym, "side": "sell", "buy_date": None,
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
            rows.append({"id": e.id, "symbol": sym, "side": "sell",
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
            holdings.append({"symbol": sym, "qty": q, "avg_price": round(c / q), "cost": c})
    holdings.sort(key=lambda h: -h["cost"])
    symbols = sorted({(e.symbol or j.symbol).strip() for e in entries} | {j.symbol.strip()})
    return {
        "rows": list(reversed(rows)),  # 최신이 위
        "summary": {"realized": total_realized, "sell_amount": total_sell,
                    "buy_amount": total_buy, "cost": total_cost},
        "holdings": holdings, "symbols": symbols,
    }


@router.get("/mjournals/overview")
def journals_overview(user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    """전 일지 합산 현황 (2026-09-05 지시) — 종목별 보유 비중(취득원가 기준) + 누적 실현손익 추이.

    수동 일지는 시세 미연동 — 비중은 원가, 수익 라인은 실현손익(매도 시점) 기준.
    """
    journals = session.scalars(select(ManualJournal).where(ManualJournal.user_id == user_id)
                               .order_by(ManualJournal.id)).all()
    items = []
    for j in journals:
        entries = session.scalars(select(ManualJournalEntry)
                                  .where(ManualJournalEntry.journal_id == j.id)).all()
        c = _compute(j, entries)
        by_date: dict[str, int] = {}
        cum = 0
        for r in reversed(c["rows"]):  # rows 는 최신순 → 시간순으로
            if r["side"] == "sell" and r["realized"] is not None:
                cum += r["realized"]
                by_date[r["sell_date"]] = cum  # 같은 날 다건은 마지막 누적값 (차트 시간축 유일성)
        series = [{"date": d, "value": v} for d, v in sorted(by_date.items())]
        items.append({"id": j.id, "name": j.name, "symbol": j.symbol,
                      "holdings": c["holdings"],
                      "realized": c["summary"]["realized"], "series": series})
    return {"items": items}


@router.get("/mjournals/{jid}")
def get_journal(jid: int, user_id: int = Depends(current_user_id),
                session: Session = Depends(get_session)) -> dict:
    j = _owned(session, jid, user_id)
    entries = session.scalars(select(ManualJournalEntry)
                              .where(ManualJournalEntry.journal_id == jid)).all()
    return {"id": j.id, "name": j.name, "symbol": j.symbol, "broker": j.broker,
            "fee_rate": float(j.fee_rate), "tax_rate": float(j.tax_rate),
            **_compute(j, entries)}


@router.post("/mjournals/{jid}/entries", status_code=201)
def add_entry(jid: int, body: EntryIn, user_id: int = Depends(current_user_id),
              session: Session = Depends(get_session)) -> dict:
    j = _owned(session, jid, user_id)
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
