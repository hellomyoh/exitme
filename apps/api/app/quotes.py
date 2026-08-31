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

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

CHANNEL = "quotes:stream"
LAST_KEY = "quotes:last:{code}"


def cache_key(code: str) -> str:
    return LAST_KEY.format(code=code)


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
