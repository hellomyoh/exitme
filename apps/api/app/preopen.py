"""장 시작 전 예상 시가 갭 취소 (2026-09-06 지시) — 08:57 에 예상체결가로 갭을 미리 판정해 **접수된 그리드 매수 지정가를 취소**한다.

배경: 예약주문(전날 접수)은 09:00 동시호가에 들어가 갭이 나면 시가에 그대로 체결된다(ADR-008 배경). 시가는 09:00 에 확정되지만
08:30~09:00 동시호가 동안 KIS 가 **예상체결가**(호가/예상체결 TR FHKST01010200 의 antc_cnpr)를 준다. 이 값이 갭 취소 기준
(전일 종가 − 1.5×ATR, 계획 스냅샷의 gap_cancel_exact) 이하면 그리드 매수를 무인으로 취소한다.

사용자 지시(2026-09-06): "무인 매수/매도는 수동으로 직접 지정한다(초기 기획안대로). 장 시작 전 예상 시가를 계산해서 특정 가격 이하시
그리드 매수를 전량 취소한다(취소는 무인으로)." — 무인 발주(09:01 실행, ADR-008)는 그대로 두고 이 기능은 **취소만** 무인으로 한다.

대상: 오늘 계획이 있는 국내 포트(실전 계좌 연결). KIS 정정취소가능(미체결) 주문 중 200 ETF **매수**이고 가격이 오늘 계획의 그리드
지정가와 **정확히 같은** 것 — 앱에서 접수한 예약주문(BrokerOrder reserved)이든 사용자가 HTS 에서 직접 넣은 것이든 같은 기준으로
취소한다(그 외 주문은 건드리지 않음). 무인 승인 줄(mode=auto)은 09:01 에 실제 시가로 판정하므로 여기서 건드리지 않는다.
설정: `user_settings.auto_exec.preopen_cancel` — 기본 **켜짐**(취소만 하는 보호 동작·사용자 지시). 설정 › 무인 실행에서 끌 수 있다.
기록: `params.preopen_cancel.last_run`(주문표 화면 한 줄) + 활동 로그(로그 페이지) + BrokerOrder 상태 `gap_cancelled`.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import log_event
from app.autoexec import account_auto_exec
from app.broker import _client, _resolve_codes, humanize_kis_error
from app.models import BrokerCredential, BrokerOrder, PortfolioPlan, TradePortfolio

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
PREOPEN_TIME = time(8, 57)   # 워커 실행 시각 — 동시호가 막판(예상체결가가 안정된 뒤), 09:00 전에 취소가 끝나도록
GRID_PREFIX = "grid"


def pf_preopen_state(pf: TradePortfolio) -> dict:
    return dict((pf.params or {}).get("preopen_cancel") or {})


def _store(pf: TradePortfolio, **kw) -> None:
    params = dict(pf.params or {})
    st = dict(params.get("preopen_cancel") or {})
    st.update(kw)
    params["preopen_cancel"] = st
    pf.params = params  # JSONB 변경 감지 — 재할당 필수


def _read_expected(client, code: str, sleep_fn=_time.sleep, tries: int = 3, wait: float = 5.0) -> int | None:
    """예상체결가(antc_cnpr). 0 이면 잠시 기다려 다시 읽는다(동시호가 초반·조회 지연)."""
    for i in range(tries):
        try:
            v = int(client.fetch_expected(code).get("expected") or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("preopen expected price failed code=%s: %s", code, exc)
            v = 0
        if v > 0:
            return v
        if i < tries - 1:
            sleep_fn(wait)
    return None


def run_preopen_cancel(session: Session, now: datetime | None = None, client_factory=None,
                       sleep_fn=_time.sleep) -> dict:
    """오늘 계획이 있는 국내 포트마다 예상체결가를 읽어 갭이면 그리드 매수 미체결을 취소한다. 포트별 실패는 기록하고 계속."""
    now = now or datetime.now(KST)
    today = now.date()
    client_factory = client_factory or _client
    out: dict = {"date": today.isoformat(), "portfolios": []}
    for plan in session.scalars(select(PortfolioPlan).where(PortfolioPlan.trade_date == today)
                                .order_by(PortfolioPlan.portfolio_id)).all():
        pf = session.get(TradePortfolio, plan.portfolio_id)
        if pf is None or pf.market != "KR" or not pf.broker_credential_id:
            continue
        cred = session.get(BrokerCredential, pf.broker_credential_id)
        if cred is None or cred.env != "prod":
            continue
        rec: dict = {"portfolio_id": pf.id, "name": pf.name, "expected": None, "gap_exact": None, "gap_hit": False,
                     "cancelled": 0, "failed": 0, "untracked": 0, "unmatched": 0, "note": None}
        if not account_auto_exec(cred).get("preopen_cancel", True):   # 계좌별 스위치 (2026-09-07)
            rec["note"] = "설정 꺼짐"
            out["portfolios"].append(rec)
            continue
        try:
            _preopen_portfolio(session, pf, cred, plan, today, now, client_factory, sleep_fn, rec)
            session.commit()
        except Exception as exc:  # noqa: BLE001 — 포트 단위 실패는 기록하고 다음 포트
            session.rollback()
            rec["error"] = str(exc)[:200]
            logger.exception("preopen cancel failed pid=%s", pf.id)
            try:
                log_event(session, pf.user_id, "preopen.error", f"사전 갭 확인 실패 — {humanize_kis_error(str(exc)[:200])}",
                          level="error", portfolio_id=pf.id, at=now)
                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
        out["portfolios"].append(rec)
    return out


def _preopen_portfolio(session: Session, pf: TradePortfolio, cred: BrokerCredential, plan: PortfolioPlan,
                       today: date, now: datetime, client_factory, sleep_fn, rec: dict) -> None:
    if (pf_preopen_state(pf).get("last_run") or {}).get("date") == today.isoformat():
        rec["note"] = "already-ran"
        return
    payload = plan.payload or {}
    gap_exact = payload.get("gap_cancel_exact") or payload.get("gap_cancel_below")
    grid_prices = {int(o["price"]) for o in payload.get("orders", [])
                   if o.get("side") == "buy" and str(o.get("kind") or "").startswith(GRID_PREFIX) and o.get("price")}
    base = {"date": today.isoformat(), "at": now.isoformat(timespec="minutes"), "gap_exact": float(gap_exact) if gap_exact else None}
    if not gap_exact or not grid_prices:
        rec["note"] = "그리드 매수 없음"
        _store(pf, last_run={**base, "expected": None, "gap_hit": False, "cancelled": 0, "failed": 0, "untracked": 0, "unmatched": 0, "note": rec["note"]})
        return
    code_200, _code_lev = _resolve_codes(session, pf)
    client = client_factory(cred)
    expected = _read_expected(client, code_200, sleep_fn=sleep_fn)
    rec["expected"], rec["gap_exact"] = expected, float(gap_exact)
    gap_int = int(float(gap_exact))
    if expected is None:
        rec["note"] = "예상체결가 없음"
        _store(pf, last_run={**base, "expected": None, "gap_hit": False, "cancelled": 0, "failed": 0, "untracked": 0, "unmatched": 0, "note": rec["note"]})
        log_event(session, pf.user_id, "preopen.run", f"사전 갭 확인 {now:%H:%M} — {code_200} 예상체결가를 읽지 못해 판정하지 않음 (기준 {gap_int:,}원)",
                  level="warn", portfolio_id=pf.id, data={"code": code_200, "gap_exact": gap_exact}, at=now)
        return
    gap_hit = expected <= float(gap_exact)
    rec["gap_hit"] = gap_hit
    if not gap_hit:
        _store(pf, last_run={**base, "expected": expected, "gap_hit": False, "cancelled": 0, "failed": 0, "untracked": 0, "unmatched": 0, "note": None})
        log_event(session, pf.user_id, "preopen.run", f"사전 갭 확인 {now:%H:%M} — {code_200} 예상체결가 {expected:,}원 > 기준 {gap_int:,}원, 그리드 매수 유지",
                  portfolio_id=pf.id, data={"code": code_200, "expected": expected, "gap_exact": gap_exact}, at=now)
        return
    # 갭 → 미체결(정정취소가능) 주문 중 200 ETF 매수·그리드 지정가와 같은 것만 취소
    tracked = [r for r in session.scalars(select(BrokerOrder).where(
        BrokerOrder.portfolio_id == pf.id, BrokerOrder.plan_date == today, BrokerOrder.mode == "reserve",
        BrokerOrder.status == "reserved", BrokerOrder.side == "buy")).all() if str(r.kind or "").startswith(GRID_PREFIX)]
    by_price: dict[int, list[BrokerOrder]] = {}
    for r in tracked:
        by_price.setdefault(int(r.price or 0), []).append(r)
    try:
        open_orders = client.list_open_orders()
    except Exception as exc:  # noqa: BLE001
        rec["note"] = f"미체결 조회 실패 — {humanize_kis_error(str(exc)[:160])}"
        _store(pf, last_run={**base, "expected": expected, "gap_hit": True, "cancelled": 0, "failed": 0, "untracked": 0, "unmatched": len(tracked), "note": rec["note"]})
        log_event(session, pf.user_id, "preopen.error", f"사전 갭 취소 실패 {now:%H:%M} — 예상체결가 {expected:,}원 ≤ 기준 {gap_int:,}원인데 미체결 조회에 실패해 취소하지 못함: {humanize_kis_error(str(exc)[:160])}",
                  level="error", portfolio_id=pf.id, data={"code": code_200, "expected": expected, "gap_exact": gap_exact}, at=now)
        return
    targets = [o for o in open_orders
               if o.get("code") == code_200 and o.get("side") == "buy" and int(o.get("price") or 0) in grid_prices
               and int(o.get("psbl_qty") or o.get("qty") or 0) > 0]
    for o in sorted(targets, key=lambda x: -int(x.get("price") or 0)):
        rows = by_price.get(int(o["price"]), [])
        row = rows.pop(0) if rows else None
        try:
            res = client.cancel_order(o.get("order_no") or "", o.get("orgno") or "")
            rec["cancelled"] += 1
            msg = f"예상 시가 갭 취소 {now:%H:%M} — 예상체결가 {expected:,}원 ≤ 기준 {gap_int:,}원 (주문 {o.get('order_no')})"
            if row is not None:
                row.status, row.message, row.order_no = "gap_cancelled", msg, o.get("order_no") or row.order_no
                row.response = {**(row.response or {}), "preopen": {"expected": expected, "gap_exact": gap_exact, "cancel": res.get("raw") or {}}}
            else:
                rec["untracked"] += 1
            log_event(session, pf.user_id, "preopen.cancel",
                      f"그리드 매수 취소 — {code_200} {int(o.get('qty') or 0):,}주 @{int(o['price']):,}원 (주문 {o.get('order_no')}){'' if row is not None else ' · 앱 밖에서 낸 주문'} · 예상체결가 {expected:,}원 ≤ 기준 {gap_int:,}원",
                      level="warn", portfolio_id=pf.id, data={"order_no": o.get("order_no"), "price": o["price"], "qty": o.get("qty"), "tracked": row is not None}, at=now)
            logger.info("preopen cancelled pid=%s %s x%s @%s no=%s", pf.id, code_200, o.get("qty"), o["price"], o.get("order_no"))
        except Exception as exc:  # noqa: BLE001
            rec["failed"] += 1
            err = humanize_kis_error(str(exc)[:160])
            if row is not None:
                row.message = f"예상 시가 갭 — 취소 실패: {err}"
            log_event(session, pf.user_id, "preopen.cancel_failed",
                      f"그리드 매수 취소 실패 — {code_200} @{int(o['price']):,}원 (주문 {o.get('order_no')}): {err}",
                      level="error", portfolio_id=pf.id, data={"order_no": o.get("order_no"), "price": o["price"]}, at=now)
            logger.warning("preopen cancel failed pid=%s no=%s: %s", pf.id, o.get("order_no"), exc)
    # 앱이 접수한 예약주문인데 미체결 목록에 없는 줄 — 예약주문이 아직 정규 주문으로 전송되지 않았거나 이미 처리됨
    for rows in by_price.values():
        for r in rows:
            rec["unmatched"] += 1
            r.message = f"예상 시가 갭 {now:%H:%M} — 미체결 목록에서 찾지 못해 취소하지 못함(예약주문 미전송 또는 이미 처리). 09:00 체결 여부를 확인하세요"
            log_event(session, pf.user_id, "preopen.unmatched",
                      f"그리드 매수 취소 불가 — {code_200} {int(r.qty):,}주 @{int(r.price or 0):,}원: 미체결 목록에 없음(예약주문 미전송 또는 이미 처리)",
                      level="warn", portfolio_id=pf.id, data={"line_key": r.line_key, "rsvn_ord_seq": r.rsvn_ord_seq}, at=now)
    summary = {**base, "expected": expected, "gap_hit": True, "cancelled": rec["cancelled"], "failed": rec["failed"],
               "untracked": rec["untracked"], "unmatched": rec["unmatched"], "note": None}
    _store(pf, last_run=summary)
    log_event(session, pf.user_id, "preopen.run",
              f"사전 갭 취소 {now:%H:%M} — {code_200} 예상체결가 {expected:,}원 ≤ 기준 {gap_int:,}원 → 그리드 매수 {rec['cancelled']}건 취소"
              + (f" · 실패 {rec['failed']}건" if rec["failed"] else "") + (f" · 앱 밖 주문 {rec['untracked']}건 포함" if rec["untracked"] else "")
              + (f" · 취소 불가 {rec['unmatched']}건" if rec["unmatched"] else ""),
              level="error" if rec["failed"] else "warn", portfolio_id=pf.id, data=summary, at=now)
