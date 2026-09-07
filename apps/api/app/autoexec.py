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
from app.broker import (STATUS_KO, OrderLineIn, _acct_out, _client, _cred, _order_out, _owned, _resolve_codes,
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

def _switches(v: dict | None) -> dict:
    v = v or {}
    # preopen_cancel: 장 시작 전 예상 시가 갭 취소 (2026-09-06 지시, app.preopen) — 취소만 하는 보호 동작이라 기본 켜짐
    return {"buy": bool(v.get("buy", False)), "sell": bool(v.get("sell", False)),
            "preopen_cancel": bool(v.get("preopen_cancel", True))}


def user_auto_exec(session: Session, user_id: int) -> dict:
    """사용자 기본값 — 새 계좌에 적용되고 '일괄 적용'의 저장소. 승인·실행 판정에는 쓰지 않는다(계좌별 스위치가 진실, 0024)."""
    row = session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    return _switches(row.auto_exec if row else None)


def account_auto_exec(cred: BrokerCredential | None) -> dict:
    """계좌별 허용 스위치 (0024, 2026-09-07 지시 "무인 실행을 등록된 증권사 계좌별로 설정") — 승인·09:01 실행·자동 승인·사전 갭 취소가
    모두 **포트에 연결된 계좌의 이 값**으로 판정한다. 계좌가 없으면 전부 꺼짐."""
    if cred is None:
        return {"buy": False, "sell": False, "preopen_cancel": False}
    return _switches(getattr(cred, "auto_exec", None))


def auto_exec_settings_view(session: Session, user_id: int) -> dict:
    """설정 화면용 — 기본값 + 계좌별 스위치(연결 포트 이름 포함). 최상위 buy/sell/preopen_cancel 은 기본값(하위호환)."""
    rows = session.scalars(select(BrokerCredential).where(BrokerCredential.user_id == user_id).order_by(BrokerCredential.id)).all()
    by_cred: dict[int, list[str]] = {}
    for p in session.scalars(select(TradePortfolio).where(TradePortfolio.user_id == user_id)).all():
        if p.broker_credential_id:
            by_cred.setdefault(p.broker_credential_id, []).append(p.name)
    default = user_auto_exec(session, user_id)
    return {**default, "default": default,
            "accounts": [{**_acct_out(r, by_cred.get(r.id, [])), "auto_exec": account_auto_exec(r)} for r in rows]}


class AutoExecSettingIn(BaseModel):
    buy: bool
    sell: bool
    preopen_cancel: bool | None = None   # 생략 = 유지


class AccountAutoExecIn(BaseModel):
    buy: bool | None = None
    sell: bool | None = None
    preopen_cancel: bool | None = None


@router.get("/settings/auto-exec")
def get_auto_exec_setting(user_id: int = Depends(current_user_id),
                          session: Session = Depends(get_session)) -> dict:
    return auto_exec_settings_view(session, user_id)


@router.put("/settings/auto-exec")
def put_auto_exec_setting(body: AutoExecSettingIn, user_id: int = Depends(current_user_id),
                          session: Session = Depends(get_session)) -> dict:
    """기본값 저장 + 등록된 모든 계좌에 일괄 적용. 끄면 그 방향의 승인된 줄은 실행 시점에 생략된다."""
    from app.settings import _row

    row = _row(session, user_id)
    cur = dict(row.auto_exec or {})
    pre = body.preopen_cancel if body.preopen_cancel is not None else bool(cur.get("preopen_cancel", True))
    row.auto_exec = {"buy": body.buy, "sell": body.sell, "preopen_cancel": pre}
    for cred in session.scalars(select(BrokerCredential).where(BrokerCredential.user_id == user_id)).all():
        cred.auto_exec = dict(row.auto_exec)
    session.commit()
    logger.info("auto-exec setting user=%s (all accounts) buy=%s sell=%s preopen_cancel=%s", user_id, body.buy, body.sell, pre)
    return auto_exec_settings_view(session, user_id)


@router.put("/settings/auto-exec/accounts/{aid}")
def put_account_auto_exec(aid: int, body: AccountAutoExecIn, user_id: int = Depends(current_user_id),
                          session: Session = Depends(get_session)) -> dict:
    """계좌 하나의 무인 매수/매도/사전 갭 취소 스위치 (2026-09-07 지시). 생략한 키는 유지."""
    cred = session.get(BrokerCredential, aid)
    if cred is None or cred.user_id != user_id:
        raise HTTPException(status_code=404, detail="account not found")
    cur = account_auto_exec(cred)
    for k in ("buy", "sell", "preopen_cancel"):
        val = getattr(body, k)
        if val is not None:
            cur[k] = bool(val)
    cred.auto_exec = cur
    from app.activity import log_event

    ko = {"buy": "무인 매수", "sell": "무인 매도", "preopen_cancel": "사전 갭 취소"}
    log_event(session, user_id, "autoexec.account_setting",
              f"계좌 {cred.label} — " + " · ".join(f"{ko[k]} {'허용' if cur[k] else '꺼짐'}" for k in ko),
              level="warn" if (cur["buy"] or cur["sell"]) else "info", data={"account_id": cred.id, **cur})
    session.commit()
    logger.info("auto-exec account setting cred=%s %s", cred.id, cur)
    return auto_exec_settings_view(session, user_id)


# ── 포트 단위 상태 (params.auto_exec) ─────────────────────────────────────────────

def pf_auto_state(pf: TradePortfolio) -> dict:
    st = dict(((pf.params or {}).get("auto_exec") or {}))
    return {"paused": bool(st.get("paused", False)), "paused_reason": st.get("paused_reason"),
            "paused_at": st.get("paused_at"), "fail_streak": int(st.get("fail_streak", 0) or 0),
            "last_run": st.get("last_run"),
            # 완전 무인(자동 승인, 2026-09-07 지시, app.autoapprove)
            "auto_approve": auto_approve_cfg(pf), "auto_approve_last": st.get("auto_approve_last")}


DAILY_BUY_CAP_PCT_DEFAULT = 20.0   # 하루 매수 총액 상한 — 총자산 대비 % (사용자 지시 2026-09-07 "기본 20%"). 0 = 상한 없음


def auto_approve_cfg(pf: TradePortfolio) -> dict:
    """포트별 자동 승인 설정 — params.auto_exec.auto_approve.

    기본 **켬**(2026-09-07 밤 사용자 지시 "무인 실행 승인은 표에서 체크하지 않아도 자동으로 발주해야 해") — 설정에서 무인 매수·매도
    허용을 켠 것이 상시 승인이고, 포트별로 끄는 것만 선택이다. 시장가 줄 예약 접수 기본 켬, 상한 총자산의 20%.
    """
    st = dict((((pf.params or {}).get("auto_exec") or {}).get("auto_approve")) or {})
    pct = st.get("daily_buy_cap_pct", DAILY_BUY_CAP_PCT_DEFAULT)
    try:
        pct = float(pct) if pct is not None else DAILY_BUY_CAP_PCT_DEFAULT
    except (TypeError, ValueError):
        pct = DAILY_BUY_CAP_PCT_DEFAULT
    return {"enabled": bool(st.get("enabled", True)), "market_reserve": bool(st.get("market_reserve", True)),
            "daily_buy_cap_pct": pct, "updated_at": st.get("updated_at")}


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
    # 로그 페이지 (2026-09-06) — 정지는 반드시 남긴다
    from sqlalchemy.orm import object_session

    from app.activity import log_event

    s = object_session(pf)
    if s is not None:
        log_event(s, pf.user_id, "autoexec.paused", f"무인 실행 정지 — {reason[:200]}", level="error", portfolio_id=pf.id, at=now)


def auto_exec_view(session: Session, pf: TradePortfolio) -> dict:
    """주문표 화면용 — 연결 계좌의 허용 스위치 + 포트 상태 + 마지막 실행 요약."""
    cred = session.get(BrokerCredential, pf.broker_credential_id) if pf.broker_credential_id else None
    return {"allowed": account_auto_exec(cred),
            "account": {"id": cred.id, "label": cred.label, "env": cred.env} if cred else None,
            **pf_auto_state(pf)}


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
    # 승인 전 계좌 옵션 검사 (2026-09-07 지시) — 이 포트에 연결된 계좌의 스위치가 판정 기준
    allowed = account_auto_exec(cred)
    sides = {ln.side for ln in body.lines}
    blocked = [s for s in ("buy", "sell") if s in sides and not allowed[s]]
    if blocked:
        ko = {"buy": "매수", "sell": "매도"}
        raise HTTPException(status_code=409, detail=f"계좌 {cred.label} 의 무인 " + "·".join(ko[s] for s in blocked)
                            + " 허용이 꺼져 있습니다 — 일반 설정 › 무인 실행에서 이 계좌의 스위치를 켠 뒤 승인하세요")
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
    from app.activity import log_event

    log_event(session, user_id, "autoexec.approve",
              f"무인 실행 승인 {ok}건 (실행일 {body.date.isoformat()})" + (f" · 거절 {failed}건" if failed else ""),
              level="warn" if failed else "info", portfolio_id=pid,
              data={"date": body.date.isoformat(), "approved": ok, "failed": failed, "lines": [i["line_key"] for i in items]})
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
            if pf is not None and rec.get("error") not in ("already-ran", "locked"):
                _log_run(session, pf, rec, now)   # 로그 페이지 (2026-09-06)
            session.commit()
        except Exception as exc:  # noqa: BLE001 — 포트 단위 실패는 기록하고 다음 포트
            session.rollback()
            rec["error"] = str(exc)[:200]
            logger.exception("auto-exec portfolio failed pid=%s", pid)
            if pf is not None:
                try:
                    from app.activity import log_event

                    log_event(session, pf.user_id, "autoexec.error", f"무인 실행 오류 — {str(exc)[:200]}", level="error", portfolio_id=pf.id, at=now)
                    session.commit()
                except Exception:  # noqa: BLE001
                    session.rollback()
        out["portfolios"].append(rec)
    return out


def _log_run(session: Session, pf: TradePortfolio, rec: dict, now: datetime) -> None:
    """실행 요약 한 줄 — 줄별 결과는 BrokerOrder 가 원천이므로 여기서는 건수만."""
    from app.activity import log_event

    parts = [f"발주 {rec['submitted']}건"]
    if rec["skipped_gap"]:
        parts.append(f"갭 취소 생략 {rec['skipped_gap']}건")
    if rec["skipped"]:
        parts.append(f"생략 {rec['skipped']}건")
    if rec["failed"]:
        parts.append(f"실패 {rec['failed']}건")
    lvl = "error" if rec["failed"] else ("warn" if (rec["skipped"] or rec["skipped_gap"]) else "info")
    log_event(session, pf.user_id, "autoexec.run", f"무인 실행 {now:%H:%M} — " + " · ".join(parts), level=lvl,
              portfolio_id=pf.id, data={k: v for k, v in rec.items() if k != "name"}, at=now)


def _ledger_holdings(session: Session, pid: int) -> dict[str, int]:
    """앱 원장(잔여 로트) 기준 종목별 보유 수량 — 09:01 사전 대조에서 계좌 잔고와 비교한다."""
    from app.models import Instrument, PositionLot

    out: dict[str, int] = {}
    for lot in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pid)).all():
        if lot.qty_open > 0:
            inst = session.get(Instrument, lot.instrument_id)
            if inst is not None:
                out[inst.code] = out.get(inst.code, 0) + int(lot.qty_open)
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
    allowed = account_auto_exec(cred)
    # ① 계좌 스위치 — 승인 뒤 꺼졌을 수 있다 (발주 직전 재검사, 2026-09-07 지시)
    keep: list[BrokerOrder] = []
    for r in rows:
        if not allowed.get(r.side, False):
            r.status, r.message = "skipped", f"계좌 설정에서 무인 {'매수' if r.side == 'buy' else '매도'} 허용이 꺼져 있어 생략"
            rec["skipped"] += 1
        elif r.otype != "limit" or not r.price:
            r.status, r.message = "skipped", "지정가가 아닌 줄은 무인 실행하지 않습니다"
            rec["skipped"] += 1
        else:
            keep.append(r)
    client = client_factory(cred)
    code_200, code_lev = _resolve_codes(session, pf)
    # ② 계획 스냅샷 재대조 (2026-09-06 후속 2) — 승인 때 확인했지만 실행 시점에 한 번 더: 그날 계획에 같은 줄(키·수량)이 있어야 발주
    plan = session.scalar(select(PortfolioPlan).where(PortfolioPlan.portfolio_id == pf.id, PortfolioPlan.trade_date == today))
    payload = (plan.payload if plan else None) or {}
    plan_lines = {line_key(o): int(o.get("qty") or 0) for o in payload.get("orders", [])}
    still: list[BrokerOrder] = []
    for r in keep:
        if plan_lines.get(r.line_key) != r.qty:
            r.status = "skipped"
            r.message = ("실행 시점 재대조 실패 — 그날 계획 스냅샷이 없습니다" if plan is None
                         else "실행 시점 재대조 실패 — 그날 계획 스냅샷에 같은 줄(수량)이 없습니다")
            rec["skipped"] += 1
        else:
            still.append(r)
    keep = still
    # ③ 시가 확인 → 갭 취소 (계획 스냅샷의 정확값)
    open_px = _read_open(client, code_200, sleep_fn=sleep_fn) if keep else None
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
            # 원장 대조 (2026-09-06 후속 1) — 전략 종목(200 ETF·레버리지)의 앱 원장 보유가 계좌 잔고와 다르면 그 보유로 계산된
            # 주문표는 틀린 전제라 그날 발주를 생략하고 정지한다. 15:45 대조를 기다리지 않고 발주 전에 막는 장치.
            ledger = _ledger_holdings(session, pf.id)
            diffs = [(c, ledger.get(c, 0), held.get(c, 0)) for c in (code_200, code_lev) if ledger.get(c, 0) != held.get(c, 0)]
            if diffs:
                detail = ", ".join(f"{c} 원장 {l:,}주 ≠ 계좌 {a:,}주" for c, l, a in diffs)
                _skip(keep, "skipped", f"사전 대조 불일치 — {detail}", rec, "skipped")
                keep = []
                pause_portfolio(pf, f"09:01 사전 대조 불일치 — {detail}. 체결 가져오기 또는 기록 수정으로 원장을 계좌에 맞춘 뒤 다시 켜세요", now)
            # 매수 한도는 발주 직전에 줄마다 KIS 매수가능조회로 판정한다(④ 참조). 여기서는 폴백용 예수금만 기억한다.
            buy_fallback_deposit = deposit
            for r in [r for r in keep if r.side == "sell"]:
                if r.qty > held.get(r.code, 0):
                    r.status, r.message = "skipped", f"잔고 부족 — 매도 {r.qty}주 > 보유 {held.get(r.code, 0)}주"
                    rec["skipped"] += 1
                    keep.remove(r)
    # ④ 발주 — 매도 먼저(현금 확보), 매수는 얕은 그리드(높은 지정가)부터. 줄 단위 실패는 기록하고 계속.
    #    매수 줄은 발주 직전에 **매수가능조회**(증거금·매도대금 재사용까지 KIS 가 계산한 주문가능 수량)로 판정하고,
    #    가능 수량 < 계획 수량이면 그 줄만 생략한다(수량을 줄여 내지 않음 — 계획과 달라지므로).
    #    조회가 실패하면 예수금 총액 누적 규칙으로 물러난다 (2026-09-06 지시).
    streak = state["fail_streak"]
    last_fail = ""
    running = 0  # 폴백(예수금 누적)용
    for r in sorted(keep, key=lambda x: (0 if x.side == "sell" else 1, -int(x.price or 0))):
        if r.side == "buy":
            cost = r.qty * int(r.price)
            try:
                pb = client.buyable(r.code, int(r.price))
                can = int(pb.get("cash_qty") or 0)
                if can < r.qty:
                    r.status = "skipped"
                    r.message = f"주문가능 수량 부족 — 가능 {can:,}주 < 계획 {r.qty:,}주 (주문가능현금 {int(pb.get('cash') or 0):,}원)"
                    rec["skipped"] += 1
                    continue
            except Exception as exc:  # noqa: BLE001 — 조회 실패 → 예수금 총액 누적 규칙으로 폴백
                logger.warning("auto-exec buyable failed pid=%s %s: %s — deposit fallback", pf.id, r.line_key, exc)
                if running + cost > buy_fallback_deposit:
                    r.status = "skipped"
                    r.message = f"예수금 한도(폴백) — 이 줄까지 매수 {running + cost:,}원 > 예수금 {buy_fallback_deposit:,}원, 생략"
                    rec["skipped"] += 1
                    continue
                running += cost
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
            last_fail = r.message
            rec["failed"] += 1
            streak += 1
            logger.warning("auto-exec failed pid=%s %s: %s", pf.id, r.line_key, exc)
    summary = {"date": today.isoformat(), "at": now.isoformat(), "open": open_px, "gap_hit": gap_hit,
               **{k: rec[k] for k in ("submitted", "skipped_gap", "skipped", "failed")}}
    _set_pf_auto_state(pf, fail_streak=streak, last_run=summary)
    if streak >= FAIL_STREAK_PAUSE:
        pause_portfolio(pf, f"발주 연속 실패 {streak}회 — 마지막 오류: {last_fail}", now)


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
    """장 마감 대조에서 **위험한** 불일치가 있으면 무인 실행을 멈춘다.

    위험 = unplanned(계획에 없던 거래)·excess(계획 초과 체결). short(부분·미달 체결)는 지정가의 정상 결과라 정지하지 않는다
    (모든 warn 을 정지 사유로 삼으면 부분체결이 잦은 그리드에서 거의 매일 멈춘다 — 2026-09-06 검토).
    """
    danger = [it for it in ((reconcile or {}).get("items") or []) if it.get("kind") in ("unplanned", "excess")]
    if not danger or pf_auto_state(pf)["paused"]:
        return False
    pause_portfolio(pf, "장 마감 대조 — " + "; ".join(w.get("text", "") for w in danger)[:160], now)
    return True
