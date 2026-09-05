"""KIS Open API 인증 — 공식 예제 kis_auth.py 패턴을 따름.

참고: https://github.com/koreainvestment/open-trading-api (examples_llm/kis_auth.py)
- 토큰 발급: POST {base}/oauth2/tokenP, grant_type=client_credentials
- 토큰은 만료까지 재사용 (KIS는 과도한 재발급을 제한함)
키는 kis_devlp.yaml 이 아니라 .env 로 관리한다 (ARCHITECTURE §8, ASSUMPTIONS 참조).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# KIS 만료시각(access_token_token_expired)은 KST 벽시계 문자열 — 컨테이너는 UTC 라서
# naive 비교 시 9시간 과대평가(만료 토큰을 9시간 더 사용) 버그가 있었다 (2026-09-05 검토).
from datetime import timezone as _tz  # noqa: E402

KST = _tz(timedelta(hours=9))


def _now() -> datetime:
    return datetime.now(KST)

BASE_URLS = {
    "prod": "https://openapi.koreainvestment.com:9443",
    "vps": "https://openapivts.koreainvestment.com:29443",
}
TOKEN_PATH = "/oauth2/tokenP"
# 만료 임박 재발급 여유
_EXPIRY_MARGIN = timedelta(minutes=10)


def mask(secret: str) -> str:
    """로그 마스킹 — 키 값이 로그에 노출되면 안 된다 (feature-market-data §10)."""
    if len(secret) <= 8:
        return "****"
    return secret[:4] + "****" + secret[-2:]


class KisAuthError(RuntimeError):
    """토큰 발급 실패 — KIS 응답 메시지를 그대로 담는다 (사용자 안내용, 2026-09-05)."""

    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


# 분당 1회 발급 제한 코드/문구 (그 외 403 은 자격·환경 오류로 취급)
_RATE_LIMIT_HINTS = ("EGW00133", "1분", "초당", "빈번", "too many")


@dataclass
class _Token:
    value: str
    expires_at: datetime


class KisAuth:
    def __init__(self, app_key: str, app_secret: str, env: str = "prod",
                 wait_on_rate_limit: bool = True) -> None:
        if env not in BASE_URLS:
            raise ValueError(f"KIS_ENV must be prod|vps, got {env!r}")
        self.wait_on_rate_limit = wait_on_rate_limit  # 대화형 호출은 대기하지 않는다 (2026-09-05)
        self.app_key = app_key
        self.app_secret = app_secret
        self.env = env
        self.base_url = BASE_URLS[env]
        self._token: _Token | None = None
        self._lock = threading.Lock()

    # ── Redis 공유 토큰 캐시 — KIS는 토큰 발급을 분당 1회로 제한하므로(EGW00133/403)
    #    프로세스(api/worker/scheduler/run)간 토큰을 공유해야 한다 (NOTES.md).
    def _redis_key(self) -> str:
        """자격(키+시크릿) 전체를 해시해 캐시를 가른다 (2026-09-05).

        앞 8자만 쓰던 이전 방식은 시크릿을 바꿔도 **옛 토큰을 계속 재사용**해,
        헤더의 appsecret 과 토큰이 어긋난 채 EGW00304 로만 실패했다.
        """
        digest = hashlib.sha256(f"{self.app_key}:{self.app_secret}".encode()).hexdigest()[:16]
        return f"kis:token:{self.env}:{digest}"

    def _redis(self):
        try:
            import redis as sync_redis

            from app.config import get_settings

            r = sync_redis.from_url(get_settings().redis_url, decode_responses=True,
                                    socket_connect_timeout=1)
            r.ping()
            return r
        except Exception:
            return None  # 테스트/redis 부재 환경 — 메모리 캐시만 사용

    def _load_shared(self) -> _Token | None:
        r = self._redis()
        if r is None:
            return None
        raw = r.get(self._redis_key())
        if not raw:
            return None
        try:
            data = json.loads(raw)
            exp = datetime.fromisoformat(data["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=KST)  # 구버전 캐시(naive=KST) 호환
            return _Token(data["value"], exp)
        except (ValueError, KeyError):
            return None

    def _store_shared(self, token: _Token) -> None:
        r = self._redis()
        if r is None:
            return
        ttl = max(int((token.expires_at - _now()).total_seconds()), 60)
        r.set(self._redis_key(),
              json.dumps({"value": token.value, "expires_at": token.expires_at.isoformat()}),
              ex=ttl)

    def access_token(self, session: requests.Session | None = None) -> str:
        """캐시(메모리→Redis) 우선, 없으면 발급.

        발급 실패 시 KIS 메시지를 KisAuthError 로 올린다. **분당 제한일 때만** 대기 후 재시도하며,
        대화형 호출(wait_on_rate_limit=False)은 기다리지 않고 즉시 알린다 —
        모든 403 을 제한으로 보고 65초 멈췄다가 결국 실패하던 결함 수정 (2026-09-05).
        """
        with self._lock:
            now = _now()
            if self._token and self._token.expires_at - _EXPIRY_MARGIN > now:
                return self._token.value
            shared = self._load_shared()
            if shared and shared.expires_at - _EXPIRY_MARGIN > now:
                self._token = shared
                return shared.value
            sess = session or requests.Session()
            try:
                self._token = self._issue(sess)
            except KisAuthError as exc:
                if not exc.rate_limited:
                    raise
                logger.warning("KIS token rate-limited key=%s (wait=%s)",
                               mask(self.app_key), self.wait_on_rate_limit)
                if not self.wait_on_rate_limit:
                    raise
                time.sleep(65)  # 배치 경로만 — 그 사이 타 프로세스 발급분 재확인
                shared = self._load_shared()
                if shared and shared.expires_at - _EXPIRY_MARGIN > _now():
                    self._token = shared
                    return shared.value
                self._token = self._issue(sess)
            self._store_shared(self._token)
            return self._token.value

    def _issue(self, session: requests.Session) -> _Token:
        resp = session.post(
            self.base_url + TOKEN_PATH,
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            # KIS 는 사유를 본문에 준다 — 코드·메시지를 그대로 올려 사용자에게 안내한다
            try:
                b = resp.json()
            except ValueError:
                b = {}
            code = str(b.get("error_code") or b.get("msg_cd") or "")
            msg = str(b.get("error_description") or b.get("msg1") or resp.text[:200]).strip()
            blob = f"{code} {msg}"
            rate = any(h.lower() in blob.lower() for h in _RATE_LIMIT_HINTS)
            detail = f"{msg or 'KIS 토큰 발급 실패'} (HTTP {resp.status_code}{f', {code}' if code else ''})"
            logger.warning("KIS token issue failed env=%s key=%s -> %s", self.env, mask(self.app_key), detail)
            raise KisAuthError(detail, rate_limited=rate)
        body = resp.json()
        token = body["access_token"]
        # 공식 응답: access_token_token_expired = "YYYY-MM-DD HH:MM:SS", expires_in = 초
        expired_str = body.get("access_token_token_expired")
        if expired_str:
            expires_at = datetime.strptime(expired_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        else:
            expires_at = _now() + timedelta(seconds=int(body.get("expires_in", 86400)))
        logger.info("KIS token issued env=%s key=%s expires=%s", self.env, mask(self.app_key), expires_at)
        return _Token(value=token, expires_at=expires_at)

    def headers(self, tr_id: str, session: requests.Session | None = None) -> dict[str, str]:
        """공통 헤더 — 공식 예제와 동일 구성.

        모의(vps)는 주문·계좌 계열 TR(T…)만 V로 치환한다.
        시세 조회 TR(FHKST…)은 실전/모의 공통이므로 치환하지 않는다.
        """
        if self.env == "vps" and tr_id.startswith("T"):
            tr_id = "V" + tr_id[1:]
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token(session)}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
