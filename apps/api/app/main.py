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
from app.settings import router as settings_router
from app.signals import router as signals_router

app = FastAPI(title="ExitMe API", version="0.1.0")


@app.on_event("startup")
def _bootstrap_admin() -> None:
    """기본 관리자(myoh) 보증 — 테이블이 아직 없으면(첫 마이그레이션 전) 건너뜀."""
    from app.auth import ensure_admin_account
    from app.db import SessionLocal

    try:
        with SessionLocal() as s:
            ensure_admin_account(s)
    except Exception:
        pass
app.include_router(quotes_router)
app.include_router(auth_router)
app.include_router(charts_router)
app.include_router(backtests_router)
app.include_router(signals_router)
app.include_router(portfolios_router)
app.include_router(dashboard_router)
app.include_router(settings_router)


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
    session.execute(text("SELECT 1"))
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


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
