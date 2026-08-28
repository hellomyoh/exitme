"""Celery 앱 — 큐 분리: backtest / ingest (ADR-001).

일일 수집 배치: 장 마감 후 KIS로 당일 일봉 수집(실패 시 pykrx 폴백) → 검증 → 적재.
beat 스케줄은 KST 16:00 (UTC 07:00). 휴장일은 캘린더로 스킵.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from celery import Celery
from celery.schedules import crontab
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
        # 장 마감 후 일봉 수집 — KST 16:05 (feature-market-data §6: 20분 내 완료 목표)
        "daily-ingest": {
            "task": "app.worker.daily_ingest",
            "schedule": crontab(hour=16, minute=5, day_of_week="mon-fri"),
        },
        # 장중 현재가 폴링 — 10초 (ASSUMPTIONS: 기본값, KIS 한도 실측 후 조정)
        "poll-quotes": {
            "task": "app.worker.poll_quotes",
            "schedule": 10.0,
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


@celery_app.task(name="app.worker.poll_quotes", ignore_result=True)
def poll_quotes() -> dict:
    """장중 현재가 폴링 → Redis 캐시 + 채널 push. 키 미설정·휴장·장외 시간은 조용히 스킵."""
    import json

    import redis as sync_redis

    from app.db import SessionLocal
    from app.models import Instrument, TradingCalendar
    from app.quotes import CHANNEL, cache_key
    from app.services.kis_auth import KisAuth
    from app.services.kis_client import KisClient

    if not (settings.kis_app_key and settings.kis_app_secret):
        return {"skipped": "no-keys"}
    now = datetime.now(KST)
    if not (9 <= now.hour < 16):
        return {"skipped": "off-hours"}
    with SessionLocal() as session:
        cal = session.get(TradingCalendar, now.date())
        if cal is not None and not cal.is_open:
            return {"skipped": "holiday"}
        codes = [c for (c,) in session.execute(select(Instrument.code))]
    client = KisClient(KisAuth(settings.kis_app_key, settings.kis_app_secret, settings.kis_env))
    r = sync_redis.from_url(settings.redis_url, decode_responses=True)
    pushed = 0
    for code in codes:
        try:
            out = client.fetch_price(code)
            quote = {
                "code": code,
                "price": int(out["stck_prpr"]),
                "change": int(out.get("prdy_vrss", 0)),
                "volume": int(out.get("acml_vol", 0)),
                "as_of": now.isoformat(),
            }
            payload = json.dumps(quote, ensure_ascii=False)
            r.set(cache_key(code), payload, ex=300)
            r.publish(CHANNEL, payload)
            pushed += 1
        except Exception:
            logger.warning("poll_quotes failed for %s", code, exc_info=True)
    return {"pushed": pushed}
