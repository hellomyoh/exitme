"""무인 실행 (2026-09-06 지시) — 사용자가 승인한 지정가 줄을 09:01 에 시가를 확인한 뒤 정규 주문으로 발주한다.

통제 (ADR-008):
1. **설정에서 허용한 쪽만** — 설정 화면의 "무인 매수 허용"·"무인 매도 허용"이 각각 켜져 있어야 그 방향의 승인·발주가 된다.
   둘 다 기본 꺼짐. 끄면 이미 승인된 줄도 실행 시점에 생략된다.
2. **의사결정은 사용자** — 전날 주문표에서 체크해 "무인 실행 승인"한 줄(BrokerOrder mode=auto, status=approved)만 대상.
   승인은 실행일마다 새로 하며, 실행일 09:00 이후에는 승인할 수 없다.
3. **지정가만** — 시장가 줄(레버리지 진입·청산)은 승인 대상에서 제외한다(기존 예약주문 경로).
4. **시가 확인 후 발주** — 09:01 에 당일 시가를 읽어, 갭 취소 기준(전일 종가 − 1.5×ATR, 계획 스냅샷의 정확값) 이하면
   그리드 매수를 생략(skipped_gap)한다. 백테스트와 같은 순서.
5. **하드 리밋** — 줄은 서버 계획 스냅샷과 정확히 일치해야 하고, 매수 합계 ≤ 예수금, 매도 수량 ≤ 잔고, 하루 1회(락), 재시도 없음.
6. **자동 정지** — 발주 실패 2회 연속 또는 장 마감 대조에서 계획·체결 불일치 경고가 나오면 그 포트의 무인 실행을 멈추고(paused)
   화면에 사유를 표시한다. 다시 켜는 것은 사용자.
7. **감사 기록** — 발주·생략·실패를 모두 BrokerOrder 에 남기고, 포트 params.auto_exec.last_run 에 요약을 남긴다.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.broker import (STATUS_KO, OrderLineIn, _client, _cred, _order_out, _owned, _resolve_codes,
                        humanize_kis_error, line_key)
from app.db import get_session
from app.models import BrokerCredential, BrokerOrder, PortfolioPlan, TradePortfolio, UserSettings

logger = logging.getLogger(__name__)
router = APIRouter()
KST = timezone(timedelta(hours=9))

OPEN_TIME = time(9, 0)          # 이 시각 이후엔 오늘 실행분 승인 불가
FAIL_STREAK_PAUSE = 2           # 발주 연속 실패 n회 → 자동 정지
GRID_KINDS_PREFIX = "grid"      # 갭 취소 대상(그리드 매수) 종류 접두

# ── 설정: 사용자 단위 허용 스위치 ───────────────────────────────────────────────────

def user_auto_exec(session: Session, user_id: int) -> dict:
    row = session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    v = (row.auto_exec if row else None) or {}
    return {"buy": bool(v.get("buy", False)), "sell": bool(v.get("sell", False))}


class AutoExecSettingIn(BaseModel):
    buy: bool
    sell: bool


@router.get("/settings/auto-exec")
def get_auto_exec_setting(user_id: int = Depends(current_user_id),
                          session: Session = Depends(get_session)) -> dict:
    return user_auto_exec(session, user_id)


@router.put("/settings/auto-exec")
def put_auto_exec_setting(body: AutoExecSettingIn, user_id: int = Depends(current_user_id),
                          session: Session = Depends(get_session)) -> dict:
    """무인 매수/매도 허용을 각각 저장한다. 끄면 그 방향의 승인된 줄은 실행 시점에 생략된다."""
    from app.settings import _row

    row = _row(session, user_id)
    row.auto_exec = {"buy": body.buy, "sell": body.sell}
    session.commit()
    logger.info("auto-exec setting user=%s buy=%s sell=%s", user_id, body.buy, body.sell)
    return user_auto_exec(session, user_id)


# ── 포트 단위 상태 (params.auto_exec) ─────────────────────────────────────────────

def pf_auto_state(pf: TradePortfolio) -> dict:
    st = dict(((pf.params or {}).get("auto_exec") or {}))
    return {"paused": bool(st.get("paused", False)), "paused_reason": st.get("paused_reason"),
            "paused_at": st.get("paused_at"), "fail_streak": int(st.get("fail_streak", 0) or 0),
            "last_run": st.get("last_run")}


def _set_pf_auto_state(pf: TradePortfolio, **kw) -> None:
    params = dict(pf.params or {})
    st = dict(params.get("auto_exec") or {})
    st.update(kw)
    params["auto_exec"] = st
    pf.params = params  # JSONB 변경 감지 — 재할당 필수


def pause_portfolio(pf: TradePortfolio, reason: str, now: datetime | None = None) -> None:
    now = now or datetime.now(KST)
    _set_pf_auto_state(pf, paused=True, paused_reason=reason[:200], paused_at=now.isoformat())
    logger.warning("auto-exec paused pid=%s: %s", pf.id, reason)


def auto_exec_view(session: Session, pf: TradePortfolio) -> dict:
    """주문표 화면용 — 허용 스위치 + 포트 상태 + 마지막 실행 요약."""
    return {"allowed": user_auto_exec(session, pf.user_id), **pf_auto_state(pf)}


@router.get("/portfolio/{pid}/auto-exec")
def get_portfolio_auto_exec(pid: int, user_id: int = Depends(current_user_id),
                            session: Session = Depends(get_session)) -> dict:
    pf = _owned(session, pid, user_id)
    return auto_exec_view(session, pf)


@router.post("/portfolio/{pid}/auto-exec/resume")
def resume_portfolio_auto_exec(pid: int, user_id: int = Depends(current_user_id),
                               session: Session = Depends(get_session)) -> dict:
    """자동 정지 해제 — 사용자가 사유를 확인한 뒤 누른다. 실패 카운터도 초기화."""
    pf = _owned(session, pid, user_id)
    _set_pf_auto_state(pf, paused=False, paused_reason=None, paused_at=None, fail_streak=0)
    session.commit()
    return auto_exec_view(session, pf)


# ── 승인 ───────────────────────────────────────────────────────────────────────

class ApproveIn(BaseModel):
    date: date                                   # 주문표 실행일 (signal.exec_day)
    lines: list[OrderLineIn] = Field(min_length=1, max_length=40)


ACTIVE_AUTO = ("approved", "submitted", "partial", "filled")


@router.post("/portfolio/{pid}/orders/approve")
def approve_auto_orders(pid: int, body: ApproveIn, user_id: int = Depends(current_user_id),
                        session: Session = Depends(get_session)) -> dict:
    """주문표 줄을 무인 실행 대상으로 승인한다 — 사용자가 화면에서 체크하고 누른 줄만.

    거절: 설정에서 그 방향이 꺼져 있음 · 시장가 줄 · 미국 포트 · 실행일이 지났거나 오늘 09:00 이후 · 계획 스냅샷과 불일치.
    같은 줄에 활성 승인/예약주문이 있으면 중복으로 건너뛴다.
    """
    from app.dashboard import kst_today

    pf = _owned(session, pid, user_id)
    cred = _cred(session, pid, user_id)
    if pf.market != "KR":
        raise HTTPException(status_code=409, detail="무인 실행은 국내 포트만 지원합니다 (KIS 국내주식 주문 TR)")
    allowed = user_auto_exec(session, user_id)
    sides = {ln.side for ln in body.lines}
    blocked = [s for s in ("buy", "sell") if s in sides and not allowed[s]]
    if blocked:
        ko = {"buy": "매수", "sell": "매도"}
        raise HTTPException(status_code=409, detail="설정에서 무인 " + "·".join(ko[s] for s in blocked)
                            + " 허용이 꺼져 있습니다 — 일반 설정 › 무인 실행에서 켠 뒤 승인하세요")
    if any(ln.otype != "limit" for ln in body.lines):
        raise HTTPException(status_code=409, detail="시장가 줄은 무인 실행 대상이 아닙니다 — 예약주문으로 접수하세요")
    now = datetime.now(KST)
    today = kst_today()
    if body.date < today or (body.date == today and now.time() >= OPEN_TIME):
        raise HTTPException(status_code=409, detail="실행일 09:00 이후에는 승인할 수 없습니다 — 다음 주문표에서 승인하세요")
    if pf_auto_state(pf)["paused"]:
        raise HTTPException(status_code=409, detail=f"이 포트의 무인 실행이 정지되어 있습니다 — {pf_auto_state(pf)['paused_reason'] or ''} · 주문표에서 '다시 켜기' 후 승인하세요")
    plan = session.scalar(select(PortfolioPlan).where(PortfolioPlan.portfolio_id == pid,
                                                      PortfolioPlan.trade_date == body.date))
    if plan is None:
        raise HTTPException(status_code=404, detail="이 실행일의 주문표 스냅샷이 없습니다 — 주문표를 먼저 조회하세요")
    plan_lines = {line_key(o): o for o in (plan.payload or {}).get("orders", [])}
    code_200, code_lev = _resolve_codes(session, pf)
    active = {r.line_key for r in session.scalars(select(BrokerOrder).where(
        BrokerOrder.portfolio_id == pid, BrokerOrder.plan_date == body.date,
        BrokerOrder.status.in_(ACTIVE_AUTO + ("reserved",)))).all()}
    items, ok, failed = [], 0, 0
    for ln in body.lines:
        o = ln.model_dump()
        key = line_key(o)
        code = code_200 if ln.instrument == "K200" else code_lev
        pending = {"id": None, "plan_date": body.date.isoformat(), "line_key": key, "code": code, "mode": "auto",
                   "instrument": ln.instrument, "kind": ln.kind, "side": ln.side, "otype": ln.otype,
                   "qty": ln.qty, "price": ln.price, "rsvn_ord_seq": None, "order_no": None, "filled_qty": 0, "created_at": None}
        pl = plan_lines.get(key)
        if pl is None or int(pl.get("qty") or 0) != ln.qty:
            items.append({**pending, "status": "mismatch", "status_ko": STATUS_KO["mismatch"],
                          "message": "화면의 주문표가 서버에 저장된 계획과 다릅니다 — 새로 고친 뒤 다시 승인하세요"})
            failed += 1
            continue
        if key in active:
            items.append({**pending, "status": "duplicate", "status_ko": STATUS_KO["duplicate"],
                          "message": "같은 줄이 이미 승인되었거나 예약주문으로 접수돼 있습니다"})
            continue
        row = BrokerOrder(portfolio_id=pid, broker_credential_id=cred.id, plan_date=body.date, line_key=key,
                          code=code, instrument=ln.instrument, kind=ln.kind, side=ln.side, otype=ln.otype,
                          qty=ln.qty, price=ln.price, mode="auto", status="approved",
                          message="09:01 시가 확인 후 발주 예정")
        session.add(row)
        session.flush()
        active.add(key)
        ok += 1
        items.append(_order_out(row))
        logger.info("auto-exec approved pid=%s %s %s x%s @%s for %s", pid, key, code, ln.qty, ln.price, body.date)
    session.commit()
    return {"date": body.date.isoformat(), "approved": ok, "failed": failed, "items": items}


# ── 실행 (워커 09:01) ──────────────────────────────────────────────────────────

def _acquire_day_lock(pf_id: int, today: date) -> bool:
    """하루 1회 실행 보장 — Redis SET NX(6시간). Redis 가 없으면 True(DB 마커는 호출자가 확인)."""
    try:
        import redis as sync_redis

        from app.config import get_settings

        r = sync_redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=1)
        return bool(r.set(f"autoexec:{pf_id}:{today.isoformat()}", "1", nx=True, ex=6 * 3600))
    except Exception:  # noqa: BLE001 — Redis 부재(테스트) 시 DB 마커로 대신
        return True


def _read_open(client, code: str, sleep_fn=_time.sleep, tries: int = 4, wait: float = 10.0) -> int | None:
    """당일 시가 — 현재가 조회의 stck_oprc. 09:00 직후 0 이면 잠시 기다려 다시 읽는다."""
    for i in range(tries):
        try:
            out = client.fetch_price(code)
            v = int(str(out.get("stck_oprc") or "0").replace(",", "") or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto-exec open price failed code=%s: %s", code, exc)
            v = 0
        if v > 0:
            return v
        if i < tries - 1:
            sleep_fn(wait)
    return None


def run_auto_execution(session: Session, now: datetime | None = None, client_factory=None,
                       sleep_fn=_time.sleep) -> dict:
    """승인된 줄을 발주한다 — 포트별로 시가 확인 → 갭 취소 → 한도 확인 → 지정가 발주. 포트별 실패는 기록만 하고 계속."""
    from app.dashboard import kst_today

    now = now or datetime.now(KST)
    today = kst_today() if now is None else now.date()
    client_factory = client_factory or _client
    out: dict = {"date": today.isoformat(), "portfolios": []}
    rows_all = session.scalars(select(BrokerOrder).where(
        BrokerOrder.mode == "auto", BrokerOrder.status == "approved", BrokerOrder.plan_date == today)
        .order_by(BrokerOrder.portfolio_id, BrokerOrder.id)).all()
    by_pf: dict[int, list[BrokerOrder]] = {}
    for r in rows_all:
        by_pf.setdefault(r.portfolio_id, []).append(r)
    for pid, rows in by_pf.items():
        pf = session.get(TradePortfolio, pid)
        rec: dict = {"portfolio_id": pid, "name": pf.name if pf else None, "submitted": 0, "skipped_gap": 0,
                     "skipped": 0, "failed": 0}
        try:
            _execute_portfolio(session, pf, rows, today, now, client_factory, sleep_fn, rec)
            session.commit()
        except Exception as exc:  # noqa: BLE001 — 포트 단위 실패는 기록하고 다음 포트
            session.rollback()
            rec["error"] = str(exc)[:200]
            logger.exception("auto-exec portfolio failed pid=%s", pid)
        out["portfolios"].append(rec)
    return out


def _skip(rows: list[BrokerOrder], status: str, message: str, rec: dict, key: str) -> None:
    for r in rows:
        r.status = status
        r.message = message
    rec[key] += len(rows)


def _execute_portfolio(session: Session, pf: TradePortfolio, rows: list[BrokerOrder], today: date, now: datetime,
                       client_factory, sleep_fn, rec: dict) -> None:
    state = pf_auto_state(pf)
    last = state.get("last_run") or {}
    if last.get("date") == today.isoformat():          # DB 마커 — 같은 날 두 번 돌지 않는다
        rec["error"] = "already-ran"
        return
    if not _acquire_day_lock(pf.id, today):
        rec["error"] = "locked"
        return
    if state["paused"]:
        _skip(rows, "skipped", f"무인 실행 정지 상태 — {state['paused_reason'] or ''}", rec, "skipped")
        _set_pf_auto_state(pf, last_run={"date": today.isoformat(), "at": now.isoformat(), **{k: rec[k] for k in ("submitted", "skipped_gap", "skipped", "failed")}, "note": "paused"})
        return
    cred = session.get(BrokerCredential, pf.broker_credential_id) if pf.broker_credential_id else None
    if cred is None:
        _skip(rows, "skipped", "연결된 증권사 계좌가 없습니다", rec, "skipped")
        return
    allowed = user_auto_exec(session, pf.user_id)
    # ① 설정 스위치 — 승인 뒤 꺼졌을 수 있다
    keep: list[BrokerOrder] = []
    for r in rows:
        if not allowed.get(r.side, False):
            r.status, r.message = "skipped", f"설정에서 무인 {'매수' if r.side == 'buy' else '매도'} 허용이 꺼져 있어 생략"
            rec["skipped"] += 1
        elif r.otype != "limit" or not r.price:
            r.status, r.message = "skipped", "지정가가 아닌 줄은 무인 실행하지 않습니다"
            rec["skipped"] += 1
        else:
            keep.append(r)
    client = client_factory(cred)
    # ② 시가 확인 → 갭 취소 (계획 스냅샷의 정확값)
    code_200, _code_lev = _resolve_codes(session, pf)
    open_px = _read_open(client, code_200, sleep_fn=sleep_fn) if keep else None
    plan = session.scalar(select(PortfolioPlan).where(PortfolioPlan.portfolio_id == pf.id, PortfolioPlan.trade_date == today))
    payload = (plan.payload if plan else None) or {}
    gap_exact = payload.get("gap_cancel_exact") or payload.get("gap_cancel_below")
    gap_hit = bool(keep and open_px is not None and gap_exact and open_px <= float(gap_exact))
    if keep and open_px is None:
        _skip(keep, "skipped", "시가를 확인하지 못해 발주하지 않았습니다 (현재가 조회 실패)", rec, "skipped")
        keep = []
    if gap_hit:
        grid = [r for r in keep if r.side == "buy" and r.kind.startswith(GRID_KINDS_PREFIX)]
        _skip(grid, "skipped_gap", f"갭 취소 — 시가 {open_px:,}원 ≤ 기준 {int(float(gap_exact)):,}원, 그리드 매수 생략", rec, "skipped_gap")
        keep = [r for r in keep if r not in grid]
    # ③ 한도 — 예수금·잔고
    if keep:
        try:
            bal = client.fetch_balance()
        except Exception as exc:  # noqa: BLE001
            _skip(keep, "skipped", f"잔고 조회 실패로 발주하지 않았습니다 — {humanize_kis_error(str(exc)[:120])}", rec, "skipped")
            keep = []
        else:
            deposit = int(bal.get("deposit") or 0)
            held = {h["code"]: int(h["qty"]) for h in bal.get("holdings", [])}
            buys = [r for r in keep if r.side == "buy"]
            buy_total = sum(r.qty * int(r.price) for r in buys)
            if buys and buy_total > deposit:
                _skip(buys, "skipped", f"예수금 부족 — 매수 합계 {buy_total:,}원 > 예수금 {deposit:,}원", rec, "skipped")
                keep = [r for r in keep if r.side != "buy"]
            for r in [r for r in keep if r.side == "sell"]:
                if r.qty > held.get(r.code, 0):
                    r.status, r.message = "skipped", f"잔고 부족 — 매도 {r.qty}주 > 보유 {held.get(r.code, 0)}주"
                    rec["skipped"] += 1
                    keep.remove(r)
    # ④ 발주 — 매도 먼저(현금 확보), 줄 단위 실패는 기록하고 계속
    streak = state["fail_streak"]
    for r in sorted(keep, key=lambda x: 0 if x.side == "sell" else 1):
        try:
            res = client.place_order(r.code, r.side, r.qty, int(r.price))
            r.order_no = res["order_no"] or None
            r.status = "submitted"
            r.message = res["msg"] or "발주됨"
            r.response = {**(r.response or {}), "order": res["raw"], "open": open_px}
            rec["submitted"] += 1
            streak = 0
            logger.info("auto-exec submitted pid=%s %s %s x%s @%s no=%s", pf.id, r.line_key, r.code, r.qty, r.price, r.order_no)
        except Exception as exc:  # noqa: BLE001
            r.status = "failed"
            r.message = humanize_kis_error(str(exc)[:200])
            rec["failed"] += 1
            streak += 1
            logger.warning("auto-exec failed pid=%s %s: %s", pf.id, r.line_key, exc)
    summary = {"date": today.isoformat(), "at": now.isoformat(), "open": open_px, "gap_hit": gap_hit,
               **{k: rec[k] for k in ("submitted", "skipped_gap", "skipped", "failed")}}
    _set_pf_auto_state(pf, fail_streak=streak, last_run=summary)
    if streak >= FAIL_STREAK_PAUSE:
        pause_portfolio(pf, f"발주 연속 실패 {streak}회 — 마지막 오류: {rows[-1].message or ''}", now)


# ── 장 마감 후 상태 확정 (run_post_close_sync 에서 호출) ─────────────────────────────

def sync_auto_orders(session: Session, cred: BrokerCredential, rows: list[BrokerOrder], today: date,
                     now: datetime | None = None, client=None) -> int:
    """발주된(submitted/partial) 무인 주문의 체결 상태를 당일 체결조회로 확정한다. 반환: 바뀐 건수."""
    active = [r for r in rows if r.mode == "auto" and r.status in ("submitted", "partial")]
    if not active:
        return 0
    now = now or datetime.now(KST)
    client = client or _client(cred)
    try:
        execs = client.fetch_executions(today, today, only_filled=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto-exec sync failed cred=%s: %s", cred.id, exc)
        return 0
    filled: dict[str, int] = {}
    for e in execs:
        filled[str(e.order_no)] = filled.get(str(e.order_no), 0) + int(e.filled_qty or 0)
    day_over = now.time() >= time(15, 30)
    changed = 0
    for r in active:
        q = filled.get(str(r.order_no or ""), 0)
        r.filled_qty = q
        st = "filled" if q >= r.qty else ("partial" if q > 0 else ("unfilled" if day_over else "submitted"))
        if st != r.status:
            r.status = st
            changed += 1
    return changed


def pause_if_reconcile_warns(session: Session, pf: TradePortfolio, reconcile: dict | None, now: datetime | None = None) -> bool:
    """장 마감 대조에서 경고(warn)가 있으면 무인 실행을 멈춘다 — 계획과 계좌가 어긋난 채 다음 날 발주하지 않기 위해."""
    warns = [it for it in ((reconcile or {}).get("items") or []) if it.get("level") == "warn"]
    if not warns or pf_auto_state(pf)["paused"]:
        return False
    pause_portfolio(pf, "장 마감 대조 경고 — " + "; ".join(w.get("text", "") for w in warns)[:160], now)
    return True
