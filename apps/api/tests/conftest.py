"""테스트 공통 — DB 격리 강제 + KisAuth Redis 공유 토큰 캐시 차단.

(1) DB 격리 강제 (2026-09-01 오염 재발 — NOTES.md "테스트 DB 격리" 참조):
    qa/README 의 수동 `-e DATABASE_URL=...stocklab_ci` 규칙만으로는 격리가 지켜지지 않았다 —
    컨테이너 안에서 `pytest` 를 그대로 실행하면 compose 의 DATABASE_URL(개발 DB stocklab)로
    통합 테스트가 실코드(069500/102110/122630)에 합성 봉을 적재한다(2026-08-28·09-01 두 차례 실사고).
    그래서 conftest 가 DB 이름이 `_ci` 로 끝나지 않으면 stocklab_ci 로 강제 재지정하고,
    격리 DB 가 없으면 생성 + alembic 마이그레이션까지 수행한다.
    app.* 모듈이 settings 를 읽기 전에 실행되어야 하므로 반드시 파일 최상단에 둔다.

(2) Redis 토큰 캐시 차단: 실행 환경의 Redis 가 살아 있으면 mock 토큰이 프로세스 간에 새어
    다른 테스트(호출 횟수 검증)를 오염시키므로, 테스트에서는 항상 메모리 캐시만 사용한다.
"""
import os
import pathlib
import sys

_DEFAULT_URL = "postgresql+psycopg://stocklab:stocklab-dev-password@db:5432/stocklab"


def _isolated_db_url() -> str:
    url = os.environ.get("DATABASE_URL", _DEFAULT_URL)
    base, _, name = url.rpartition("/")
    if name.endswith("_ci"):
        return url
    print(f"[conftest] DATABASE_URL({name}) 은 격리 DB 가 아님 → stocklab_ci 로 재지정", file=sys.stderr)
    return f"{base}/stocklab_ci"


os.environ["DATABASE_URL"] = _isolated_db_url()
os.environ.setdefault("ALLOW_OPEN_REGISTRATION", "true")  # 테스트 전용 — 운영 기본은 차단 (2026-09-01)


def _ensure_ci_schema() -> None:
    """격리 DB 가 없으면 생성하고 head 까지 마이그레이션한다.

    DB 서버 자체가 없으면 조용히 통과 — 통합 테스트는 각 모듈의 DB_UP 게이트가 스킵한다.
    """
    url = os.environ["DATABASE_URL"]
    base, _, dbname = url.rpartition("/")
    try:
        import psycopg

        admin_dsn = base.replace("postgresql+psycopg", "postgresql", 1) + "/postgres"
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=3) as conn:
            row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
            if row is None:
                conn.execute(f'CREATE DATABASE "{dbname}"')
                print(f"[conftest] 격리 DB {dbname} 생성", file=sys.stderr)
    except Exception:
        return
    from alembic import command
    from alembic.config import Config

    api_root = pathlib.Path(__file__).resolve().parents[1]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    command.upgrade(cfg, "head")


_ensure_ci_schema()

import pytest

from app.services.kis_auth import KisAuth


@pytest.fixture(autouse=True)
def _no_shared_token_cache(monkeypatch):
    monkeypatch.setattr(KisAuth, "_redis", lambda self: None)
