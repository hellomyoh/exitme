"""ExitMe API — 계약은 THROUGHLINE/ARCHITECTURE.md §5 (problem+json, as_of/delayed 포함)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Instrument, OhlcvDaily, OhlcvIntraday
from app.auth import router as auth_router
from app.backtests import router as backtests_router
from app.charts import router as charts_router
from app.dashboard import router as dashboard_router
from app.portfolios import router as portfolios_router
from app.quotes import router as quotes_router
from app.broker import router as broker_router
from app.chat import router as chat_router
from app.mjournal import router as mjournal_router
from app.settings import router as settings_router
from app.signals import router as signals_router

_APP_DIR = __import__("pathlib").Path(__file__).resolve().parent


def app_version() -> str:
    """배포 버전 — `app/VERSION` 파일(태그 시 함께 갱신, AGENTS.md "버저닝과 태그")을 읽는다.

    APP_VERSION 환경변수가 비어 있지 않으면 그것으로 덮어쓴다(선택). 파일도 없으면 "dev".
    """
    import os

    env = (os.environ.get("APP_VERSION") or "").strip()
    if env:
        return env
    try:
        return "v" + (_APP_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"


def build_time() -> str | None:
    """이미지 빌드 시각(UTC ISO) — Dockerfile 이 /srv/app/BUILD_TIME 에 기록. 개발 bind mount 에서는 없음."""
    try:
        return (_APP_DIR.parent / "BUILD_TIME").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


app = FastAPI(title="ExitMe API", version=app_version().lstrip("v"))


@app.on_event("startup")
def _migrate_and_bootstrap() -> None:
    """기동 시 마이그레이션 자동 적용 + 기본 관리자 보증 (2026-09-02).

    'git pull 후 alembic 누락'으로 원격 배포가 반복적으로 깨져(0010·0011) 자동화.
    다중 레플리카 경합은 PostgreSQL advisory lock 으로 직렬화한다.
    """
    import logging

    from sqlalchemy import text

    from app.auth import ensure_admin_account
    from app.db import SessionLocal, engine

    log = logging.getLogger("startup")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(772026)"))
            try:
                from alembic import command
                from alembic.config import Config

                cfg = Config("alembic.ini")
                command.upgrade(cfg, "head")
                log.info("alembic upgrade head 완료")
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(772026)"))
    except Exception:
        log.exception("startup migration failed — 수동 확인 필요")
    try:
        with SessionLocal() as s:
            ensure_admin_account(s)
    except Exception:
        log.exception("admin bootstrap failed")
app.include_router(quotes_router)
app.include_router(auth_router)
app.include_router(charts_router)
app.include_router(backtests_router)
app.include_router(signals_router)
app.include_router(portfolios_router)
app.include_router(dashboard_router)
app.include_router(settings_router)
app.include_router(chat_router)
app.include_router(mjournal_router)
app.include_router(broker_router)


def problem(status: int, title: str, detail: str, instance: str) -> JSONResponse:
    """RFC 9457 problem+json 공통 에러 포맷 (ARCHITECTURE §5)."""
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": f"https://stocklab.example/problems/{title.replace(' ', '-').lower()}",
            "title": title,
            "status": status,
            "detail": detail,
            "instance": instance,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return problem(500, "internal error", str(exc), str(request.url.path))


@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    """상태 + **배포 확인용 정보** (2026-09-05 지시: 원격 반영 여부를 바로 판별).

    version = app/VERSION(태그와 함께 갱신), build_time = 이미지 빌드 시각(UTC), db_revision = alembic 리비전.
    운영자가 환경변수를 넘길 필요가 없다 — 새로 빌드하면 build_time 이 바뀌고, 마이그레이션이 돌면 db_revision 이 바뀐다.
    """
    session.execute(text("SELECT 1"))
    try:
        rev = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 — 마이그레이션 전이면 테이블이 없다
        rev = None
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(),
            "version": app_version(), "build_time": build_time(), "db_revision": rev}


@app.get("/instruments")
def search_instruments(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, le=100),
    session: Session = Depends(get_session),
) -> dict:
    rows = session.scalars(
        select(Instrument)
        .where((Instrument.code.ilike(f"%{q}%")) | (Instrument.name.ilike(f"%{q}%")))
        .order_by(Instrument.code)
        .limit(limit)
    ).all()
    return {
        "items": [
            {"code": r.code, "name": r.name, "market": r.market, "type": r.type} for r in rows
        ]
    }


@app.get("/ohlcv")
def get_ohlcv(
    request: Request,
    code: str,
    from_: date = Query(alias="from"),
    to: date = Query(),
    timeframe: str = Query(default="D", pattern="^(D|1m)$"),
    limit: int = Query(default=1000, le=200000),
    session: Session = Depends(get_session),
):
    """시세 조회 — 수정주가(= raw × adj_factor) 기준, as_of/delayed 포함 (ARCHITECTURE §5).

    timeframe: D(일봉, 기본) | 1m(분봉 — KIS 보관 한도 내 증분 수집분)
    """
    inst = session.scalar(select(Instrument).where(Instrument.code == code))
    if inst is None:
        return problem(404, "instrument not found", f"unknown code {code}", str(request.url.path))
    if timeframe == "1m":
        rows = session.execute(
            select(OhlcvIntraday)
            .where(OhlcvIntraday.instrument_id == inst.id, OhlcvIntraday.timeframe == "1m",
                   OhlcvIntraday.ts >= from_, OhlcvIntraday.ts < to + __import__("datetime").timedelta(days=1))
            .order_by(OhlcvIntraday.ts)
            .limit(limit)
        ).scalars().all()
        as_of = session.scalar(
            select(func.max(OhlcvIntraday.ingested_at)).where(OhlcvIntraday.instrument_id == inst.id)
        )
        return {
            "code": code, "timeframe": "1m",
            "as_of": as_of.isoformat() if as_of else None, "delayed": True,
            "items": [
                {"ts": r.ts.isoformat(), "open": r.open_raw, "high": r.high_raw,
                 "low": r.low_raw, "close": r.close_raw, "volume": r.volume}
                for r in rows
            ],
        }
    rows = session.execute(
        select(OhlcvDaily)
        .where(
            OhlcvDaily.instrument_id == inst.id,
            OhlcvDaily.trade_date >= from_,
            OhlcvDaily.trade_date <= to,
        )
        .order_by(OhlcvDaily.trade_date)
        .limit(limit)
    ).scalars().all()
    as_of = session.scalar(
        select(func.max(OhlcvDaily.ingested_at)).where(OhlcvDaily.instrument_id == inst.id)
    )
    return {
        "code": code,
        "as_of": as_of.isoformat() if as_of else None,
        "delayed": True,  # v1: 장중 실시간 아님 — 정직한 표기 (ARCHITECTURE §10)
        "items": [
            {
                "date": r.trade_date.isoformat(),
                "open": round(r.open_raw * float(r.adj_factor)),
                "high": round(r.high_raw * float(r.adj_factor)),
                "low": round(r.low_raw * float(r.adj_factor)),
                "close": round(r.close_raw * float(r.adj_factor)),
                "volume": r.volume,
            }
            for r in rows
        ],
    }
