"""WS /ws/quotes 릴레이 테스트 — redis 필요(compose). 미접속 시 skip.

검증: 구독 시 캐시 즉시 수신, 채널 publish 릴레이, 미구독 코드 미수신.
"""
import json
import threading
import time

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.quotes import CHANNEL, cache_key

try:
    _r = sync_redis.from_url(get_settings().redis_url, socket_connect_timeout=2)
    _r.ping()
    REDIS_UP = True
except Exception:
    REDIS_UP = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not REDIS_UP, reason="redis not reachable"),
]


def quote(code: str, price: int) -> str:
    return json.dumps({"code": code, "price": price, "as_of": "t"})


def test_cached_quote_sent_on_subscribe():
    _r.set(cache_key("069500"), quote("069500", 70000), ex=60)
    client = TestClient(app)
    with client.websocket_connect("/ws/quotes") as ws:
        ws.send_json({"subscribe": ["069500"]})
        msg = ws.receive_json()
        assert msg["code"] == "069500" and msg["price"] == 70000


def test_publish_relayed_only_to_subscribed():
    _r.delete(cache_key("122630"), cache_key("XXXXXX"))
    client = TestClient(app)
    with client.websocket_connect("/ws/quotes") as ws:
        ws.send_json({"subscribe": ["122630"]})
        time.sleep(0.3)  # pubsub 구독 안정화

        def publish():
            time.sleep(0.2)
            _r.publish(CHANNEL, quote("XXXXXX", 1))   # 미구독 — 수신되면 안 됨
            _r.publish(CHANNEL, quote("122630", 20000))

        t = threading.Thread(target=publish)
        t.start()
        msg = ws.receive_json()
        t.join()
        assert msg["code"] == "122630" and msg["price"] == 20000
