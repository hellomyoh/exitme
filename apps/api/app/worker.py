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
        "app.worker.run_backtest_job": {"queue": "backtest"},
        "app.worker.daily_signal": {"queue": "ingest"},
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
        # 일별 자산 스냅샷 — KST 16:40 (시그널 배치 후)
        "daily-snapshot": {
            "task": "app.worker.daily_asset_snapshot",
            "schedule": crontab(hour=16, minute=40, day_of_week="mon-fri"),
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
                if kis is not None:
                    # 1분봉 증분 수집 — 당일분만 (과거 소급은 scripts.seed_minutes)
                    from app.services.ingest import upsert_minute_bars

                    mbars = kis.fetch_minutes_day(inst.code, target_date)
                    mres = upsert_minute_bars(session, inst.id, mbars, source="kis")
                    totals["minutes_inserted"] = totals.get("minutes_inserted", 0) + mres.inserted
            except Exception as exc:  # 개별 종목 실패는 배치 전체를 죽이지 않는다
                logger.error("ingest failed for %s: %s", inst.code, exc)
                totals["failed"].append(inst.code)
        status = "ok" if not totals["failed"] else "failed"
        finish_batch(session, run, status, totals)
        session.commit()
    if status == "ok":
        # 시세 확보 → 전략 엔진 배치 트리거 (feature-market-data §5)
        daily_signal.apply_async(args=[target_date.isoformat()], queue="ingest")
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


@celery_app.task(name="app.worker.run_backtest_job")
def run_backtest_job(bt_id: int) -> dict:
    """백테스트 잡 실행 — 진행률 1% 발행, 취소 폴링, 결과 단일 트랜잭션 저장 (feature-backtest §8)."""
    import json as _json
    from dataclasses import asdict
    from datetime import date as _date

    import redis as sync_redis

    from app.backtests import CANCEL_KEY, PROGRESS_CH, PROGRESS_KEY, load_bars_with_warmup, pair_from_params
    from app.db import SessionLocal
    from app.models import Backtest, BacktestEquity
    from app.strategy.backtest import Cancelled, run_backtest
    from app.strategy.params import AblationFlags, Params

    r = sync_redis.from_url(settings.redis_url, decode_responses=True)

    def publish(payload: dict) -> None:
        data = _json.dumps(payload, ensure_ascii=False)
        r.set(PROGRESS_KEY.format(id=bt_id), data, ex=3600)
        r.publish(PROGRESS_CH.format(id=bt_id), data)

    with SessionLocal() as session:
        bt = session.get(Backtest, bt_id)
        if bt is None:
            return {"error": "not found"}
        if bt.status == "DONE":
            # acks_late 재전달로 완료 잡이 다시 들어올 수 있음 — 결과 보존, 재실행 금지 (멱등)
            return {"status": "already-done"}
        try:
            p = bt.params
            bars_200, bars_lev, fp, start_idx = load_bars_with_warmup(
                session, _date.fromisoformat(p["date_from"]), _date.fromisoformat(p["date_to"]),
                codes=pair_from_params(p),
            )
            bt.status = "RUNNING"
            bt.data_fingerprint = fp
            session.commit()
            publish({"id": bt_id, "status": "RUNNING", "progress": 0})

            params = Params(**p.get("costs", {}), flags=AblationFlags(**p.get("flags", {})))

            def progress_cb(done: int, total: int) -> bool:
                if r.get(CANCEL_KEY.format(id=bt_id)):
                    return False
                pct = int(done * 100 / total)
                publish({"id": bt_id, "status": "RUNNING", "progress": pct})
                return True

            result = run_backtest(bars_200, bars_lev, float(p["capital"]), params,
                                  start_index=start_idx, progress_cb=progress_cb)

            # 단일 트랜잭션 저장 — 재시도 멱등: 이전 시도의 잔여 행을 먼저 제거
            session.query(BacktestEquity).filter(BacktestEquity.backtest_id == bt_id).delete()
            bt.kpi = result.kpi
            bt.trades = [asdict(t) for t in result.trades]
            bt.status = "DONE"
            bt.progress = 100
            for d, eq, bench, reg, exp in zip(result.dates, result.equity, result.benchmark,
                                              result.regimes, result.exposures):
                session.add(BacktestEquity(backtest_id=bt_id, trade_date=_date.fromisoformat(d),
                                           equity=eq, benchmark=bench, regime=reg, exposure=exp))
            session.commit()
            publish({"id": bt_id, "status": "DONE", "progress": 100, "kpi": result.kpi})
            return {"status": "DONE"}
        except Cancelled:
            session.rollback()
            bt = session.get(Backtest, bt_id)
            bt.status = "CANCELED"
            session.commit()
            publish({"id": bt_id, "status": "CANCELED", "progress": bt.progress})
            return {"status": "CANCELED"}
        except Exception as exc:
            session.rollback()
            bt = session.get(Backtest, bt_id)
            bt.status = "FAILED"
            bt.error = str(exc)[:2000]
            session.commit()
            publish({"id": bt_id, "status": "FAILED", "error": str(exc)[:500]})
            logger.exception("backtest %s failed", bt_id)
            return {"status": "FAILED"}


@celery_app.task(name="app.worker.daily_signal", max_retries=2, autoretry_for=(Exception,), retry_backoff=60)
def daily_signal(target: str | None = None) -> dict:
    """일일 시그널 배치 — 주문표 생성 (feature-strategy-engine §5.8). batch_runs 기록."""
    from datetime import date as _date

    from app.db import SessionLocal
    from app.services.ingest import finish_batch, start_batch
    from app.signals import run_signal_batch

    with SessionLocal() as session:
        run = start_batch(session, "daily_signal", {"target": target})
        try:
            snap = run_signal_batch(session, _date.fromisoformat(target) if target else None)
            finish_batch(session, run, "ok" if snap.status == "OK" else "failed",
                         {"signal_status": snap.status, "trade_date": snap.trade_date.isoformat(),
                          "version": snap.version})
            session.commit()
            return {"status": snap.status, "trade_date": snap.trade_date.isoformat()}
        except Exception as exc:
            finish_batch(session, run, "failed", {"error": str(exc)[:500]})
            session.commit()
            raise


@celery_app.task(name="app.worker.daily_asset_snapshot")
def daily_asset_snapshot() -> dict:
    """전 사용자 자산 스냅샷 적재 (feature-dashboard §5 — 추이·캘린더 원천)."""
    from datetime import date as _date

    from app.dashboard import compute_user_snapshot
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as session:
        users = session.scalars(select(User)).all()
        for u in users:
            compute_user_snapshot(session, u.id, _date.today())
        session.commit()
        return {"users": len(users)}
