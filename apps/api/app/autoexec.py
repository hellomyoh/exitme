"""무인 매매 단일 실행 (ADR-009, 2026-09-08 지시) — 실행일 09:01 에 그 순간의 원장·설정으로 주문표를 계산해 곧바로 발주하고 동결한다.

사용자 지시(2026-09-08): "매수/매도 수량은 실시간 반영 · 무인 대기 상태를 명확히 표기 · 설정의 on/off 플래그만 참조, 주문표의 버튼은
모두 제거, 해제하면 즉시 동작 · 무인을 취소하고 수동 입력할 수 있는 취소 버튼". 사용자 결정: 시장가 줄도 09:01 무인 포함 · 플래그를
끄면 살아 있는 무인 주문도 즉시 취소 · 플래그는 증권사 계좌별 · 현금 원천은 앱 원장 + 매수가능조회.

통제 (ADR-009 §2 — 모두 코드로 강제):
1. **계좌 플래그만** — `broker_credentials.auto_exec = {buy, sell, daily_buy_cap_pct}`. 포트에 연결된 계좌의 값이 유일한 판정 기준.
   둘 다 꺼짐 = 수동 모드(주문표만 계산·동결). 끄면 그 방향의 오늘 미체결 무인 주문을 증권사에서 취소한다.
2. **한 번에** — 승인 단계 없음. 09:01 에 `_portfolio_orders(force_freeze=True)` 로 계산 → 발주 → 스냅샷 동결. 하루 1회(락 + DB 마커).
3. **시가 확인 후** — 시가 ≤ 갭 취소 기준(전일 종가 − 1.5×ATR, 정확값)이면 그리드 매수 생략(skipped_gap). 백테스트와 같은 순서.
4. **잔고 대조** — 앱 원장의 전략 종목 보유 ≠ 계좌 잔고면 그날 전부 생략하고 정지(주문표가 틀린 전제로 계산된 것). 매도 수량 ≤ 보유.
5. **하루 매수 상한** — 총자산 대비 %(계좌 설정, 기본 20%). 초과분은 정지가 아니라 **축소**(수량을 남은 예산에 맞춤, 0 이면 생략).
6. **매수가능조회** — 발주 직전 KIS 주문가능 수량이 계획보다 적으면 그 수량으로 축소(미수 없음). 조회 실패 시 예수금 누적 규칙으로 폴백.
7. **자동 정지** — 발주 연속 실패 2회 또는 장 마감 대조의 위험한 불일치(계획 외 거래·초과 체결). 해제는 사용자.
8. **감시** — 09:15 워치독이 실행 기록 없는 포트를 지연 실행(락으로 중복 없음) + 경고. beat→큐→워커 하트비트(Redis) 를 컨테이너 헬스체크가 본다.
9. **감사 기록** — 줄마다 BrokerOrder(mode=auto) 최종 상태·사유, 포트 params.auto_exec.last_run 요약, 활동 로그·알림.
10. **장중 재시도** (2026-09-09 지시) — 09:01 결과가 실패·생략인 줄만 사용자가 '재시도'로 같은 절차(시가·갭 → 잔고 대조 → 상한·매수가능 → 발주)를
    다시 돌린다. 09:00~15:20 만, 이전 행은 기록으로 남고 줄마다 새 행(response.retry_of). 갭 생략·꺼진 방향·발주된 줄은 대상 외.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.broker import _acct_out, _client, _order_out, _owned, _resolve_codes, humanize_kis_error, line_key
from app.db import get_session
from app.models import BrokerCredential, BrokerOrder, Instrument, PortfolioPlan, TradePortfolio, UserSettings

logger = logging.getLogger(__name__)
router = APIRouter()
KST = timezone(timedelta(hours=9))

OPEN_TIME = time(9, 0)                # 동결·발주 기준 시각 (signals.FREEZE_TIME 과 같은 값)
RUN_GRACE = time(9, 5)                # 이 시각까지 실행 기록이 없으면 '실행 중', 이후엔 '기록 없음' 경고
LATE_RUN_LIMIT = time(9, 30)          # 지연 실행 상한 (2026-09-09 사고) — 스케줄러가 멈춘 뒤 따라잡기로 도착한 09:01/09:15 실행은 이 시각을 넘으면 발주하지 않는다
FAIL_STREAK_PAUSE = 2                 # 발주 연속 실패 n회 → 자동 정지
GRID_KINDS_PREFIX = "grid"            # 갭 취소 대상(그리드 매수) 종류 접두
DAILY_BUY_CAP_PCT_DEFAULT = 0.0       # 하루 매수 총액 상한 — 총자산 대비 %. 기본 0 = 없음 (2026-09-08 사용자 결정 "참고용, 사용하지 않음" — 진입 속도 제한이며 안전장치가 아님, docs/cold-start-entry-study §6)
LIVE_AUTO = ("submitted", "partial")  # 증권사에 살아 있는 무인 주문
HEARTBEAT_KEY = "autoexec:pipeline:heartbeat"   # beat → ingest 큐 → 워커 경로가 살아 있음을 60초마다 기록 (TTL 180초)
HEARTBEAT_TTL = 180
RUNNING_KEY = "autoexec:running"                 # 09:01 실행 중 표시 — 시세·예상 시가 폴링이 이 동안 KIS 호출을 양보한다 (2026-09-09 유량 사고)
RUNNING_TTL = 180
PORTFOLIO_GAP_SEC = 1.0                          # 포트 사이 간격 — 계좌가 같은 앱키를 쓰면 연속 호출이 한 초에 몰린다
RETRY_WINDOW = (time(9, 0), time(15, 20))        # 장중 재시도 허용 구간 (2026-09-09 지시 "API 실패 시 취소 대신 재시도") — 동시호가 전까지
RETRYABLE = ("failed", "skipped")                # 재시도 대상 상태. skipped_gap(그날의 전략 판정)·발주됨·체결은 대상 외
REORDERABLE = ("failed", "skipped", "cancelled")  # 줄별 '재등록' 대상 — 사용자가 그 줄을 직접 취소한 경우 포함 (2026-09-10 지시)
RETRY_LOCK_TTL = 60

SIDE_KO = {"buy": "매수", "sell": "매도"}


# ── 설정: 증권사 계좌별 플래그 ─────────────────────────────────────────────────────

def _switches(v: dict | None) -> dict:
    v = v or {}
    pct = v.get("daily_buy_cap_pct", DAILY_BUY_CAP_PCT_DEFAULT)
    try:
        pct = float(pct) if pct is not None else DAILY_BUY_CAP_PCT_DEFAULT
    except (TypeError, ValueError):
        pct = DAILY_BUY_CAP_PCT_DEFAULT
    return {"buy": bool(v.get("buy", False)), "sell": bool(v.get("sell", False)), "daily_buy_cap_pct": max(0.0, min(100.0, pct))}


def user_auto_exec(session: Session, user_id: int) -> dict:
    """사용자 기본값 — 새 계좌에 적용되고 '일괄 적용'의 저장소. 판정에는 쓰지 않는다(계좌별 플래그가 진실, 0024)."""
    row = session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    return _switches(row.auto_exec if row else None)


def account_auto_exec(cred: BrokerCredential | None) -> dict:
    """계좌별 플래그 (0024, 2026-09-07 지시 "계좌별로 설정") — 09:01 실행·상태 표기·해제 취소가 모두 **포트에 연결된 계좌의 이 값**으로 판정한다.
    계좌가 없으면 전부 꺼짐."""
    if cred is None:
        return {"buy": False, "sell": False, "daily_buy_cap_pct": DAILY_BUY_CAP_PCT_DEFAULT}
    return _switches(getattr(cred, "auto_exec", None))


def auto_exec_settings_view(session: Session, user_id: int) -> dict:
    """설정 화면용 — 기본값 + 계좌별 플래그(연결 포트 이름 포함). 최상위 buy/sell 은 기본값(하위호환)."""
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
    daily_buy_cap_pct: float | None = Field(default=None, ge=0, le=100)   # 생략 = 유지


class AccountAutoExecIn(BaseModel):
    buy: bool | None = None
    sell: bool | None = None
    daily_buy_cap_pct: float | None = Field(default=None, ge=0, le=100)


@router.get("/settings/auto-exec")
def get_auto_exec_setting(user_id: int = Depends(current_user_id),
                          session: Session = Depends(get_session)) -> dict:
    return auto_exec_settings_view(session, user_id)


def _cancel_for_turned_off(session: Session, user_id: int, cred: BrokerCredential, before: dict, after: dict,
                           now: datetime, client_factory=None) -> dict:
    """플래그를 끈 방향의 **오늘 살아 있는 무인 주문**을 증권사에서 취소한다 (사용자 결정 2026-09-08 "해제하면 살아 있는 주문도 즉시 취소")."""
    off = [s for s in ("buy", "sell") if before.get(s) and not after.get(s)]
    total = {"cancelled": 0, "failed": 0}
    if not off:
        return total
    today = now.date()
    for pf in session.scalars(select(TradePortfolio).where(TradePortfolio.broker_credential_id == cred.id,
                                                            TradePortfolio.market == "KR")).all():
        for side in off:
            r = cancel_live_auto_orders(session, pf, cred, today, side=side, reason=f"설정에서 무인 {SIDE_KO[side]} 해제",
                                        now=now, client_factory=client_factory)
            total["cancelled"] += r["cancelled"]
            total["failed"] += r["failed"]
    return total


@router.put("/settings/auto-exec")
def put_auto_exec_setting(body: AutoExecSettingIn, user_id: int = Depends(current_user_id),
                          session: Session = Depends(get_session)) -> dict:
    """기본값 저장 + 등록된 모든 계좌에 일괄 적용. 끈 방향의 오늘 무인 주문은 즉시 취소."""
    from app.activity import log_event
    from app.settings import _row

    now = datetime.now(KST)
    row = _row(session, user_id)
    cur = _switches(row.auto_exec)
    pct = body.daily_buy_cap_pct if body.daily_buy_cap_pct is not None else cur["daily_buy_cap_pct"]
    new = {"buy": body.buy, "sell": body.sell, "daily_buy_cap_pct": float(pct)}
    row.auto_exec = dict(new)
    total = {"cancelled": 0, "failed": 0}
    for cred in session.scalars(select(BrokerCredential).where(BrokerCredential.user_id == user_id)).all():
        before = account_auto_exec(cred)
        cred.auto_exec = dict(new)
        c = _cancel_for_turned_off(session, user_id, cred, before, new, now)
        total["cancelled"] += c["cancelled"]
        total["failed"] += c["failed"]
    log_event(session, user_id, "autoexec.account_setting",
              "모든 계좌 — " + " · ".join(f"무인 {SIDE_KO[k]} {'허용' if new[k] else '꺼짐'}" for k in ("buy", "sell"))
              + f" · 하루 매수 상한 {new['daily_buy_cap_pct']:g}%" + (f" · 살아 있는 무인 주문 {total['cancelled']}건 취소" if total["cancelled"] else ""),
              level="warn" if (new["buy"] or new["sell"] or total["cancelled"]) else "info", data={**new, **total}, at=now)
    session.commit()
    logger.info("auto-exec setting user=%s (all accounts) %s %s", user_id, new, total)
    return {**auto_exec_settings_view(session, user_id), **total}


@router.put("/settings/auto-exec/accounts/{aid}")
def put_account_auto_exec(aid: int, body: AccountAutoExecIn, user_id: int = Depends(current_user_id),
                          session: Session = Depends(get_session)) -> dict:
    """계좌 하나의 무인 매수/매도 플래그·하루 매수 상한 (2026-09-07 지시). 생략한 키는 유지. 끈 방향의 오늘 무인 주문은 즉시 취소."""
    from app.activity import log_event

    cred = session.get(BrokerCredential, aid)
    if cred is None or cred.user_id != user_id:
        raise HTTPException(status_code=404, detail="account not found")
    now = datetime.now(KST)
    before = account_auto_exec(cred)
    cur = dict(before)
    for k in ("buy", "sell"):
        val = getattr(body, k)
        if val is not None:
            cur[k] = bool(val)
    if body.daily_buy_cap_pct is not None:
        cur["daily_buy_cap_pct"] = float(body.daily_buy_cap_pct)
    cred.auto_exec = cur
    total = _cancel_for_turned_off(session, user_id, cred, before, cur, now)
    log_event(session, user_id, "autoexec.account_setting",
              f"계좌 {cred.label} — " + " · ".join(f"무인 {SIDE_KO[k]} {'허용' if cur[k] else '꺼짐'}" for k in ("buy", "sell"))
              + f" · 하루 매수 상한 {cur['daily_buy_cap_pct']:g}%" + (f" · 살아 있는 무인 주문 {total['cancelled']}건 취소" if total["cancelled"] else "")
              + (f" · 취소 실패 {total['failed']}건" if total["failed"] else ""),
              level="error" if total["failed"] else ("warn" if (cur["buy"] or cur["sell"] or total["cancelled"]) else "info"),
              data={"account_id": cred.id, **cur, **total}, at=now)
    session.commit()
    logger.info("auto-exec account setting cred=%s %s %s", cred.id, cur, total)
    return {**auto_exec_settings_view(session, user_id), **total}


# ── 포트 단위 상태 (params.auto_exec) ─────────────────────────────────────────────

def pf_auto_state(pf: TradePortfolio) -> dict:
    st = dict(((pf.params or {}).get("auto_exec") or {}))
    return {"paused": bool(st.get("paused", False)), "paused_reason": st.get("paused_reason"),
            "paused_at": st.get("paused_at"), "fail_streak": int(st.get("fail_streak", 0) or 0),
            "last_run": st.get("last_run"),
            # 사용자 취소(수동 전환) — {date, at, cancelled}. 실행일이 지나면 자연히 무효 (ADR-009 §4)
            "skip": st.get("skip")}


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


def _default_exec_day(session: Session, pf: TradePortfolio, today: date) -> date:
    """화면이 실행일을 주지 않을 때(챗봇 등) — 오늘 이후 가장 최근 주문표 스냅샷의 실행일, 없으면 오늘."""
    from sqlalchemy import func

    d = session.scalar(select(func.max(PortfolioPlan.trade_date)).where(PortfolioPlan.portfolio_id == pf.id,
                                                                        PortfolioPlan.trade_date >= today))
    return d or today


def auto_exec_state(pf: TradePortfolio, cred: BrokerCredential | None, allowed: dict, st: dict, exec_day: date | None,
                    now: datetime) -> dict:
    """주문표 상단 한 줄 — 무인 대기인지 아닌지를 사유와 함께 (요청 2, ADR-009 §3). code: off | paused | skipped_user | waiting | running | ran | missed"""
    from app.signals import freeze_at

    on = [SIDE_KO[s] for s in ("buy", "sell") if allowed.get(s)]
    if pf.market != "KR":
        return {"code": "off", "label": "수동 모드 — 무인 매매는 국내 포트만 지원", "detail": None}
    if cred is None:
        return {"code": "off", "label": "수동 모드 — 연결된 증권사 계좌 없음", "detail": "계좌 연결 후 설정 › 무인 실행에서 켜면 09:01 발주"}
    if not on:
        return {"code": "off", "label": f"수동 모드 — 계좌 '{cred.label}' 무인 매수·매도 꺼짐",
                "detail": "주문표는 참고용 — HTS 에서 직접 주문 · 켜기: 설정 › 무인 실행"}
    if st.get("paused"):
        return {"code": "paused", "label": f"정지 — {st.get('paused_reason') or ''}",
                "detail": f"{(st.get('paused_at') or '')[:16].replace('T', ' ')}부터 정지 · 계좌·기록 확인 후 '다시 켜기'"}
    ed = exec_day
    skip = st.get("skip") or {}
    last = st.get("last_run") or {}
    who = "·".join(on)
    if ed is not None and skip.get("date") == ed.isoformat():
        n = int(skip.get("cancelled") or 0)
        return {"code": "skipped_user", "label": f"무인 취소됨 — {ed.isoformat()} 은 수동 처리",
                "detail": (f"무인 주문 {n}건 취소 · " if n else "") + ("09:00 전 '되돌리기' 가능 · " if now < freeze_at(ed) else "") + "다음 실행일 자동 복귀"}
    if ed is not None and last.get("date") == ed.isoformat():
        parts = [f"발주 {last.get('submitted', 0)}건"]
        if last.get("skipped_gap"):
            parts.append(f"갭 취소 생략 {last['skipped_gap']}건")
        if last.get("skipped"):
            parts.append(f"생략 {last['skipped']}건")
        if last.get("failed"):
            parts.append(f"실패 {last['failed']}건")
        if last.get("clipped"):
            parts.append(f"축소 {last['clipped']}건")
        note = last.get("note")
        label = {"manual": "수동 모드 — 주문표만 동결", "no_orders": "발주 완료 — 오늘 주문 없음", "skipped_user": "무인 취소됨 — 수동 처리",
                 "stale_plan": "발주 안 함 — 주문표 기준일 불일치", "paused": "정지 — 발주 안 함",
                 "late": f"발주 안 함 — {LATE_RUN_LIMIT:%H:%M} 지연 상한 초과"}.get(note)
        if label is None:
            label = f"무인 {who} 완료 {str(last.get('at') or '')[11:16]} — " + " · ".join(parts)
        detail = []
        if last.get("open") is not None:
            detail.append(f"시가 {int(last['open']):,}원" + (" — 갭 취소 발동" if last.get("gap_hit") else ""))
        if last.get("trigger") == "watchdog":
            detail.append("09:15 감시가 지연 실행")
        if last.get("reason"):
            detail.append(str(last["reason"]))
        rt = last.get("retry")
        if rt:
            rparts = [f"발주 {rt.get('submitted', 0)}건"]
            for k, ko in (("skipped_gap", "갭 취소 생략"), ("skipped", "생략"), ("failed", "실패"), ("clipped", "축소")):
                if rt.get(k):
                    rparts.append(f"{ko} {rt[k]}건")
            detail.append(f"재시도 {str(rt.get('at') or '')[11:16]} — " + " · ".join(rparts))
        return {"code": "ran", "label": label, "detail": " · ".join(detail) or None, "run": last}
    if ed is None:
        return {"code": "waiting", "label": f"무인 {who} 대기", "detail": None}
    if ed > now.date() or now < freeze_at(ed):
        return {"code": "waiting", "label": f"무인 {who} 대기 — {ed.isoformat()} 09:01 발주 예정",
                "detail": "09:00 까지 등록분 반영 · 09:01 시가 확인 후 발주"}
    if ed == now.date() and now.time() < RUN_GRACE:
        return {"code": "running", "label": "09:01 무인 실행 중", "detail": "결과가 곧 표시됩니다"}
    return {"code": "missed", "label": f"경고 — {ed.isoformat()} 09:01 실행 기록 없음",
            "detail": "09:15 감시가 지연 실행 · 그래도 없으면 워커·스케줄러 상태 확인"}


def auto_exec_view(session: Session, pf: TradePortfolio, exec_day: date | None = None, now: datetime | None = None) -> dict:
    """주문표 화면·챗봇용 — 연결 계좌 플래그 + 포트 상태 + 마지막 실행 요약 + 상태 한 줄(state)."""
    now = now or datetime.now(KST)
    cred = session.get(BrokerCredential, pf.broker_credential_id) if pf.broker_credential_id else None
    allowed = account_auto_exec(cred)
    st = pf_auto_state(pf)
    ed = exec_day or _default_exec_day(session, pf, now.date())
    last = st.get("last_run") or {}
    # 재시도 대상 줄 수 — 실행 기록이 있는 실행일에서, 실패·생략(갭 제외)이고 방향이 켜진 줄 (화면의 '재시도' 버튼 표시 기준)
    retryable = (len(retryable_rows(session, pf, ed, allowed)) if last.get("date") == ed.isoformat() and not st["paused"]
                 and (st.get("skip") or {}).get("date") != ed.isoformat() else 0)
    return {"allowed": allowed,
            "account": {"id": cred.id, "label": cred.label, "env": cred.env} if cred else None,
            **st, "exec_day": ed.isoformat(), "retryable": retryable,
            "state": auto_exec_state(pf, cred, allowed, st, ed, now)}


@router.get("/portfolio/{pid}/auto-exec")
def get_portfolio_auto_exec(pid: int, date_: date | None = Query(default=None, alias="date"),
                            user_id: int = Depends(current_user_id), session: Session = Depends(get_session)) -> dict:
    pf = _owned(session, pid, user_id)
    return auto_exec_view(session, pf, date_)


@router.post("/portfolio/{pid}/auto-exec/resume")
def resume_portfolio_auto_exec(pid: int, user_id: int = Depends(current_user_id),
                               session: Session = Depends(get_session)) -> dict:
    """자동 정지 해제 — 사용자가 사유를 확인한 뒤 누른다. 실패 카운터도 초기화."""
    pf = _owned(session, pid, user_id)
    _set_pf_auto_state(pf, paused=False, paused_reason=None, paused_at=None, fail_streak=0)
    session.commit()
    return auto_exec_view(session, pf)


# ── 사용자 취소 (수동 전환) — 요청 4 ───────────────────────────────────────────────

class SkipIn(BaseModel):
    date: date   # 주문표 실행일 (signal.exec_day)


def cancel_live_auto_orders(session: Session, pf: TradePortfolio, cred: BrokerCredential | None, plan_date: date,
                            side: str | None = None, reason: str = "취소", now: datetime | None = None,
                            client_factory=None) -> dict:
    """증권사에 살아 있는(submitted·partial) 이 실행일의 무인 주문을 취소한다. 줄 단위 실패는 기록하고 계속. 체결된 줄은 대상 외."""
    now = now or datetime.now(KST)
    q = select(BrokerOrder).where(BrokerOrder.portfolio_id == pf.id, BrokerOrder.plan_date == plan_date,
                                  BrokerOrder.mode == "auto", BrokerOrder.status.in_(LIVE_AUTO))
    if side:
        q = q.where(BrokerOrder.side == side)
    rows = session.scalars(q.order_by(BrokerOrder.id)).all()
    res: dict = {"cancelled": 0, "failed": 0, "items": []}
    if not rows:
        return res
    client = None
    for r in rows:
        try:
            if cred is None:
                raise RuntimeError("연결된 증권사 계좌가 없어 취소 요청을 보낼 수 없습니다")
            client = client or (client_factory or _client)(cred)
            orgno = str(((r.response or {}).get("order") or {}).get("KRX_FWDG_ORD_ORGNO") or "")
            resp = client.cancel_order(r.order_no or "", orgno=orgno)
            r.status, r.message = "cancelled", f"{reason} — " + (resp.get("msg") or "취소됨")
            res["cancelled"] += 1
        except Exception as exc:  # noqa: BLE001
            r.message = f"{reason} 취소 실패: {humanize_kis_error(str(exc)[:160])}"
            res["failed"] += 1
            logger.warning("auto-exec cancel failed pid=%s order=%s: %s", pf.id, r.id, exc)
        res["items"].append(_order_out(r))
    from app.activity import log_event

    log_event(session, pf.user_id, "autoexec.cancel",
              f"무인 주문 취소 — {reason}: {res['cancelled']}건 취소" + (f" · 실패 {res['failed']}건" if res["failed"] else ""),
              level="error" if res["failed"] else "warn", portfolio_id=pf.id,
              data={"plan_date": plan_date.isoformat(), "side": side, "cancelled": res["cancelled"], "failed": res["failed"]}, at=now)
    return res


@router.post("/portfolio/{pid}/auto-exec/skip")
def skip_auto_exec(pid: int, body: SkipIn, user_id: int = Depends(current_user_id),
                   session: Session = Depends(get_session)) -> dict:
    """이번 실행일 무인 취소 → 수동. 09:00 전이면 그날 발주를 건너뛰고, 09:01 후면 살아 있는 무인 주문을 증권사에서 취소한다.
    다음 실행일에는 자동으로 무인 대기로 돌아간다."""
    from app.activity import log_event
    from app.signals import freeze_at

    pf = _owned(session, pid, user_id)
    now = datetime.now(KST)
    if body.date < now.date():
        raise HTTPException(status_code=409, detail="지난 실행일은 취소할 수 없습니다")
    cred = session.get(BrokerCredential, pf.broker_credential_id) if pf.broker_credential_id else None
    res = {"cancelled": 0, "failed": 0}
    if now >= freeze_at(body.date):
        res = cancel_live_auto_orders(session, pf, cred, body.date, reason="사용자 취소 — 수동 전환", now=now)
    _set_pf_auto_state(pf, skip={"date": body.date.isoformat(), "at": now.isoformat(timespec="minutes"), "cancelled": res["cancelled"]})
    log_event(session, user_id, "autoexec.skip",
              f"무인 취소 — {body.date.isoformat()} 은 수동 처리" + (f" · 살아 있는 주문 {res['cancelled']}건 취소" if res["cancelled"] else "")
              + (f" · 취소 실패 {res['failed']}건" if res["failed"] else ""),
              level="error" if res["failed"] else "warn", portfolio_id=pid, data={"date": body.date.isoformat(), **{k: res[k] for k in ("cancelled", "failed")}}, at=now)
    session.commit()
    return {**auto_exec_view(session, pf, body.date, now), "cancelled": res["cancelled"], "failed": res["failed"]}


@router.post("/portfolio/{pid}/auto-exec/unskip")
def unskip_auto_exec(pid: int, user_id: int = Depends(current_user_id),
                     session: Session = Depends(get_session)) -> dict:
    """취소 되돌리기 — 실행일 09:00 전에만 (이미 09:01 이 지났으면 그날은 수동으로 남는다)."""
    from app.activity import log_event
    from app.signals import freeze_at

    pf = _owned(session, pid, user_id)
    now = datetime.now(KST)
    skip = pf_auto_state(pf).get("skip") or {}
    if not skip.get("date"):
        raise HTTPException(status_code=409, detail="취소된 실행일이 없습니다")
    d = date.fromisoformat(skip["date"])
    if now >= freeze_at(d):
        raise HTTPException(status_code=409, detail=f"{d.isoformat()} 09:00 이 지나 되돌릴 수 없습니다 — 그날은 수동으로 처리하세요")
    _set_pf_auto_state(pf, skip=None)
    log_event(session, user_id, "autoexec.skip", f"무인 취소 되돌림 — {d.isoformat()} 09:01 발주 예정", portfolio_id=pid, data={"date": d.isoformat()}, at=now)
    session.commit()
    return auto_exec_view(session, pf, d, now)


# ── 실행 (워커 09:01 · 09:15 감시) ────────────────────────────────────────────────

def _acquire_day_lock(pf_id: int, today: date) -> bool:
    """하루 1회 실행 보장 — Redis SET NX(6시간). Redis 가 없으면 True(DB 마커는 호출자가 확인)."""
    try:
        import redis as sync_redis

        from app.config import get_settings

        r = sync_redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=1)
        return bool(r.set(f"autoexec:{pf_id}:{today.isoformat()}", "1", nx=True, ex=6 * 3600))
    except Exception:  # noqa: BLE001 — Redis 부재(테스트) 시 DB 마커로 대신
        return True


def set_running(on: bool) -> None:
    """실행 중 플래그 (Redis). 폴링 태스크가 `is_running()` 으로 확인해 09:01 전후 KIS 호출을 건너뛴다."""
    try:
        import redis as sync_redis

        from app.config import get_settings

        r = sync_redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=1)
        if on:
            r.set(RUNNING_KEY, datetime.now(KST).isoformat(timespec="seconds"), ex=RUNNING_TTL)
        else:
            r.delete(RUNNING_KEY)
    except Exception:  # noqa: BLE001
        pass


def is_running() -> bool:
    try:
        import redis as sync_redis

        from app.config import get_settings

        r = sync_redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=1)
        return bool(r.get(RUNNING_KEY))
    except Exception:  # noqa: BLE001
        return False


def touch_heartbeat() -> bool:
    """beat → 큐 → 워커 경로 하트비트 (60초 주기 태스크가 호출). 컨테이너 헬스체크가 TTL 안의 키를 확인한다."""
    try:
        import redis as sync_redis

        from app.config import get_settings

        r = sync_redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=1)
        r.set(HEARTBEAT_KEY, datetime.now(KST).isoformat(timespec="seconds"), ex=HEARTBEAT_TTL)
        return True
    except Exception:  # noqa: BLE001
        return False


def heartbeat_age() -> int | None:
    """마지막 하트비트로부터 몇 초 지났나 (없으면 None) — 상태 도구·감시 로그용."""
    try:
        import redis as sync_redis

        from app.config import get_settings

        r = sync_redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=1)
        v = r.get(HEARTBEAT_KEY)
        if not v:
            return None
        return int((datetime.now(KST) - datetime.fromisoformat(v)).total_seconds())
    except Exception:  # noqa: BLE001
        return None


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


def _default_plan(session: Session, pf: TradePortfolio, now: datetime) -> dict:
    """09:01 주문표 계산 + 동결 — 화면(/signals/daily)과 같은 함수, force_freeze 로 그날의 주문표를 확정한다 (ADR-009 §1)."""
    from app.signals import _portfolio_orders

    return _portfolio_orders(session, pf.id, pf.user_id, force_freeze=True, now=now)


def run_auto_execution(session: Session, now: datetime | None = None, client_factory=None,
                       sleep_fn=_time.sleep, plan_fn=None, only_credential_ids: set[int] | None = None,
                       trigger: str = "beat", late_limit: time | None = LATE_RUN_LIMIT) -> dict:
    """국내 포트마다 주문표 계산·동결 → (연결 계좌 플래그) → 시가 → 잔고 → 상한 → 발주. 포트별 실패는 기록만 하고 계속.

    only_credential_ids: 이 계좌들에 연결된 포트만(테스트·수동 재실행용). trigger: beat(09:01) | watchdog(09:15 지연 실행).
    late_limit: 이 시각(KST)을 넘겨 도착한 실행은 발주하지 않고 'late' 로 기록한다 — Celery beat 는 멈춘 사이 지나간 크론을 기동 즉시 보내므로
    (2026-09-09 사고: 낡은 상태 파일로 배포마다 지난 배치 재실행), 정오에 09:01 논리로 발주하는 일을 막는다. None = 상한 없음.
    """
    now = now or datetime.now(KST)
    today = now.date()
    client_factory = client_factory or _client
    plan_fn = plan_fn or _default_plan
    out: dict = {"date": today.isoformat(), "trigger": trigger, "portfolios": []}
    # 계좌 미연결 포트도 돈다 — 주문표 계산·동결만 하고(수동 모드) 발주는 없다. 화면의 '09:00 동결'이 모든 국내 포트에 같은 뜻이 되도록
    q = select(TradePortfolio).where(TradePortfolio.market == "KR")
    if only_credential_ids:
        q = q.where(TradePortfolio.broker_credential_id.in_(list(only_credential_ids)))
    set_running(True)
    first = True
    for pf in session.scalars(q.order_by(TradePortfolio.id)).all():
        rec: dict = {"portfolio_id": pf.id, "name": pf.name, "exec_day": None, "submitted": 0, "skipped_gap": 0,
                     "skipped": 0, "failed": 0, "note": None}
        try:
            if late_limit is not None and now.time() > late_limit:
                # 지연 상한 초과 — 오늘 이미 돈 포트는 그대로, 아니면 발주 없이 기록·경고만 (사용자는 HTS 로 직접)
                if ((pf_auto_state(pf).get("last_run") or {}).get("date") == today.isoformat()):
                    rec["error"] = "already-ran"
                else:
                    rec["exec_day"] = today.isoformat()
                    _finish(pf, rec, today, now, trigger, "late",
                            f"{now:%H:%M} 도착 — 지연 상한 {late_limit:%H:%M} 초과(스케줄러 중단 뒤 따라잡기). 오늘 무인 발주 없음, 필요하면 직접 주문")
                    _log_run(session, pf, rec, now, trigger)
                session.commit()
                out["portfolios"].append(rec)
                continue
            if not first:
                sleep_fn(PORTFOLIO_GAP_SEC)   # 같은 앱키의 연속 호출이 한 초에 몰리지 않게 (2026-09-09)
            first = False
            _execute_portfolio(session, pf, today, now, client_factory, sleep_fn, plan_fn, rec, trigger)
            if rec.get("error") not in ("already-ran", "locked"):
                _log_run(session, pf, rec, now, trigger)
            session.commit()
        except Exception as exc:  # noqa: BLE001 — 포트 단위 실패는 기록하고 다음 포트
            session.rollback()
            detail = getattr(exc, "detail", None) or str(exc)
            rec["error"] = str(detail)[:200]
            logger.exception("auto-exec portfolio failed pid=%s", pf.id)
            try:
                from app.activity import log_event

                log_event(session, pf.user_id, "autoexec.error", f"무인 실행 오류 — {humanize_kis_error(str(detail)[:200])}",
                          level="error", portfolio_id=pf.id, at=now)
                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
        out["portfolios"].append(rec)
    set_running(False)
    return out


def run_watchdog(session: Session, now: datetime | None = None, client_factory=None, sleep_fn=_time.sleep, plan_fn=None,
                 only_credential_ids: set[int] | None = None) -> dict:
    """09:15 감시 — 09:01 이 돌지 않은 포트를 지연 실행한다(락·마커로 중복 없음). 실행한 포트는 경고 로그·알림으로 드러낸다 (ADR-009 §5)."""
    out = run_auto_execution(session, now=now, client_factory=client_factory, sleep_fn=sleep_fn, plan_fn=plan_fn,
                             only_credential_ids=only_credential_ids, trigger="watchdog")
    out["heartbeat_age"] = heartbeat_age()
    out["late"] = [r["portfolio_id"] for r in out["portfolios"] if r.get("error") not in ("already-ran", "locked") and not r.get("error")]
    return out


def _log_run(session: Session, pf: TradePortfolio, rec: dict, now: datetime, trigger: str) -> None:
    """실행 요약 한 줄 — 줄별 결과는 BrokerOrder 가 원천이므로 여기서는 건수만. 수동 모드는 조용히(매일 아침 잡음 방지)."""
    from app.activity import log_event

    note = rec.get("note")
    if note == "manual":
        return
    late = " (09:01 배치 미실행 → 09:15 감시가 지연 실행)" if trigger == "watchdog" else ""
    if note in ("stale_plan", "paused", "skipped_user", "no_orders", "late"):
        ko = {"stale_plan": f"발주 안 함 — {rec.get('reason') or '주문표 기준일 불일치'}", "paused": f"발주 안 함 — 정지 상태 ({rec.get('reason') or ''})",
              "skipped_user": "발주 안 함 — 사용자가 이번 실행일 무인을 취소(수동)", "no_orders": "오늘 주문 없음",
              "late": f"발주 안 함 — {rec.get('reason') or '지연 상한 초과'}"}[note]
        log_event(session, pf.user_id, "autoexec.run", f"무인 실행 {now:%H:%M}{late} — {ko}",
                  level="error" if note in ("stale_plan", "late") else ("warn" if note in ("paused", "skipped_user") or late else "info"),
                  portfolio_id=pf.id, data={k: v for k, v in rec.items() if k != "name"}, at=now)
        return
    parts = [f"발주 {rec['submitted']}건"]
    if rec["skipped_gap"]:
        parts.append(f"갭 취소 생략 {rec['skipped_gap']}건")
    if rec["skipped"]:
        parts.append(f"생략 {rec['skipped']}건")
    if rec["failed"]:
        parts.append(f"실패 {rec['failed']}건")
    if rec.get("clipped"):
        parts.append(f"축소 {rec['clipped']}건")
    lvl = "error" if rec["failed"] else ("warn" if (rec["skipped"] or rec["skipped_gap"] or late) else "info")
    log_event(session, pf.user_id, "autoexec.run", f"무인 실행 {now:%H:%M}{late} — " + " · ".join(parts), level=lvl,
              portfolio_id=pf.id, data={k: v for k, v in rec.items() if k != "name"}, at=now)


def _ledger_holdings(session: Session, pid: int) -> dict[str, int]:
    """앱 원장(잔여 로트) 기준 종목별 보유 수량 — 09:01 사전 대조에서 계좌 잔고와 비교한다."""
    from app.models import PositionLot

    out: dict[str, int] = {}
    for lot in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pid)).all():
        if lot.qty_open > 0:
            inst = session.get(Instrument, lot.instrument_id)
            if inst is not None:
                out[inst.code] = out.get(inst.code, 0) + int(lot.qty_open)
    return out


def _latest_close(session: Session, code: str) -> int:
    from app.portfolios import latest_close

    inst = session.scalar(select(Instrument).where(Instrument.code == code))
    lc = latest_close(session, inst.id) if inst else None
    return int(lc[0]) if lc else 0


def _skip(rows: list[BrokerOrder], status: str, message: str, rec: dict, key: str) -> None:
    for r in rows:
        r.status = status
        r.message = message
    rec[key] += len(rows)


def _finish(pf: TradePortfolio, rec: dict, today: date, now: datetime, trigger: str, note: str, reason: str | None = None, **extra) -> None:
    rec["note"], rec["reason"] = note, reason
    _set_pf_auto_state(pf, last_run={"date": today.isoformat(), "at": now.isoformat(), "trigger": trigger, "note": note, "reason": reason,
                                     "exec_day": rec.get("exec_day"), **{k: rec[k] for k in ("submitted", "skipped_gap", "skipped", "failed")}, **extra})


def _execute_portfolio(session: Session, pf: TradePortfolio, today: date, now: datetime, client_factory, sleep_fn, plan_fn,
                       rec: dict, trigger: str) -> None:
    state = pf_auto_state(pf)
    last = state.get("last_run") or {}
    if last.get("date") == today.isoformat():          # DB 마커 — 같은 날 두 번 돌지 않는다
        rec["error"] = "already-ran"
        return
    if not _acquire_day_lock(pf.id, today):
        rec["error"] = "locked"
        return
    cred = session.get(BrokerCredential, pf.broker_credential_id) if pf.broker_credential_id else None
    allowed = account_auto_exec(cred)
    # ① 주문표 계산 + 동결 — 플래그와 무관하게 모든 연결 포트가 09:00 상태로 동결된다 (수동 포트도 화면 = 그 시각 계획)
    plan = plan_fn(session, pf, now)
    exec_day = date.fromisoformat(str(plan.get("exec_day")))
    rec["exec_day"] = exec_day.isoformat()
    lines = list(plan.get("orders") or [])
    if exec_day != today:
        # 어제 일봉이 없거나(수집 실패) 휴장이 캘린더에 없다 — 틀린 기준으로 발주하지 않는다
        why = (f"계산된 실행일 {exec_day} 이 오늘보다 앞섬 — 기준일 이후 일봉 누락 또는 휴장 미등록(캘린더 갱신 필요)" if exec_day < today
               else f"계산된 실행일 {exec_day} 이 오늘보다 뒤 — 오늘이 캘린더상 휴장")
        _finish(pf, rec, today, now, trigger, "stale_plan", why)
        return
    if not (allowed["buy"] or allowed["sell"]):
        _finish(pf, rec, today, now, trigger, "manual", "계좌 무인 매수·매도 꺼짐")
        return
    if state["paused"]:
        _finish(pf, rec, today, now, trigger, "paused", state["paused_reason"] or "")
        return
    if (state.get("skip") or {}).get("date") == today.isoformat():
        _finish(pf, rec, today, now, trigger, "skipped_user", "사용자 취소")
        return
    if not lines:
        _finish(pf, rec, today, now, trigger, "no_orders")
        return
    code_200, code_lev = _resolve_codes(session, pf)
    rows: list[BrokerOrder] = []
    for o in lines:
        code = code_200 if o.get("instrument") == "K200" else code_lev
        r = BrokerOrder(portfolio_id=pf.id, broker_credential_id=cred.id, plan_date=today, line_key=line_key(o), code=code,
                        instrument=o["instrument"], kind=o["kind"], side=o["side"], otype=o.get("otype") or ("limit" if o.get("price") else "market"),
                        qty=int(o["qty"]), price=int(o["price"]) if o.get("price") else None, mode="auto", status="skipped",
                        response={"plan_qty": int(o["qty"])})
        session.add(r)
        rows.append(r)
    session.flush()
    # ② 플래그 — 꺼진 방향은 수동
    keep: list[BrokerOrder] = []
    for r in rows:
        if not allowed.get(r.side, False):
            r.message = f"무인 {SIDE_KO[r.side]} 꺼짐 — 수동 처리"
            rec["skipped"] += 1
        else:
            keep.append(r)
    client = client_factory(cred)
    res = _place_lines(session, pf, client, keep, plan, allowed, state, rec, now, sleep_fn, code_200, code_lev, who="09:01")
    rec["clipped"] = res["clipped"]
    _finish(pf, rec, today, now, trigger, "ran", open=res["open"], gap_hit=res["gap_hit"], clipped=res["clipped"])
    _set_pf_auto_state(pf, fail_streak=res["streak"])
    if res["streak"] >= FAIL_STREAK_PAUSE:
        pause_portfolio(pf, f"발주 연속 실패 {res['streak']}회 — 마지막 오류: {res['last_fail']}", now)


def _place_lines(session: Session, pf: TradePortfolio, client, keep: list[BrokerOrder], plan: dict, allowed: dict, state: dict,
                 rec: dict, now: datetime, sleep_fn, code_200: str, code_lev: str, who: str = "09:01") -> dict:
    """③ 시가·갭 → ④ 잔고 대조·매도 한도 → ⑤ 상한·매수가능 → 발주. 09:01 실행과 장중 재시도가 **같은 절차**를 쓴다 (ADR-009 §2, 통제 10).

    keep: 발주 후보 행(status 는 'skipped' 로 시작, 여기서 submitted/skipped/skipped_gap/failed 로 확정). rec 의 건수를 누적한다.
    who: 정지 사유에 붙는 주체("09:01" / "재시도 10:12"). 반환 {open, gap_hit, clipped, streak, last_fail}.
    """
    # ③ 시가 확인 → 갭 취소 (정확값)
    open_px = _read_open(client, code_200, sleep_fn=sleep_fn) if keep else None
    gap_exact = plan.get("gap_cancel_exact") or plan.get("gap_cancel_below")
    gap_hit = bool(keep and open_px is not None and gap_exact and open_px <= float(gap_exact))
    if keep and open_px is None:
        _skip(keep, "skipped", "시가를 확인하지 못해 발주하지 않았습니다 (현재가 조회 실패)", rec, "skipped")
        keep = []
    if gap_hit:
        # 갭 취소 대상 = 그리드 매수 + 소량 진입(boot, ADR-010). 레버리지 시장가·익절 매도는 대상 외
        grid = [r for r in keep if r.side == "buy" and (r.kind.startswith(GRID_KINDS_PREFIX) or r.kind == "boot")]
        _skip(grid, "skipped_gap", f"갭 취소 — 시가 {open_px:,}원 ≤ 기준 {int(float(gap_exact)):,}원, 그리드·초기 진입 매수 생략", rec, "skipped_gap")
        keep = [r for r in keep if r not in grid]
    # ④ 잔고 — 원장 대조·매도 한도
    deposit = 0
    if keep:
        try:
            bal = client.fetch_balance()
        except Exception as exc:  # noqa: BLE001
            _skip(keep, "skipped", f"잔고 조회 실패로 발주하지 않았습니다 — {humanize_kis_error(str(exc)[:120])}", rec, "skipped")
            keep = []
        else:
            deposit = int(bal.get("deposit") or 0)
            held = {h["code"]: int(h["qty"]) for h in bal.get("holdings", [])}
            ledger = _ledger_holdings(session, pf.id)
            diffs = [(c, ledger.get(c, 0), held.get(c, 0)) for c in (code_200, code_lev) if ledger.get(c, 0) != held.get(c, 0)]
            if diffs:
                detail = ", ".join(f"{c} 원장 {l:,}주 ≠ 계좌 {a:,}주" for c, l, a in diffs)
                _skip(keep, "skipped", f"사전 대조 불일치 — {detail}", rec, "skipped")
                keep = []
                pause_portfolio(pf, f"{who} 사전 대조 불일치 — {detail}. 체결 가져오기 또는 기록 수정으로 원장을 계좌에 맞춘 뒤 다시 켜세요", now)
            for r in [r for r in keep if r.side == "sell"]:
                if r.qty > held.get(r.code, 0):
                    r.message = f"잔고 부족 — 매도 {r.qty}주 > 보유 {held.get(r.code, 0)}주"
                    rec["skipped"] += 1
                    keep.remove(r)
    # ⑤ 상한 + 매수가능조회 → 발주. 매도 먼저(시장가 → 지정가, 현금 확보), 매수는 시장가(레버리지 진입) → 얕은 그리드 → 깊은 그리드.
    #    상한·주문가능 수량에 걸리면 정지가 아니라 **축소**(0 이면 생략) — 신규 진입이 며칠에 걸쳐 채워진다 (ADR-009 §2-8·9)
    equity = int((plan.get("account") or {}).get("equity") or 0)
    pct = float(allowed.get("daily_buy_cap_pct") or 0)
    cap = int(equity * pct / 100.0) if pct > 0 and equity > 0 else None
    streak, last_fail, running, clipped = state["fail_streak"], "", 0, 0
    for r in sorted(keep, key=lambda x: (0 if x.side == "sell" else 1, 0 if x.otype == "market" else 1, -int(x.price or 0))):
        plan_qty = int(r.qty)
        notes: list[str] = []
        if r.side == "buy":
            est_px = int(r.price) if r.price else _latest_close(session, r.code)
            if est_px <= 0:
                r.message = "매수 기준가를 알 수 없어 생략 (최근 종가 없음)"
                rec["skipped"] += 1
                continue
            if cap is not None:
                room = cap - running
                if room < r.qty * est_px:
                    q = max(0, room // est_px)
                    if q <= 0:
                        r.message = f"하루 매수 상한 — 이 줄까지 매수 {running + r.qty * est_px:,}원 > 상한 {cap:,}원(총자산 {equity:,}원의 {pct:g}%), 생략"
                        rec["skipped"] += 1
                        continue
                    r.qty = q
                    notes.append(f"하루 매수 상한({pct:g}%)으로 {plan_qty:,}→{q:,}주")
            try:
                pb = client.buyable(r.code, int(est_px))
                can = int(pb.get("cash_qty") or 0)
                if can < r.qty:
                    if can <= 0:
                        r.message = f"주문가능 수량 부족 — 가능 0주 (주문가능현금 {int(pb.get('cash') or 0):,}원), 생략"
                        rec["skipped"] += 1
                        continue
                    notes.append(f"주문가능 수량에 맞춰 {r.qty:,}→{can:,}주 (주문가능현금 {int(pb.get('cash') or 0):,}원)")
                    r.qty = can
            except Exception as exc:  # noqa: BLE001 — 조회 실패 → 예수금 누적 규칙으로 폴백
                logger.warning("auto-exec buyable failed pid=%s %s: %s — deposit fallback", pf.id, r.line_key, exc)
                room = deposit - running
                if room < r.qty * est_px:
                    q = max(0, room // est_px)
                    if q <= 0:
                        r.message = f"예수금 한도(폴백) — 이 줄까지 매수 {running + r.qty * est_px:,}원 > 예수금 {deposit:,}원, 생략"
                        rec["skipped"] += 1
                        continue
                    notes.append(f"예수금(폴백)에 맞춰 {r.qty:,}→{q:,}주")
                    r.qty = q
            running += r.qty * est_px
        if r.qty != plan_qty:
            clipped += 1
        try:
            res = client.place_order(r.code, r.side, int(r.qty), int(r.price) if r.price else None)
            r.order_no = res["order_no"] or None
            r.status = "submitted"
            r.message = (res["msg"] or "발주됨") + (" · " + " · ".join(notes) if notes else "")
            r.response = {**(r.response or {}), "order": res["raw"], "open": open_px, "notes": notes}
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
    return {"open": open_px, "gap_hit": gap_hit, "clipped": clipped, "streak": streak, "last_fail": last_fail}


# ── 장중 재시도 (2026-09-09 지시 "API 실패 시 취소 대신 재시도") ─────────────────────

def retryable_rows(session: Session, pf: TradePortfolio, plan_date: date, allowed: dict,
                   statuses: tuple[str, ...] = RETRYABLE, only_lines: set[str] | None = None) -> list[BrokerOrder]:
    """재시도·재등록 대상 — 이 실행일 줄(line_key)별 **최신** 무인 행이 `statuses` 이고 그 방향이 지금 켜져 있는 줄.

    기본(RETRYABLE) = 실패(failed)·생략(skipped). 갭 취소 생략(skipped_gap)은 그날의 전략 판정이라 늘 제외하고,
    꺼진 방향도 제외한다(켜면 대상이 된다 — '꺼짐 — 수동 처리' 행 포함). 줄별 재등록은 statuses 에 cancelled 를 더해
    `only_lines` 로 그 줄만 고른다 (2026-09-10 지시 "취소 후 다시 입력").
    """
    rows = session.scalars(select(BrokerOrder).where(BrokerOrder.portfolio_id == pf.id, BrokerOrder.plan_date == plan_date,
                                                     BrokerOrder.mode == "auto").order_by(BrokerOrder.id)).all()
    latest: dict[str, BrokerOrder] = {}
    for r in rows:
        latest[r.line_key] = r
    return [r for k, r in latest.items() if r.status in statuses and allowed.get(r.side, False)
            and (only_lines is None or k in only_lines)]


def _acquire_retry_lock(pf_id: int) -> bool:
    """재시도 중복 클릭 방지 — Redis SET NX(60초). Redis 가 없으면 True."""
    try:
        import redis as sync_redis

        from app.config import get_settings

        r = sync_redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=1)
        return bool(r.set(f"autoexec:retry:{pf_id}", "1", nx=True, ex=RETRY_LOCK_TTL))
    except Exception:  # noqa: BLE001
        return True


def _default_plan_view(session: Session, pf: TradePortfolio, now: datetime) -> dict:
    """재시도용 주문표 — 동결된 스냅샷을 그대로(force_freeze 없음). 갭 기준·총자산(상한 환산)만 쓴다."""
    from app.signals import _portfolio_orders

    return _portfolio_orders(session, pf.id, pf.user_id, force_freeze=False, now=now)


def retry_auto_exec(session: Session, pf: TradePortfolio, now: datetime | None = None, client_factory=None,
                    sleep_fn=_time.sleep, plan_fn=None, statuses: tuple[str, ...] = RETRYABLE,
                    only_lines: set[str] | None = None, what: str = "재시도") -> dict:
    """오늘 09:01 결과가 실패·생략인 줄만 같은 절차로 다시 발주한다 (통제 10). 이전 행은 기록으로 남고 줄마다 새 행이 생긴다.

    막는 조건(409): 국내 아님 · 계좌 없음 · 플래그 모두 꺼짐 · 정지 · 오늘 무인 취소 · 오늘 실행 기록 없음 · 장중(09:00~15:20) 밖 ·
    대상 줄 없음 · 주문표 실행일 ≠ 오늘 · 이미 재시도 진행 중. 반환: last_run.retry 에 기록되는 요약 + items(새 행).
    """
    from app.activity import log_event

    now = now or datetime.now(KST)
    today = now.date()
    if pf.market != "KR":
        raise HTTPException(status_code=409, detail="국내 포트만 무인 재시도가 가능합니다")
    cred = session.get(BrokerCredential, pf.broker_credential_id) if pf.broker_credential_id else None
    if cred is None:
        raise HTTPException(status_code=409, detail="연결된 증권사 계좌가 없습니다")
    allowed = account_auto_exec(cred)
    if not (allowed["buy"] or allowed["sell"]):
        raise HTTPException(status_code=409, detail="무인 매수·매도가 모두 꺼져 있습니다 — 설정 › 무인 실행에서 켠 뒤 재시도하세요")
    state = pf_auto_state(pf)
    if state["paused"]:
        raise HTTPException(status_code=409, detail=f"정지 상태 — 먼저 '다시 켜기'를 누르세요 ({state['paused_reason'] or ''})")
    if (state.get("skip") or {}).get("date") == today.isoformat():
        raise HTTPException(status_code=409, detail="오늘은 무인 취소(수동) 상태입니다")
    last = state.get("last_run") or {}
    if last.get("date") != today.isoformat():
        raise HTTPException(status_code=409, detail=f"오늘 09:01 실행 기록이 없습니다 — {what}할 결과가 없습니다")
    if not (RETRY_WINDOW[0] <= now.time() <= RETRY_WINDOW[1]):
        raise HTTPException(status_code=409, detail=f"{what}는 장중(09:00~15:20)에만 가능합니다")
    rows = retryable_rows(session, pf, today, allowed, statuses=statuses, only_lines=only_lines)
    if not rows:
        raise HTTPException(status_code=409, detail=f"{what}할 줄이 없습니다 — 대상 상태가 아니거나 그 방향이 꺼져 있습니다")
    plan = (plan_fn or _default_plan_view)(session, pf, now)
    if str(plan.get("exec_day")) != today.isoformat():
        raise HTTPException(status_code=409, detail=f"주문표 실행일 {plan.get('exec_day')} 이 오늘과 다릅니다")
    if not _acquire_retry_lock(pf.id):
        raise HTTPException(status_code=409, detail=f"{what}가 이미 진행 중입니다 — 잠시 뒤 새로고침하세요")
    code_200, code_lev = _resolve_codes(session, pf)
    rec: dict = {"portfolio_id": pf.id, "name": pf.name, "exec_day": today.isoformat(), "submitted": 0, "skipped_gap": 0,
                 "skipped": 0, "failed": 0, "note": "retry"}
    new: list[BrokerOrder] = []
    for old in rows:
        plan_qty = int((old.response or {}).get("plan_qty") or old.qty)   # 축소 전 계획 수량으로 되돌려 다시 판정
        r = BrokerOrder(portfolio_id=pf.id, broker_credential_id=cred.id, plan_date=today, line_key=old.line_key, code=old.code,
                        instrument=old.instrument, kind=old.kind, side=old.side, otype=old.otype, qty=plan_qty, price=old.price,
                        mode="auto", status="skipped",
                        response={"plan_qty": plan_qty, "retry_of": old.id, "retry_at": now.isoformat(timespec="seconds")})
        session.add(r)
        new.append(r)
    session.flush()
    set_running(True)
    try:
        client = (client_factory or _client)(cred)
        res = _place_lines(session, pf, client, new, plan, allowed, state, rec, now, sleep_fn, code_200, code_lev, who=f"재시도 {now:%H:%M}")
    finally:
        set_running(False)
    retry = {"at": now.isoformat(timespec="seconds"), "n": len(new), "open": res["open"], "gap_hit": res["gap_hit"], "clipped": res["clipped"],
             **{k: rec[k] for k in ("submitted", "skipped_gap", "skipped", "failed")}}
    _set_pf_auto_state(pf, last_run={**last, "retry": retry}, fail_streak=res["streak"])
    if res["streak"] >= FAIL_STREAK_PAUSE:
        pause_portfolio(pf, f"재시도 발주 연속 실패 {res['streak']}회 — 마지막 오류: {res['last_fail']}", now)
    parts = [f"발주 {rec['submitted']}건"]
    for k, ko in (("skipped_gap", "갭 취소 생략"), ("skipped", "생략"), ("failed", "실패")):
        if rec[k]:
            parts.append(f"{ko} {rec[k]}건")
    if res["clipped"]:
        parts.append(f"축소 {res['clipped']}건")
    log_event(session, pf.user_id, "autoexec.retry", f"무인 {what} {now:%H:%M} — 대상 {len(new)}줄: " + " · ".join(parts),
              level="error" if rec["failed"] else ("warn" if rec["skipped"] or rec["skipped_gap"] else "info"),
              portfolio_id=pf.id, data={k: v for k, v in rec.items() if k != "name"} | {"retry": retry}, at=now)
    return {**retry, "items": [_order_out(r) for r in new]}


@router.post("/portfolio/{pid}/orders/{oid}/reorder")
def reorder_auto_line(pid: int, oid: int, user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    """줄별 재등록 (2026-09-10 지시 "취소 후 거래 가능한 금액은 재등록 버튼으로 다시 입력") — 취소·실패·생략된 그 줄 하나만
    09:01 과 같은 절차(시가·갭 → 잔고 대조 → 상한·매수가능 → 발주)로 다시 낸다. 수량은 취소로 풀린 현금까지 반영해
    매수가능조회로 다시 계산되므로 처음보다 줄거나 늘 수 있다. 갭 취소 생략(skipped_gap)·이미 발주·체결된 줄은 대상 외."""
    pf = _owned(session, pid, user_id)
    row = session.get(BrokerOrder, oid)
    if row is None or row.portfolio_id != pid:
        raise HTTPException(status_code=404, detail="order not found")
    if (getattr(row, "mode", "") or "") != "auto":
        raise HTTPException(status_code=409, detail="무인 실행이 낸 줄만 재등록할 수 있습니다")
    if row.status not in REORDERABLE:
        from app.broker import STATUS_KO

        raise HTTPException(status_code=409, detail=f"재등록할 수 없는 상태입니다 ({STATUS_KO.get(row.status, row.status)})")
    now = datetime.now(KST)
    res = retry_auto_exec(session, pf, now=now, statuses=REORDERABLE, only_lines={row.line_key}, what="재등록")
    session.commit()
    return {**auto_exec_view(session, pf, now.date(), now), "retry": res}


@router.post("/portfolio/{pid}/auto-exec/retry")
def retry_portfolio_auto_exec(pid: int, user_id: int = Depends(current_user_id),
                              session: Session = Depends(get_session)) -> dict:
    """장중 재시도 — '무인' 열 헤더의 `재시도`. 실패·생략 줄만 09:01 과 같은 절차로 다시 발주하고 결과를 돌려준다."""
    pf = _owned(session, pid, user_id)
    now = datetime.now(KST)
    res = retry_auto_exec(session, pf, now=now)
    session.commit()
    return {**auto_exec_view(session, pf, now.date(), now), "retry": res}


# ── 장 마감 후 상태 확정 (run_post_close_sync 에서 호출) ─────────────────────────────

def sync_auto_orders(session: Session, cred: BrokerCredential, rows: list[BrokerOrder], today: date,
                     now: datetime | None = None, client=None) -> int:
    """발주된(submitted/partial) 무인 주문의 체결 상태를 당일 체결조회로 확정한다. 반환: 바뀐 건수."""
    active = [r for r in rows if r.mode == "auto" and r.status in LIVE_AUTO]
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
