"""활동 로그 (2026-09-06 지시 "로깅 기능") — 사용자의 매매 기록·주문 상태·실행 이벤트(실패 포함)를 한 화면(로그 페이지)에서 본다.

세 원천을 합쳐 시간순으로 보여 준다 — 중복 기록 없음:
- **거래(trade)**: 거래 원장 `TradeTransaction` — 매수·매도·입금·출금(수동·증권사 가져오기·예수금 보정). 실행 시각 기준.
- **주문(order)**: `BrokerOrder` — 예약주문·무인 실행 줄의 현재 상태(접수·발주·체결·미체결·취소·생략·실패)와 KIS 메시지. 마지막 변경 시각 기준.
- **이벤트(event)**: `ActivityLog` — 실행·동기화·취소·대조 등 과정 기록과 오류(KIS 조회 실패 등). **여기서만 새로 저장한다.**
  기록 지점: 무인 실행 실행 요약·정지, 승인, 예약주문 접수·취소, 사전 갭 취소, 장 마감 동기화 결과·오류, 예수금 대조 경고·보정, 거래 삭제.

수준(level): info(정상) · warn(생략·미체결·취소·대조 차이) · error(발주 실패·조회 실패·정지). 화면의 "경고 이상만" 필터가 실패 확인용.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import ActivityLog, BrokerOrder, Instrument, TradePortfolio, TradeTransaction

logger = logging.getLogger(__name__)
router = APIRouter()
KST = timezone(timedelta(hours=9))

LEVELS = ("info", "warn", "error")
TX_KO = {"buy": "매수", "sell": "매도", "deposit": "입금", "withdraw": "출금"}
SIDE_KO = {"buy": "매수", "sell": "매도"}
ORDER_KIND_KO = {"grid1": "그리드 1차", "grid2": "그리드 2차", "grid3": "그리드 3차", "tp": "익절", "reduce": "축소",
                 "lev_strat": "레버 전략", "lev_tact1": "레버 전술1", "lev_tact2": "레버 전술2", "lev_tact_exit": "전술 이탈",
                 "lev_liq": "레버 청산", "tf_entry": "추세 진입", "tf_exit": "추세 이탈", "core": "코어"}


def _iso(dt: datetime) -> str:
    """표시용 시각 — KST 로 맞춰 분 단위 ISO (DB 는 UTC 로 돌려줄 수 있다)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST).isoformat(timespec="minutes")
# 주문 상태 → 수준: 실패·불일치는 error, 생략·취소·미체결·중복은 warn, 나머지 info
ORDER_LEVEL = {"failed": "error", "mismatch": "error",
               "skipped": "warn", "skipped_gap": "warn", "gap_cancelled": "warn", "unfilled": "warn",
               "cancelled": "warn", "duplicate": "warn"}
KIND_KO = {  # 이벤트 종류 표시명 (화면 배지)
    "autoexec.run": "무인 실행", "autoexec.paused": "무인 실행 정지", "autoexec.approve": "무인 승인", "autoexec.error": "무인 실행 오류",
    "order.reserve": "예약주문 접수", "order.cancel": "주문 취소",
    "preopen.run": "사전 갭 확인", "preopen.cancel": "사전 갭 취소", "preopen.cancel_failed": "사전 갭 취소 실패",
    "preopen.unmatched": "사전 갭 취소 불가", "preopen.error": "사전 갭 확인 오류",
    "sync.post_close": "장 마감 동기화", "sync.error": "동기화 오류", "sync.reserved_failed": "예약주문 상태 조회 실패",
    "cash_check.warn": "예수금 대조 경고", "cash_check.align": "예수금 보정",
    "tx.delete": "거래 삭제",
}


def log_event(session: Session, user_id: int, kind: str, text: str, *, level: str = "info",
              portfolio_id: int | None = None, data: dict | None = None, at: datetime | None = None) -> ActivityLog:
    """이벤트 한 줄 저장 (commit 은 호출자). 본문은 500자, 자료는 JSON 직렬화 가능한 값만."""
    if level not in LEVELS:
        level = "info"
    row = ActivityLog(user_id=user_id, portfolio_id=portfolio_id, kind=kind, level=level,
                      text=(text or "")[:500], data=_jsonable(data or {}), at=at or datetime.now(KST))
    session.add(row)
    return row


def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _fmt_price(v: int | None, market: str) -> str:
    if not v:
        return "시장가"
    return f"${v / 100:,.2f}" if market == "US" else f"{int(v):,}원"


@router.get("/logs")
def list_logs(days: int = Query(default=30, ge=1, le=365), portfolio_id: int | None = None,
              type: str = Query(default="all", pattern="^(all|trade|order|event)$"),
              level: str = Query(default="all", pattern="^(all|warn|error)$"),
              q: str | None = None, limit: int = Query(default=300, ge=1, le=2000),
              user_id: int = Depends(current_user_id), session: Session = Depends(get_session)) -> dict:
    """로그 페이지 — 거래·주문·이벤트를 합쳐 최신순. level=warn 은 경고 이상(실패 포함), error 는 오류만."""
    since = datetime.now(KST) - timedelta(days=days)
    pfs = {p.id: p for p in session.scalars(select(TradePortfolio).where(TradePortfolio.user_id == user_id)).all()}
    if portfolio_id is not None and portfolio_id not in pfs:
        raise HTTPException(status_code=404, detail="portfolio not found")
    pids = [portfolio_id] if portfolio_id is not None else list(pfs)
    items: list[dict] = []
    inst_cache: dict[int, Instrument | None] = {}

    def inst(iid):
        if iid not in inst_cache:
            inst_cache[iid] = session.get(Instrument, iid)
        return inst_cache[iid]

    if pids and type in ("all", "trade"):
        for t in session.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id.in_(pids),
                                                                 TradeTransaction.executed_at >= since)).all():
            pf = pfs[t.portfolio_id]
            if t.kind in ("buy", "sell"):
                i = inst(t.instrument_id) if t.instrument_id else None
                name = (i.name if i else None) or (i.code if i else "?")
                text = f"{TX_KO[t.kind]} {name} {int(t.qty or 0):,}주 @{_fmt_price(t.price, pf.market)}"
                if t.kind == "sell" and t.realized_pnl is not None:
                    text += f" · 실현 {'+' if t.realized_pnl >= 0 else ''}{_fmt_price(abs(int(t.realized_pnl)), pf.market) if t.realized_pnl >= 0 else '-' + _fmt_price(abs(int(t.realized_pnl)), pf.market)}"
            else:
                text = f"{TX_KO[t.kind]} {_fmt_price(t.amount, pf.market)}"
            items.append({"at": _iso(t.executed_at), "type": "trade", "kind": t.kind, "kind_ko": TX_KO[t.kind],
                          "level": "info", "portfolio_id": pf.id, "portfolio": pf.name, "text": text,
                          "detail": t.memo, "ref": f"tx:{t.id}"})
    if pids and type in ("all", "order"):
        from app.broker import STATUS_KO

        for o in session.scalars(select(BrokerOrder).where(BrokerOrder.portfolio_id.in_(pids),
                                                            BrokerOrder.updated_at >= since)).all():
            pf = pfs[o.portfolio_id]
            mode = "무인" if (getattr(o, "mode", "reserve") or "reserve") == "auto" else "예약"
            text = (f"{mode} · {ORDER_KIND_KO.get(o.kind, o.kind)} {SIDE_KO.get(o.side, o.side)} {o.code} {int(o.qty):,}주 "
                    f"@{_fmt_price(o.price, pf.market)} → {STATUS_KO.get(o.status, o.status)}")
            items.append({"at": _iso(o.updated_at or o.created_at), "type": "order", "kind": o.status,
                          "kind_ko": STATUS_KO.get(o.status, o.status), "level": ORDER_LEVEL.get(o.status, "info"),
                          "portfolio_id": pf.id, "portfolio": pf.name, "text": text, "detail": o.message,
                          "ref": f"order:{o.id}", "plan_date": o.plan_date.isoformat() if o.plan_date else None})
    if type in ("all", "event"):
        stmt = select(ActivityLog).where(ActivityLog.user_id == user_id, ActivityLog.at >= since)
        if portfolio_id is not None:
            stmt = stmt.where(ActivityLog.portfolio_id == portfolio_id)
        for e in session.scalars(stmt).all():
            pf = pfs.get(e.portfolio_id) if e.portfolio_id else None
            items.append({"at": _iso(e.at), "type": "event", "kind": e.kind, "kind_ko": KIND_KO.get(e.kind, e.kind),
                          "level": e.level if e.level in LEVELS else "info", "portfolio_id": e.portfolio_id,
                          "portfolio": pf.name if pf else None, "text": e.text, "detail": None, "data": e.data,
                          "ref": f"event:{e.id}"})
    if level == "warn":
        items = [i for i in items if i["level"] in ("warn", "error")]
    elif level == "error":
        items = [i for i in items if i["level"] == "error"]
    if q:
        needle = q.strip().lower()
        items = [i for i in items if needle in (i["text"] or "").lower() or needle in (i.get("detail") or "").lower()
                 or needle in (i.get("portfolio") or "").lower()]
    items.sort(key=lambda i: i["at"], reverse=True)
    counts = {lv: sum(1 for i in items if i["level"] == lv) for lv in LEVELS}
    return {"days": days, "since": since.isoformat(), "total": len(items), "counts": counts, "items": items[:limit],
            "portfolios": [{"id": p.id, "name": p.name, "market": p.market} for p in pfs.values()]}
