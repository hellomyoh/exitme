"""KIS Open API 인증 — 공식 예제 kis_auth.py 패턴을 따름.

참고: https://github.com/koreainvestment/open-trading-api (examples_llm/kis_auth.py)
- 토큰 발급: POST {base}/oauth2/tokenP, grant_type=client_credentials
- 토큰은 만료까지 재사용 (KIS는 과도한 재발급을 제한함)
키는 kis_devlp.yaml 이 아니라 .env 로 관리한다 (ARCHITECTURE §8, ASSUMPTIONS 참조).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

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


@dataclass
class _Token:
    value: str
    expires_at: datetime


class KisAuth:
    def __init__(self, app_key: str, app_secret: str, env: str = "prod") -> None:
        if env not in BASE_URLS:
            raise ValueError(f"KIS_ENV must be prod|vps, got {env!r}")
        self.app_key = app_key
        self.app_secret = app_secret
        self.env = env
        self.base_url = BASE_URLS[env]
        self._token: _Token | None = None
        self._lock = threading.Lock()

    def access_token(self, session: requests.Session | None = None) -> str:
        with self._lock:
            now = datetime.now()
            if self._token and self._token.expires_at - _EXPIRY_MARGIN > now:
                return self._token.value
            self._token = self._issue(session or requests.Session())
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
        resp.raise_for_status()
        body = resp.json()
        token = body["access_token"]
        # 공식 응답: access_token_token_expired = "YYYY-MM-DD HH:MM:SS", expires_in = 초
        expired_str = body.get("access_token_token_expired")
        if expired_str:
            expires_at = datetime.strptime(expired_str, "%Y-%m-%d %H:%M:%S")
        else:
            expires_at = datetime.now() + timedelta(seconds=int(body.get("expires_in", 86400)))
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
