"""장 시작 전 예상 시가 관찰 (2026-09-09 지시 "08:30~09:10 일정 간격으로 체크해서 예상 시가를 표기") — 표시 전용, 발주 판단은 09:01 실행기가 실제 시가로.

08:30~08:59 동시호가: KIS 호가/예상체결(FHKST01010200) 의 예상체결가를 매 분 기록한다. 09:00~09:10: 현재가 조회의 당일 시가(stck_oprc, 확정)를
기록한다. Redis `preopen:expected:{code}:{date}` 에 마지막 값 + 표본(최대 60개)을 두고(TTL 12시간), 주문표(/signals/daily) 가
`expected_open` 으로 실어 화면에 "예상 시가 105,300원 (08:58) — 갭 기준 위/이하" 를 보인다.

2026-09-06 의 08:57 사전 갭 취소(app.preopen, 폐지)와 다르다 — 취소·발주를 하지 않고 보여 주기만 한다. 예상체결가는 근사값이라
실제 시가와 다를 수 있으므로(호가 잔량 기반) 화면에도 '예상' 을 붙인다.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
OPEN_TIME = time(9, 0)
CODES_200 = ("069500", "102110")   # 관찰 대상 — 주문표의 200 ETF 레그 후보(KODEX·TIGER)
MAX_SAMPLES = 60
TTL_SECONDS = 12 * 3600


def _key(code: str, day: date) -> str:
    return f"preopen:expected:{code}:{day.isoformat()}"


def _redis():
    import redis as sync_redis

    from app.config import get_settings

    return sync_redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=1)


def record_sample(code: str, day: date, price: int, kind: str, at: datetime, r=None) -> dict:
    """표본 1개 추가 — kind: expected(동시호가 예상체결가) | open(확정 시가) | current(시가 미확정 시 현재가)."""
    r = r or _redis()
    key = _key(code, day)
    cur = {}
    try:
        raw = r.get(key)
        cur = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        cur = {}
    samples = list(cur.get("samples") or [])
    samples.append({"at": at.astimezone(KST).strftime("%H:%M"), "price": int(price), "kind": kind})
    samples = samples[-MAX_SAMPLES:]
    doc = {"code": code, "date": day.isoformat(), "price": int(price), "kind": kind,
           "at": at.astimezone(KST).strftime("%H:%M"), "samples": samples}
    r.set(key, json.dumps(doc, ensure_ascii=False), ex=TTL_SECONDS)
    return doc


def read_expected(code: str, day: date, r=None) -> dict | None:
    """마지막 관찰값 — 없으면 None. Redis 장애도 None(표시 전용이라 주문표를 막지 않는다)."""
    try:
        r = r or _redis()
        raw = r.get(_key(code, day))
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def expected_open_view(code: str, day: date, gap_cancel_exact: float | None, r=None) -> dict | None:
    """주문표용 — 마지막 값 + 갭 기준 판정. gap_hit: 예상(또는 확정) 시가 ≤ 갭 취소 기준."""
    doc = read_expected(code, day, r)
    if not doc:
        return None
    price = int(doc.get("price") or 0)
    return {"price": price, "at": doc.get("at"), "kind": doc.get("kind"),
            "gap_hit": bool(gap_cancel_exact and price and price <= float(gap_cancel_exact)),
            "samples": [{"at": s["at"], "price": s["price"]} for s in (doc.get("samples") or [])[-12:]]}


def poll_expected_open(now: datetime | None = None, client=None, codes: tuple[str, ...] = CODES_200, r=None) -> dict:
    """1분 주기 태스크 본체 — 09:00 전이면 예상체결가, 09:00 이후면 확정 시가(없으면 현재가). 종목별 실패는 기록만."""
    now = now or datetime.now(KST)
    day = now.date()
    if client is None:
        from app.config import get_settings
        from app.services.kis_auth import KisAuth
        from app.services.kis_client import KisClient

        st = get_settings()
        if not (st.kis_app_key and st.kis_app_secret):
            return {"skipped": "no-kis-keys", "date": day.isoformat()}
        client = KisClient(KisAuth(st.kis_app_key, st.kis_app_secret, st.kis_env))
    r = r or _redis()
    out: dict = {"date": day.isoformat(), "at": now.astimezone(KST).strftime("%H:%M"), "codes": {}}
    for code in codes:
        try:
            if now.astimezone(KST).time() < OPEN_TIME:
                px = int((client.fetch_expected(code) or {}).get("expected") or 0)
                kind = "expected"
            else:
                q = client.fetch_price(code) or {}
                px = int(str(q.get("stck_oprc") or "0").replace(",", "") or 0)
                kind = "open"
                if px <= 0:
                    px = int(str(q.get("stck_prpr") or "0").replace(",", "") or 0)
                    kind = "current"
            if px > 0:
                record_sample(code, day, px, kind, now, r)
                out["codes"][code] = {"price": px, "kind": kind}
            else:
                out["codes"][code] = {"price": None, "kind": kind, "note": "0 (아직 예상체결가 없음)"}
        except Exception as exc:  # noqa: BLE001
            out["codes"][code] = {"error": str(exc)[:120]}
            logger.warning("preopen watch failed code=%s: %s", code, exc)
    return out
