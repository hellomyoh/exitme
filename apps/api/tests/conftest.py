"""테스트 공통 — KisAuth 의 Redis 공유 토큰 캐시를 차단한다.

실행 환경의 Redis 가 살아 있으면 mock 토큰이 프로세스 간에 새어 다른 테스트(호출 횟수 검증)를
오염시키므로, 테스트에서는 항상 메모리 캐시만 사용한다.
"""
import os

os.environ.setdefault("ALLOW_OPEN_REGISTRATION", "true")  # 테스트 전용 — 운영 기본은 차단 (2026-09-01)

import pytest

from app.services.kis_auth import KisAuth


@pytest.fixture(autouse=True)
def _no_shared_token_cache(monkeypatch):
    monkeypatch.setattr(KisAuth, "_redis", lambda self: None)
