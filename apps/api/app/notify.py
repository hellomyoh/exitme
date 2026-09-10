"""외부 메신저 알림 — 텔레그램 (2026-09-07 지시).

지시: "외부 메신저로 매매결과, 현황 등 메시지를 전송 — 텔레그램 연동, 봇 토큰은 설정에서 입력, 설정에 전송할 메시지 항목을
나열하고 사용자가 체크하면 그 메시지는 발송."

설계
- 설정(사용자 단위, `user_settings`): `telegram_bot_token`(🔒 암호화), `telegram_chat_id`, `notify = {"enabled": bool, "events": {카테고리: bool}}`.
  chat_id 는 사용자가 봇에 아무 메시지를 보낸 뒤 "연결 확인"(getUpdates)으로 자동 확인한다.
- 발송 지점은 세 곳뿐이다:
  1) **활동 로그 저장 직후**(`activity.log_event` → `notify_event`): 이벤트 종류 → 카테고리 매핑표(KIND_TO_CATEGORY)로 걸러 발송.
     무인 실행·자동 승인·사전 갭 취소·정지·동기화·예수금 대조·주문 접수/취소가 모두 여기로 흐른다 — 기록과 알림이 같은 원천.
  2) **거래 등록**(`portfolios.register_transaction` → `notify_trade`): 수동 등록 매수·매도·입출금. 증권사 가져오기는 동기화 요약에 포함.
  3) **일일 현황**(`worker.daily_asset_snapshot` 16:40 → `send_daily_status`): 총자산·전일 대비·구성·포트별 평가액.
- 발송은 동기 HTTP(8초 제한), 실패는 절대 예외로 올리지 않고 로그(`notify.failed`)만 남긴다 — 알림 장애가 발주·동기화를 막지 않게.
- 텔레그램 Bot API: sendMessage(text, 4096자 제한 → 3900자에서 자름), getUpdates(chat_id 확인). 토큰은 브라우저로 마스킹해서만 나간다.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import TradePortfolio, UserSettings

logger = logging.getLogger(__name__)
router = APIRouter()
KST = timezone(timedelta(hours=9))
TG_API = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,60}$")   # BotFather 토큰 형식 "123456789:AA..."
MAX_LEN = 3900

# 설정 화면의 체크 항목 — (키, 표시명, 설명, 기본값). 기본은 '사람이 봐야 하는 결과·경고' 만 켬
CATEGORIES: list[tuple[str, str, str, bool]] = [
    ("autoexec", "무인 실행 결과", "09:01 발주·갭 취소 생략·생략·축소·실패 요약, 09:15 감시 지연 실행, 무인 실행 오류", True),
    ("paused", "정지 · 긴급 정지", "무인 실행 자동 정지(연속 실패·대조 불일치·상한 초과), 전량 취소", True),
    ("post_close", "장 마감 동기화", "15:45 체결 가져오기·주문 상태 확정 결과, 동기화·조회 오류", True),
    ("cash_check", "예수금 대조", "원장 현금 vs 계좌 D+2 예수금 경고, 차액 보정 등록", True),
    ("orders", "주문 접수 · 취소 · 설정", "예약주문 접수, 무인 승인, 개별 취소, 완전 무인 설정 변경", False),
    ("trades", "체결 등록", "실전매매에 수동 등록한 매수·매도·입출금, 거래 삭제", False),
    ("daily_status", "일일 현황", "16:40 총자산·전일 대비·주식/현금 구성·포트별 평가액", True),
]
DEFAULT_EVENTS = {k: d for k, _l, _d, d in CATEGORIES}
# 활동 로그 이벤트 종류 → 카테고리. 없는 종류(줄 단위 사전 갭 취소 등)는 보내지 않는다 — 요약 한 건이 대신한다
KIND_TO_CATEGORY = {
    "autoexec.run": "autoexec", "autoexec.error": "autoexec", "autoexec.retry": "autoexec",   # 장중 재시도 (2026-09-09)
    "autoexec.paused": "paused",
    "autoexec.skip": "orders", "autoexec.cancel": "orders",   # 사용자 취소(수동 전환)·설정 해제로 취소 (ADR-009)
    "sync.post_close": "post_close", "sync.error": "post_close", "sync.reserved_failed": "post_close",
    "cash_check.warn": "cash_check", "cash_check.align": "cash_check",
    "order.reserve": "orders", "order.cancel": "orders", "order.manual": "orders", "autoexec.account_setting": "orders",
    "tx.delete": "trades",
}
LEVEL_EMOJI = {"info": "ℹ️", "warn": "⚠️", "error": "🛑"}


# ── 설정 ─────────────────────────────────────────────────────────────────────────

def _row(session: Session, user_id: int) -> UserSettings | None:
    return session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))


def user_notify(session: Session, user_id: int) -> dict:
    """알림 설정 요약 — 토큰은 있는지 여부만. events 는 기본값에 저장값을 덮는다."""
    row = _row(session, user_id)
    cfg = dict((row.notify if row else None) or {})
    events = {**DEFAULT_EVENTS, **{k: bool(v) for k, v in (cfg.get("events") or {}).items() if k in DEFAULT_EVENTS}}
    token = (row.telegram_bot_token if row else None) or ""
    return {"enabled": bool(cfg.get("enabled", False)), "has_token": bool(token),
            "token_masked": _mask(token) if token else "", "chat_id": (row.telegram_chat_id if row else None) or "",
            "events": events, "ready": bool(cfg.get("enabled", False) and token and (row.telegram_chat_id if row else "")),
            # 마지막 전송 성공/실패 — "안 오는데 왜?" 를 설정 화면에서 바로 보게 (2026-09-09)
            "last": dict(cfg.get("last") or {})}


def _record_last(row: UserSettings, ok: bool, err: str | None = None) -> None:
    """row.notify.last 에 마지막 전송 결과 기록 (commit 은 호출자). JSONB 변경 감지 — 재할당."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    cfg = dict(row.notify or {})
    last = dict(cfg.get("last") or {})
    if ok:
        last.update({"sent_at": now, "error": None, "error_at": None})
    else:
        last.update({"error": (err or "")[:200], "error_at": now})
    cfg["last"] = last
    row.notify = cfg


def _mask(s: str) -> str:
    if len(s) <= 10:
        return "*" * len(s)
    return s[:6] + "*" * 8 + s[-4:]


class NotifyIn(BaseModel):
    enabled: bool | None = None
    bot_token: str | None = Field(default=None, max_length=120)   # 빈 문자열/None = 유지
    clear_token: bool = False
    chat_id: str | None = Field(default=None, max_length=40)
    events: dict[str, bool] | None = None


@router.get("/settings/notify")
def get_notify(user_id: int = Depends(current_user_id), session: Session = Depends(get_session)) -> dict:
    return {**user_notify(session, user_id),
            "categories": [{"key": k, "label": l, "desc": d, "default": df} for k, l, d, df in CATEGORIES]}


@router.put("/settings/notify")
def put_notify(body: NotifyIn, user_id: int = Depends(current_user_id), session: Session = Depends(get_session)) -> dict:
    """봇 토큰(암호화 저장)·chat_id·켬/끔·보낼 항목. 토큰은 빈 값이면 유지, clear_token 이면 삭제."""
    from app.settings import _row as _get_or_create

    row = _get_or_create(session, user_id)
    if body.clear_token:
        row.telegram_bot_token = None
    elif body.bot_token is not None and body.bot_token.strip():
        tok = body.bot_token.strip()
        if not TOKEN_RE.match(tok):
            raise HTTPException(status_code=422, detail="봇 토큰 형식이 아닙니다 — @BotFather 가 준 '숫자:영문숫자' 형태(예: 123456789:AAH…)를 그대로 붙여넣으세요")
        row.telegram_bot_token = tok
    if body.chat_id is not None:
        cid = body.chat_id.strip()
        if cid and not re.fullmatch(r"-?\d{1,20}", cid):
            raise HTTPException(status_code=422, detail="채팅 ID 는 숫자입니다 — 비워 두고 '연결 확인'을 누르면 자동으로 채워집니다")
        row.telegram_chat_id = cid or None
    cfg = dict(row.notify or {})
    if body.enabled is not None:
        cfg["enabled"] = bool(body.enabled)
    if body.events is not None:
        cur = {**DEFAULT_EVENTS, **(cfg.get("events") or {})}
        cur.update({k: bool(v) for k, v in body.events.items() if k in DEFAULT_EVENTS})
        cfg["events"] = cur
    row.notify = cfg
    session.commit()
    logger.info("notify settings user=%s enabled=%s", user_id, cfg.get("enabled"))
    return user_notify(session, user_id)


@router.post("/settings/notify/test")
def test_notify(user_id: int = Depends(current_user_id), session: Session = Depends(get_session)) -> dict:
    """연결 확인 — chat_id 가 비어 있으면 getUpdates 로 봇에게 말을 건 채팅을 찾아 저장하고, 테스트 메시지를 보낸다."""
    row = _row(session, user_id)
    token = (row.telegram_bot_token if row else None) or ""
    if not token:
        raise HTTPException(status_code=409, detail="봇 토큰을 먼저 저장하세요")
    chat_id = (row.telegram_chat_id or "") if row else ""
    title = None
    if not chat_id:
        try:
            updates = _http_get_updates(token)
        except Exception as exc:  # noqa: BLE001
            _record_last(row, False, _humanize(exc))
            session.commit()
            raise HTTPException(status_code=502, detail=f"텔레그램 조회 실패 — {_humanize(exc)}")
        chat = _pick_chat(updates)
        if chat is None:
            raise HTTPException(status_code=409, detail="봇과 대화한 기록이 없습니다 — 텔레그램에서 봇을 찾아 아무 메시지나 보낸 뒤 다시 누르세요 (그룹이면 봇을 초대하고 메시지)")
        chat_id, title = str(chat.get("id")), chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name"), chat.get("username")) if x)
        row.telegram_chat_id = chat_id
        session.commit()
    try:
        _http_send(token, chat_id, "✅ ExitMe 텔레그램 연결 확인 — 설정에서 체크한 항목의 알림이 이 채팅으로 옵니다.")
    except Exception as exc:  # noqa: BLE001
        _record_last(row, False, _humanize(exc))
        session.commit()
        raise HTTPException(status_code=502, detail=f"테스트 메시지 실패 — {_humanize(exc)}")
    _record_last(row, True)
    # 연결 확인까지 했는데 '알림 보내기'가 꺼져 있어 한 통도 안 가던 사례(2026-09-09 운영: enabled 키 자체가 없었음) — 성공 시 켠다
    cfg = dict(row.notify or {})
    enabled_now = not cfg.get("enabled")
    if enabled_now:
        cfg["enabled"] = True
        row.notify = cfg
    session.commit()
    return {"ok": True, "chat_id": chat_id, "chat_title": title, "enabled_now": enabled_now}


def _pick_chat(updates: list[dict]) -> dict | None:
    for u in reversed(updates or []):
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            m = u.get(key) or {}
            chat = m.get("chat")
            if chat and chat.get("id") is not None:
                return chat
    return None


def _humanize(exc: Exception) -> str:
    s = str(exc)
    if "401" in s or "Unauthorized" in s:
        return "봇 토큰이 올바르지 않습니다 (401)"
    if "400" in s and "chat not found" in s.lower():
        return "채팅을 찾을 수 없습니다 — 채팅 ID 를 비우고 봇에게 메시지를 보낸 뒤 다시 연결 확인"
    if "403" in s:
        return "봇이 차단되었거나 채팅에 없습니다 (403)"
    if any(k in s for k in ("SSL", "EOF", "Connection reset", "ConnectionError", "Max retries", "NameResolution", "NewConnectionError")):
        return "텔레그램 서버(api.telegram.org)에 연결할 수 없습니다 — 서버 네트워크에서 차단(방화벽·사내망)된 경우가 대부분입니다. 서버에서 접속 확인 필요"
    if "timed out" in s.lower() or "Timeout" in s:
        return "텔레그램 응답 시간 초과(8초) — 서버 네트워크 확인"
    return s[:160]


# ── 전송 (테스트에서 _http_send / _http_get_updates 를 바꿔 끼운다) ─────────────────

def _http_send(token: str, chat_id: str, text: str) -> dict:
    resp = requests.post(f"{TG_API}/bot{token}/sendMessage",
                         json={"chat_id": chat_id, "text": text[:MAX_LEN], "disable_web_page_preview": True}, timeout=8)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or not data.get("ok", False):
        raise RuntimeError(f"telegram {resp.status_code}: {str(data.get('description') or resp.text)[:160]}")
    return data


def _http_get_updates(token: str) -> list[dict]:
    resp = requests.get(f"{TG_API}/bot{token}/getUpdates", params={"limit": 20, "timeout": 0}, timeout=8)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or not data.get("ok", False):
        raise RuntimeError(f"telegram {resp.status_code}: {str(data.get('description') or resp.text)[:160]}")
    return list(data.get("result") or [])


def maybe_notify(session: Session, user_id: int, category: str | None, text: str, *, level: str = "info",
                 portfolio_name: str | None = None) -> bool:
    """설정이 켜져 있고 카테고리가 체크돼 있으면 보낸다. 실패는 로그(notify.failed)만 — 예외를 올리지 않는다."""
    if not category:
        return False
    row = _row(session, user_id)
    if row is None:
        return False
    cfg = dict(row.notify or {})
    if not cfg.get("enabled") or not row.telegram_bot_token or not row.telegram_chat_id:
        return False
    events = {**DEFAULT_EVENTS, **(cfg.get("events") or {})}
    if not events.get(category, False):
        return False
    head = f"{LEVEL_EMOJI.get(level, 'ℹ️')} [ExitMe{' · ' + portfolio_name if portfolio_name else ''}] "
    try:
        _http_send(row.telegram_bot_token, row.telegram_chat_id, head + text)
        _record_last(row, True)
        return True
    except Exception as exc:  # noqa: BLE001 — 알림 장애가 본 작업을 막지 않게
        logger.warning("telegram notify failed user=%s cat=%s: %s", user_id, category, exc)
        _record_last(row, False, _humanize(exc))
        try:
            from app.activity import log_event

            log_event(session, user_id, "notify.failed", f"텔레그램 전송 실패 ({category}) — {_humanize(exc)}", level="warn")
        except Exception:  # noqa: BLE001
            pass
        return False


def notify_event(session: Session, user_id: int, kind: str, text: str, level: str, portfolio_id: int | None) -> bool:
    """활동 로그 한 줄 → 카테고리 매핑 → 발송 (activity.log_event 가 호출)."""
    cat = KIND_TO_CATEGORY.get(kind)
    if not cat:
        return False
    pf = session.get(TradePortfolio, portfolio_id) if portfolio_id else None
    return maybe_notify(session, user_id, cat, text, level=level, portfolio_name=pf.name if pf else None)


def notify_trade(session: Session, user_id: int, pf: TradePortfolio, kind: str, *, code: str | None, name: str | None,
                 qty: int | None, price: int | None, amount: int | None, memo: str | None, realized: int | None) -> bool:
    ko = {"buy": "매수", "sell": "매도", "deposit": "입금", "withdraw": "출금"}.get(kind, kind)
    us = pf.market == "US"
    fmt = (lambda v: f"${v / 100:,.2f}") if us else (lambda v: f"{int(v):,}원")
    if kind in ("buy", "sell"):
        text = f"체결 등록 — {ko} {name or code} {int(qty or 0):,}주 @{fmt(price or 0)}"
        if kind == "sell" and realized is not None:
            text += f" · 실현 {'+' if realized >= 0 else '-'}{fmt(abs(realized))}"
    else:
        text = f"{ko} 등록 — {fmt(amount or 0)}"
    if memo:
        text += f" · {memo}"
    return maybe_notify(session, user_id, "trades", text, portfolio_name=pf.name)


# ── 일일 현황 (16:40 스냅샷 뒤) ────────────────────────────────────────────────────

def daily_status_text(session: Session, user_id: int, today: date) -> str | None:
    from app.models import AssetSnapshot, PortfolioSnapshot

    snap = session.scalar(select(AssetSnapshot).where(AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date == today))
    if snap is None:
        return None
    prev = session.scalars(select(AssetSnapshot).where(AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date < today)
                           .order_by(AssetSnapshot.snap_date.desc()).limit(1)).first()
    total = int(snap.total or 0)
    line = f"📊 일일 현황 {today.isoformat()} — 총자산 {total:,}원"
    if prev is not None and int(prev.total or 0) > 0:
        # 대시보드와 같은 식 — 입출금은 자산 이동이라 빼고 본다 (단순 Dietz, dashboard.compute_user_snapshot; 2026-09-09 통일)
        from app.dashboard import user_flows_between

        flows = int(user_flows_between(session, user_id, prev.snap_date, today) or 0)
        diff = total - int(prev.total) - flows
        denom = int(prev.total) + flows
        line += f" (전일 대비 {diff:+,}원" + (f", {diff / denom * 100:+.2f}%" if denom > 0 else "") + (f" · 입출금 {flows:+,}원 제외" if flows else "") + ")"
    line += f"\n주식 {int(snap.stock or 0):,}원 · 현금 {int(snap.cash or 0):,}원"
    if int(snap.other or 0):
        line += f" · 기타 {int(snap.other):,}원"
    if int(snap.journal or 0):
        line += f" · 매매일지 {int(snap.journal):,}원"
    pfs = {p.id: p for p in session.scalars(select(TradePortfolio).where(TradePortfolio.user_id == user_id)).all()}
    rows = session.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id.in_(list(pfs)) if pfs else False,
                                                           PortfolioSnapshot.snap_date == today)).all() if pfs else []
    for r in sorted(rows, key=lambda x: -int(x.equity or 0)):
        pf = pfs.get(r.portfolio_id)
        if pf is None:
            continue
        eq = int(r.equity or 0)
        line += f"\n· {pf.name}: " + (f"${eq / 100:,.2f}" if r.currency == "USD" else f"{eq:,}원") + f" (주식 {int(r.stock_value or 0):,} · 현금 {int(r.cash or 0):,})"
    return line


def send_daily_status(session: Session, user_id: int, today: date | None = None) -> bool:
    """일일 현황 발송 — 같은 날 두 번 보내지 않는다 (2026-09-09: 스케줄러 따라잡기로 16:40 스냅샷 배치가 재실행돼 중복 발송). 기록은 notify.daily_status_sent."""
    today = today or datetime.now(KST).date()
    row = _row(session, user_id)
    if row is not None and (row.notify or {}).get("daily_status_sent") == today.isoformat():
        return False
    text = daily_status_text(session, user_id, today)
    if not text:
        return False
    ok = maybe_notify(session, user_id, "daily_status", text)
    if ok and row is not None:
        cfg = dict(row.notify or {})   # maybe_notify 가 last 를 갱신했으므로 다시 읽어 덧붙인다 (JSONB 재할당)
        cfg["daily_status_sent"] = today.isoformat()
        row.notify = cfg
    return ok
