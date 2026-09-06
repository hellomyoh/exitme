"""완전 무인 운영 (2026-09-07 지시) — 장 마감 후 주문표를 **자동 승인**하고, 이미 접수·발주된 주문을 **한 번에 취소**한다.

배경: 무인 실행(ADR-008)은 사용자가 전날 주문표에서 줄을 승인해야 09:01 에 발주했다. 사용자 지시 "이 단계 없이 완전 무인으로
운영" → 포트별 옵션 **자동 승인**을 켜면 16:45 배치가 다음 실행일 주문표를 계산·저장하고 허용된 방향의 지정가 줄을 승인 상태로
만든다. 시장가 줄(레버리지 진입·청산)은 정규 주문 경로에 없으므로 옵션에 따라 **예약주문으로 자동 접수**한다(접수 창 15:40~).
09:01 실행과 그 통제(시가 확인·갭 취소·원장 대조·계획 재대조·매수가능조회·자동 정지)는 그대로다.

추가 안전장치: 하루 매수 총액 상한 — **총자산 대비 %**(기본 20%, 0 = 없음, 사용자 지시 2026-09-07). 계획 매수 합계(지정가×수량 +
시장가는 최근 종가×수량)가 신호 기준일 총자산 × 상한을 넘으면 그날 승인하지 않고 포트를 정지한다(사람이 보지 않는 운영이므로 배너로
드러나게). 레짐 전환일의 레버리지 진입(총자산의 30~40%)은 20% 를 넘을 수 있다 — 그날은 정지되어 사람이 확인·승인한다.
정지된 포트·설정에서 매수·매도 모두 꺼진 사용자는 건너뛴다.

취소: `POST /portfolio/{pid}/orders/cancel-all` — 승인(철회)·예약주문(CTSC0009U)·발주된 정규 주문(TTTC0013U)을 모두 취소한다.
`stop=true` 면 포트의 무인 운영도 정지(자동 승인 끔 + paused) — 긴급 정지 버튼.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import log_event
from app.auth import current_user_id
from app.autoexec import (ACTIVE_AUTO, _set_pf_auto_state, auto_approve_cfg, auto_exec_view, pause_portfolio,
                          pf_auto_state, user_auto_exec)
from app import broker as _broker  # _client 는 호출 시점에 찾는다 — 테스트가 app.broker._client 를 바꿔 끼우면 따라가게
from app.broker import _order_out, _owned, _resolve_codes, humanize_kis_error, line_key, reservation_window
from app.db import get_session
from app.models import BrokerCredential, BrokerOrder, Instrument, PortfolioPlan, TradePortfolio

logger = logging.getLogger(__name__)
router = APIRouter()
KST = timezone(timedelta(hours=9))
ACTIVE_STATES = ("approved", "reserved", "submitted", "partial")   # 취소할 수 있는 살아 있는 주문


# ── 설정 (포트 단위, params.auto_exec.auto_approve) ────────────────────────────────

class AutoApproveIn(BaseModel):
    enabled: bool
    market_reserve: bool = True                       # 시장가 줄(레버리지)을 예약주문으로 자동 접수 (사용자 지시 2026-09-07: 무인 접수)
    daily_buy_cap_pct: float = Field(default=20.0, ge=0, le=100)   # 하루 매수 총액 상한 — 총자산 대비 % (기본 20, 0 = 없음)


@router.put("/portfolio/{pid}/auto-exec/auto-approve")
def put_auto_approve(pid: int, body: AutoApproveIn, user_id: int = Depends(current_user_id),
                     session: Session = Depends(get_session)) -> dict:
    """완전 무인(자동 승인) 켜기/끄기 — 국내 포트·연결 계좌·설정에서 매수 또는 매도 허용이 켜져 있어야 켤 수 있다."""
    pf = _owned(session, pid, user_id)
    if pf.market != "KR":
        raise HTTPException(status_code=409, detail="완전 무인 운영은 국내 포트만 지원합니다")
    if body.enabled:
        if not pf.broker_credential_id:
            raise HTTPException(status_code=409, detail="연결된 증권사 계좌가 없습니다 — 증권사 연동에서 계좌를 연결한 뒤 켜세요")
        allowed = user_auto_exec(session, user_id)
        if not (allowed["buy"] or allowed["sell"]):
            raise HTTPException(status_code=409, detail="설정 › 무인 실행에서 매수 또는 매도 허용을 먼저 켜세요 — 켠 방향의 지정가 줄만 자동 승인됩니다")
    now = datetime.now(KST)
    cfg = {"enabled": body.enabled, "market_reserve": body.market_reserve,
           "daily_buy_cap_pct": float(body.daily_buy_cap_pct), "updated_at": now.isoformat(timespec="minutes")}
    _set_pf_auto_state(pf, auto_approve=cfg)
    log_event(session, user_id, "autoexec.auto_approve_setting",
              ("완전 무인 운영 켬 — 16:45 주문표 자동 승인" + (" · 시장가 줄 예약주문 자동 접수" if body.market_reserve else " · 시장가 줄은 수동")
               + (f" · 하루 매수 상한 총자산의 {cfg['daily_buy_cap_pct']:g}%" if cfg["daily_buy_cap_pct"] else " · 하루 매수 상한 없음")) if body.enabled else "완전 무인 운영 끔",
              level="warn" if body.enabled else "info", portfolio_id=pf.id, data=cfg, at=now)
    session.commit()
    logger.info("auto-approve setting pid=%s %s", pf.id, cfg)
    return auto_exec_view(session, pf)


# ── 16:45 배치 ─────────────────────────────────────────────────────────────────

def _default_plan(session: Session, pf: TradePortfolio) -> dict:
    """다음 실행일 주문표 계산 + 스냅샷 저장 — 화면(/signals/daily)과 같은 함수."""
    from app.signals import _portfolio_orders

    return _portfolio_orders(session, pf.id, pf.user_id)


def run_auto_approve(session: Session, now: datetime | None = None, client_factory=None, plan_fn=None) -> dict:
    """자동 승인이 켜진 국내 포트마다 다음 실행일 주문표를 계산해 승인/예약 접수한다. 포트별 실패는 기록하고 계속."""
    now = now or datetime.now(KST)
    today = now.date()
    client_factory = client_factory or _broker._client
    plan_fn = plan_fn or _default_plan
    out: dict = {"date": today.isoformat(), "portfolios": []}
    for pf in session.scalars(select(TradePortfolio).where(TradePortfolio.market == "KR",
                                                            TradePortfolio.broker_credential_id.is_not(None))
                              .order_by(TradePortfolio.id)).all():
        if not auto_approve_cfg(pf)["enabled"]:
            continue
        rec: dict = {"portfolio_id": pf.id, "name": pf.name, "exec_day": None, "approved": 0, "reserved": 0,
                     "skipped": 0, "failed": 0, "manual": [], "note": None}
        try:
            _auto_approve_portfolio(session, pf, today, now, client_factory, plan_fn, rec)
            session.commit()
        except Exception as exc:  # noqa: BLE001 — 포트 단위 실패는 기록하고 다음 포트
            session.rollback()
            rec["error"] = str(exc)[:200]
            logger.exception("auto-approve failed pid=%s", pf.id)
            try:
                detail = getattr(exc, "detail", None) or str(exc)
                log_event(session, pf.user_id, "autoexec.auto_approve", f"자동 승인 실패 — {humanize_kis_error(str(detail)[:200])}",
                          level="error", portfolio_id=pf.id, at=now)
                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
        out["portfolios"].append(rec)
    return out


def _buy_total(session: Session, lines: list[dict], code_200: str, code_lev: str) -> int:
    """계획 매수 합계(원) — 지정가는 지정가×수량, 시장가는 최근 종가×수량으로 근사."""
    from app.portfolios import latest_close

    total = 0
    for o in lines:
        if o.get("side") != "buy":
            continue
        qty = int(o.get("qty") or 0)
        if o.get("price"):
            total += qty * int(o["price"])
        else:
            code = code_200 if o.get("instrument") == "K200" else code_lev
            inst = session.scalar(select(Instrument).where(Instrument.code == code))
            lc = latest_close(session, inst.id) if inst else None
            total += qty * int(lc[0]) if lc else 0
    return total


def _equity_for_cap(session: Session, pf: TradePortfolio, plan: dict) -> int:
    """상한의 분모 = 신호 기준일 총자산 — 주문표 계산이 준 account.equity, 없으면 원장 현금 + 보유 로트×최근 종가."""
    acct = plan.get("account") or {}
    if acct.get("equity"):
        return int(acct["equity"])
    from app.cashcheck import ledger_cash
    from app.models import PositionLot
    from app.portfolios import latest_close

    value = 0
    for lot in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pf.id, PositionLot.qty_open > 0)).all():
        lc = latest_close(session, lot.instrument_id)
        value += int(lot.qty_open) * (int(lc[0]) if lc else int(lot.price))
    return ledger_cash(session, pf.id) + value


def _auto_approve_portfolio(session: Session, pf: TradePortfolio, today: date, now: datetime,
                            client_factory, plan_fn, rec: dict) -> None:
    cfg = auto_approve_cfg(pf)
    state = pf_auto_state(pf)
    if state["paused"]:
        rec["note"] = "정지 상태"
        log_event(session, pf.user_id, "autoexec.auto_approve", f"자동 승인 건너뜀 {now:%H:%M} — 무인 실행 정지 상태 ({state['paused_reason'] or ''})",
                  level="warn", portfolio_id=pf.id, at=now)
        return
    cred = session.get(BrokerCredential, pf.broker_credential_id) if pf.broker_credential_id else None
    if cred is None:
        rec["note"] = "연결 계좌 없음"
        return
    allowed = user_auto_exec(session, pf.user_id)
    if not (allowed["buy"] or allowed["sell"]):
        rec["note"] = "설정에서 무인 매수·매도 모두 꺼짐"
        log_event(session, pf.user_id, "autoexec.auto_approve", f"자동 승인 건너뜀 {now:%H:%M} — 설정 › 무인 실행에서 매수·매도 허용이 모두 꺼져 있음",
                  level="warn", portfolio_id=pf.id, at=now)
        return
    plan = plan_fn(session, pf)
    exec_day = date.fromisoformat(str(plan.get("exec_day")))
    rec["exec_day"] = exec_day.isoformat()
    if exec_day <= today:
        rec["note"] = "실행일이 지났음 (오늘 일봉 미적재?)"
        log_event(session, pf.user_id, "autoexec.auto_approve", f"자동 승인 건너뜀 {now:%H:%M} — 계산된 실행일 {exec_day} 이 오늘 이전 (오늘 일봉이 아직 없음)",
                  level="warn", portfolio_id=pf.id, at=now)
        return
    lines = list(plan.get("orders") or [])
    snapshot = session.scalar(select(PortfolioPlan).where(PortfolioPlan.portfolio_id == pf.id, PortfolioPlan.trade_date == exec_day))
    if snapshot is None:
        rec["note"] = "계획 스냅샷 없음"
        log_event(session, pf.user_id, "autoexec.auto_approve", f"자동 승인 실패 {now:%H:%M} — 실행일 {exec_day} 계획 스냅샷이 저장되지 않음",
                  level="error", portfolio_id=pf.id, at=now)
        return
    plan_lines = {line_key(o): o for o in (snapshot.payload or {}).get("orders", [])}
    code_200, code_lev = _resolve_codes(session, pf)
    # 하루 매수 총액 상한(총자산 대비 %) — 넘으면 승인하지 않고 정지(배너로 드러나게)
    pct = float(cfg.get("daily_buy_cap_pct") or 0)
    if pct > 0:
        equity = _equity_for_cap(session, pf, plan)
        cap = int(equity * pct / 100.0)
        total = _buy_total(session, lines, code_200, code_lev)
        if total > cap:
            rec["note"] = f"매수 합계 {total:,}원 > 상한 {pct:g}% ({cap:,}원, 총자산 {equity:,}원)"
            pause_portfolio(pf, f"자동 승인 중단 — 실행일 {exec_day} 계획 매수 합계 {total:,}원이 하루 상한 {pct:g}%({cap:,}원, 총자산 {equity:,}원)를 넘음. 계획을 확인하고 필요하면 주문표에서 직접 승인한 뒤 다시 켜세요", now)
            _set_pf_auto_state(pf, auto_approve_last={"date": today.isoformat(), "at": now.isoformat(timespec="minutes"), "exec_day": exec_day.isoformat(),
                                                       "approved": 0, "reserved": 0, "skipped": len(lines), "failed": 0, "manual": [], "note": rec["note"]})
            return
    active = {r.line_key for r in session.scalars(select(BrokerOrder).where(
        BrokerOrder.portfolio_id == pf.id, BrokerOrder.plan_date == exec_day,
        BrokerOrder.status.in_(ACTIVE_AUTO + ("reserved",)))).all()}
    client = None
    win = None
    for o in lines:
        key = line_key(o)
        code = code_200 if o.get("instrument") == "K200" else code_lev
        side = o.get("side")
        if key in active:
            rec["skipped"] += 1
            continue
        pl = plan_lines.get(key)
        if pl is None or int(pl.get("qty") or 0) != int(o.get("qty") or 0):
            rec["skipped"] += 1
            continue
        if not allowed.get(side, False):
            rec["skipped"] += 1
            rec["manual"].append(f"{o.get('kind')} ({'매수' if side == 'buy' else '매도'} 허용 꺼짐)")
            continue
        if o.get("otype") == "limit" and o.get("price"):
            session.add(BrokerOrder(portfolio_id=pf.id, broker_credential_id=cred.id, plan_date=exec_day, line_key=key,
                                    code=code, instrument=o["instrument"], kind=o["kind"], side=side, otype="limit",
                                    qty=int(o["qty"]), price=int(o["price"]), mode="auto", status="approved",
                                    message="자동 승인 — 09:01 시가 확인 후 발주 예정"))
            active.add(key)
            rec["approved"] += 1
            continue
        # 시장가 줄 — 예약주문으로 자동 접수(옵션) : 실전 계좌·접수 창 안에서만
        if not cfg.get("market_reserve", True):
            rec["skipped"] += 1
            rec["manual"].append(f"{o.get('kind')} (시장가 — 수동)")
            continue
        if cred.env != "prod":
            rec["skipped"] += 1
            rec["manual"].append(f"{o.get('kind')} (시장가 — 모의 계좌는 예약주문 불가)")
            continue
        win = win or reservation_window(now=now, session=session)
        if not win.get("open"):
            rec["skipped"] += 1
            rec["manual"].append(f"{o.get('kind')} (시장가 — 접수 창 닫힘: {win.get('reason')})")
            continue
        row = BrokerOrder(portfolio_id=pf.id, broker_credential_id=cred.id, plan_date=exec_day, line_key=key,
                          code=code, instrument=o["instrument"], kind=o["kind"], side=side, otype="market",
                          qty=int(o["qty"]), price=None, mode="reserve")
        try:
            client = client or client_factory(cred)
            r = client.reserve_order(code, side, int(o["qty"]), None)
            row.rsvn_ord_seq = r.get("rsvn_ord_seq") or None
            row.status, row.message = "reserved", (r.get("msg") or "자동 접수") + " (자동 승인)"
            row.response = r.get("raw") if isinstance(r.get("raw"), dict) else {}
            active.add(key)
            rec["reserved"] += 1
        except Exception as exc:  # noqa: BLE001 — 줄 단위 실패는 기록하고 계속
            row.status, row.message = "failed", humanize_kis_error(str(exc)[:200])
            rec["failed"] += 1
            logger.warning("auto-approve reserve failed pid=%s %s: %s", pf.id, key, exc)
        session.add(row)
    summary = {"date": today.isoformat(), "at": now.isoformat(timespec="minutes"), "exec_day": exec_day.isoformat(),
               "approved": rec["approved"], "reserved": rec["reserved"], "skipped": rec["skipped"], "failed": rec["failed"],
               "manual": rec["manual"][:10], "note": None}
    _set_pf_auto_state(pf, auto_approve_last=summary)
    text = f"자동 승인 {now:%H:%M} — 실행일 {exec_day} 지정가 {rec['approved']}건 승인"
    if rec["reserved"]:
        text += f" · 시장가 {rec['reserved']}건 예약주문 접수"
    if rec["manual"]:
        text += f" · 수동 필요 {len(rec['manual'])}건 ({', '.join(rec['manual'][:3])})"
    if rec["failed"]:
        text += f" · 접수 실패 {rec['failed']}건"
    if not lines:
        text += " · 주문 없음"
    log_event(session, pf.user_id, "autoexec.auto_approve", text,
              level="error" if rec["failed"] else ("warn" if rec["manual"] else "info"), portfolio_id=pf.id, data=summary, at=now)


# ── 전량 취소 (긴급 정지) ────────────────────────────────────────────────────────

class CancelAllIn(BaseModel):
    stop: bool = False              # 무인 운영도 정지 — 자동 승인 끔 + paused (긴급 정지)
    since: date | None = None       # 이 실행일 이후의 살아 있는 주문 (생략 = 오늘 이후 전부). 필드명이 date 면 타입 이름을 가린다


@router.post("/portfolio/{pid}/orders/cancel-all")
def cancel_all_orders(pid: int, body: CancelAllIn | None = None, user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    """살아 있는 주문 전량 취소 — 승인(철회)·예약주문(예약 취소 TR)·발주된 정규 주문(취소 TR). 줄 단위 실패는 기록하고 계속.

    이미 체결된 것은 취소할 수 없다(반대 매매만 가능) — 체결 줄은 대상에서 빠지고 응답에 건수로 알린다.
    """
    from app.dashboard import kst_today

    body = body or CancelAllIn()
    pf = _owned(session, pid, user_id)
    now = datetime.now(KST)
    since = body.since or kst_today()
    rows = session.scalars(select(BrokerOrder).where(BrokerOrder.portfolio_id == pid, BrokerOrder.plan_date >= since,
                                                    BrokerOrder.status.in_(ACTIVE_STATES)).order_by(BrokerOrder.id)).all()
    filled = session.scalar(select(BrokerOrder.id).where(BrokerOrder.portfolio_id == pid, BrokerOrder.plan_date >= since,
                                                        BrokerOrder.status == "filled").limit(1)) is not None
    cred = session.get(BrokerCredential, pf.broker_credential_id) if pf.broker_credential_id else None
    client = None
    res: dict = {"cancelled": 0, "failed": 0, "items": [], "filled_untouched": filled}
    for r in rows:
        try:
            if r.status == "approved":
                r.status, r.message = "cancelled", "승인 철회 (전량 취소)"
            else:
                if cred is None:
                    raise RuntimeError("연결된 증권사 계좌가 없어 취소 요청을 보낼 수 없습니다")
                client = client or _broker._client(cred)
                if (getattr(r, "mode", "reserve") or "reserve") == "auto":
                    orgno = str(((r.response or {}).get("order") or {}).get("KRX_FWDG_ORD_ORGNO") or "")
                    resp = client.cancel_order(r.order_no or "", orgno=orgno)
                else:
                    created = r.created_at or now
                    ord_dt = (created.astimezone(KST) if created.tzinfo else created).date()
                    resp = client.cancel_reserved_order(r.rsvn_ord_seq or "", ord_dt, orgno=str((r.response or {}).get("RSVN_ORD_ORGNO") or ""))
                r.status, r.message = "cancelled", (resp.get("msg") or "취소됨") + " (전량 취소)"
            res["cancelled"] += 1
        except Exception as exc:  # noqa: BLE001
            r.message = f"전량 취소 실패: {humanize_kis_error(str(exc)[:160])}"
            res["failed"] += 1
            logger.warning("cancel-all failed pid=%s order=%s: %s", pid, r.id, exc)
        res["items"].append(_order_out(r))
    if body.stop:
        pause_portfolio(pf, "사용자 긴급 정지 — 살아 있는 주문 전량 취소", now)
        cur = auto_approve_cfg(pf)
        if cur["enabled"]:
            _set_pf_auto_state(pf, auto_approve={**cur, "enabled": False, "updated_at": now.isoformat(timespec="minutes")})
    text = f"전량 취소 — {res['cancelled']}건 취소" + (f" · 실패 {res['failed']}건" if res["failed"] else "") \
        + (" · 무인 운영 정지(자동 승인 끔)" if body.stop else "") + (" · 이미 체결된 줄은 대상 외" if filled else "")
    log_event(session, user_id, "order.cancel_all", text, level="error" if body.stop or res["failed"] else "warn",
              portfolio_id=pid, data={"cancelled": res["cancelled"], "failed": res["failed"], "stop": body.stop, "since": since.isoformat()}, at=now)
    session.commit()
    return {**res, "auto_exec": auto_exec_view(session, pf)}
