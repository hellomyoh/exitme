"""애플리케이션 설정 — .env 단일 관리 (ARCHITECTURE §8).

KIS 키는 공식 예제(kis_devlp.yaml)와 달리 .env 로 관리한다.
사용자는 프로젝트 루트의 .env 에 KIS_APP_KEY / KIS_APP_SECRET 를 기입한다.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_env: str = "prod"  # prod(실전) | vps(모의)

    database_url: str = "postgresql+psycopg://stocklab:stocklab-dev-password@db:5432/stocklab"
    redis_url: str = "redis://redis:6379/0"

    jwt_secret: str = "change-me"
    allow_open_registration: bool = False  # 계정은 관리자 발급 원칙 (2026-09-01) — 테스트에서만 env 로 개방
    encryption_key: str = "change-me-32bytes-base64"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
