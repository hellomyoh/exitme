"""Celery 앱 — 큐 분리: backtest / ingest (ADR-001).

일일 수집 배치: 장 마감 후 KIS로 당일 일봉 수집(실패 시 pykrx 폴백) → 검증 → 적재.
beat 스케줄은 KST 16:00 (UTC 07:00). 휴장일은 캘린더로 스킵.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from celery import Celery
from sqlalchemy import select

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery("stocklab", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_default_queue="ingest",
    task_routes={
        "app.worker.daily_ingest": {"queue": "ingest"},
        # 백테스트 태스크(Phase 3)는 "backtest" 큐 사용
    },
    task_acks_late=True,
    timezone="Asia/Seoul",
    beat_schedule={
        "daily-ingest": {
            "task": "app.worker.daily_ingest",
            "schedule": 60 * 60 * 24,  # placeholder — Phase 1 마감: crontab(hour=16, minute=0) 로 교체
        },
    },
)

KST = timezone(timedelta(hours=9))


@celery_app.task(name="app.worker.daily_ingest", max_retries=2, autoretry_for=(Exception,), retry_backoff=60)
def daily_ingest(target: str | None = None) -> dict:
    """당일(또는 target=YYYY-MM-DD) 일봉 수집. 추적 종목 = instruments 전체."""
    from app.db import SessionLocal
    from app.models import Instrument, TradingCalendar
    from app.services import pykrx_client
    from app.services.ingest import finish_batch, start_batch, upsert_daily_bars
    from app.services.kis_auth import KisAuth
    from app.services.kis_client import KisClient

    target_date = date.fromisoformat(target) if target else datetime.now(KST).date()
    with SessionLocal() as session:
        cal = session.get(TradingCalendar, target_date)
        if cal is not None and not cal.is_open:
            logger.info("skip daily_ingest: %s is a holiday", target_date)
            return {"skipped": "holiday", "date": target_date.isoformat()}

        run = start_batch(session, "daily_ingest", {"date": target_date.isoformat()})
        totals = {"inserted": 0, "rejected": 0, "fallback": 0, "failed": []}
        kis: KisClient | None = None
        if settings.kis_app_key and settings.kis_app_secret:
            kis = KisClient(KisAuth(settings.kis_app_key, settings.kis_app_secret, settings.kis_env))

        instruments = session.scalars(select(Instrument)).all()
        for inst in instruments:
            try:
                bars: list[dict] = []
                if kis is not None:
                    try:
                        bars = [b.__dict__ for b in kis.fetch_daily(inst.code, target_date, target_date)]
                    except Exception:  # KIS 장애 → pykrx 폴백 (ADR-004)
                        logger.warning("KIS failed for %s, falling back to pykrx", inst.code, exc_info=True)
                        totals["fallback"] += 1
                if not bars:
                    bars = pykrx_client.fetch_daily(inst.code, target_date, target_date)
                res = upsert_daily_bars(session, inst.id, bars, source="kis" if kis else "pykrx")
                totals["inserted"] += res.inserted
                totals["rejected"] += res.rejected
            except Exception as exc:  # 개별 종목 실패는 배치 전체를 죽이지 않는다
                logger.error("ingest failed for %s: %s", inst.code, exc)
                totals["failed"].append(inst.code)
        status = "ok" if not totals["failed"] else "failed"
        finish_batch(session, run, status, totals)
        session.commit()
        return {"status": status, **totals}
