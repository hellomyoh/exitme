"""실시간(폴링) 시세 — Redis pub/sub → WS 팬아웃 (feature-market-data §5, ARCHITECTURE §1).

구조: worker의 poll_quotes 태스크가 KIS 현재가를 폴링해 Redis에
  - 캐시 키  quotes:last:{code} (마지막 시세 JSON)
  - 채널     quotes:stream      (변경 push)
로 넣고, WS 핸들러는 구독 코드의 캐시를 즉시 보낸 뒤 채널을 릴레이한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app.auth import current_user_id
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

CHANNEL = "quotes:stream"
LAST_KEY = "quotes:last:{code}"
SERIES_KEY = "quotes:series:{code}:{day}"       # 장중 현재가 시계열 — 10초 폴링이 누적, 주문표 실시간 그래프가 읽는다 (2026-09-09)
SERIES_MAX = 3000                                 # 10초 × 6.5시간 ≈ 2,340점 + 여유
SERIES_TTL = 24 * 3600
BACKFILL_FLAG = "quotes:backfill:{code}:{day}"    # 1분봉 백필은 10분에 1회만 (KIS 13호출)


def cache_key(code: str) -> str:
    return LAST_KEY.format(code=code)


def series_key(code: str, day) -> str:
    return SERIES_KEY.format(code=code, day=day.isoformat() if hasattr(day, "isoformat") else str(day))


def append_sample(r, code: str, day, ts: int, price: int) -> None:
    """시계열에 한 점 추가 (RPUSH) — 같은 초의 중복은 그대로 두고(폴링 10초라 사실상 없음) 길이·TTL 만 관리."""
    key = series_key(code, day)
    r.rpush(key, json.dumps([int(ts), int(price)]))
    r.ltrim(key, -SERIES_MAX, -1)
    r.expire(key, SERIES_TTL)


def read_series(r, code: str, day) -> list[dict]:
    """[{t: epoch초, p: 가격}] 시각순."""
    out = []
    for raw in r.lrange(series_key(code, day), 0, -1) or []:
        try:
            t, p = json.loads(raw)
            out.append({"t": int(t), "p": int(p)})
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda x: x["t"])
    return out


def backfill_from_minutes(r, code: str, day, client) -> int:
    """누적이 비었을 때(워커 장중 재시작 등) KIS 1분봉으로 그날 시계열을 채운다 — 분 종가를 한 점씩. 반환: 추가 점 수."""
    flag = BACKFILL_FLAG.format(code=code, day=day.isoformat())
    if not r.set(flag, "1", nx=True, ex=600):
        return 0
    bars = client.fetch_minutes_day(code, day)
    n = 0
    for b in bars:
        ts = int(b.ts.timestamp())
        append_sample(r, code, day, ts, int(b.close))
        n += 1
    return n


@router.get("/quotes/series")
def get_quote_series(code: str = Query(min_length=1, max_length=12), date_: date | None = Query(default=None, alias="date"),
                     _user: int = Depends(current_user_id)) -> dict:
    """장중 현재가 시계열 — 주문표 실시간 그래프의 첫 로드용. 비어 있고 오늘이면 KIS 1분봉으로 1회 백필(10분 잠금)."""
    import redis as sync_redis

    from app.config import get_settings
    from app.dashboard import kst_today

    st = get_settings()
    day = date_ or kst_today()
    r = sync_redis.from_url(st.redis_url, decode_responses=True, socket_connect_timeout=1)
    items = read_series(r, code, day)
    backfilled = 0
    if not items and day == kst_today() and st.kis_app_key and st.kis_app_secret:
        try:
            from app.services.kis_auth import KisAuth
            from app.services.kis_client import KisClient

            backfilled = backfill_from_minutes(r, code, day, KisClient(KisAuth(st.kis_app_key, st.kis_app_secret, st.kis_env)))
            if backfilled:
                items = read_series(r, code, day)
        except Exception as exc:  # noqa: BLE001 — 백필 실패는 빈 그래프로 시작 (표시 전용)
            logger.warning("quote series backfill failed code=%s: %s", code, exc)
    return {"code": code, "date": day.isoformat(), "items": items, "backfilled": backfilled}


@router.websocket("/ws/quotes")
async def ws_quotes(ws: WebSocket) -> None:
    """클라이언트: {"subscribe": ["069500", ...]} 전송 → 서버: 코드별 시세 JSON push."""
    await ws.accept()
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    subscribed: set[str] = set()
    pubsub = r.pubsub()
    await pubsub.subscribe(CHANNEL)

    async def relay() -> None:
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            try:
                quote = json.loads(msg["data"])
            except ValueError:
                continue
            if quote.get("code") in subscribed:
                await ws.send_json(quote)

    relay_task = asyncio.create_task(relay())
    try:
        while True:
            data = await ws.receive_json()
            for code in data.get("subscribe", []):
                subscribed.add(code)
                cached = await r.get(cache_key(code))
                if cached:
                    await ws.send_json(json.loads(cached))
            for code in data.get("unsubscribe", []):
                subscribed.discard(code)
    except WebSocketDisconnect:
        pass
    finally:
        relay_task.cancel()
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()
        await r.aclose()
