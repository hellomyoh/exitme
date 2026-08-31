"""1분봉 증분 수집 — KIS 주식일별분봉조회 (보관 한도 약 1년, feature-market-data §5).

사용: docker compose run --rm api python -m scripts.seed_minutes [--codes 069500,122630] [--days 365]

- **증분**: 코드별 DB 최신 ts 이후만 수집 (최신 ts 당일은 재조회 — ON CONFLICT 멱등).
  DB 가 비어 있으면 (오늘 − days)부터. 백테스트는 DB만 읽으므로 재실행마다 API 를 다시 부르지 않는다.
- 거래일은 trading_calendar 기준. 하루 단위 commit — 중단 후 재실행 시 이어받기.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Instrument, OhlcvIntraday, TradingCalendar
from app.services.ingest import finish_batch, start_batch, upsert_minute_bars
from app.services.kis_auth import KisAuth
from app.services.kis_client import KisClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("seed_minutes")

DEFAULT_CODES = ["069500", "122630"]


def trading_days(session, start: date, end: date) -> list[date]:
    rows = session.scalars(
        select(TradingCalendar.cal_date).where(
            TradingCalendar.cal_date >= start, TradingCalendar.cal_date <= end,
            TradingCalendar.is_open.is_(True)
        ).order_by(TradingCalendar.cal_date)
    ).all()
    return list(rows)


def collect_code(session, client: KisClient, code: str, lookback_days: int) -> dict:
    inst = session.scalar(select(Instrument).where(Instrument.code == code))
    if inst is None:
        raise RuntimeError(f"instrument {code} not seeded — run scripts.seed first")
    last_ts = session.scalar(
        select(func.max(OhlcvIntraday.ts)).where(
            OhlcvIntraday.instrument_id == inst.id, OhlcvIntraday.timeframe == "1m")
    )
    start = last_ts.date() if last_ts else date.today() - timedelta(days=lookback_days)
    days = trading_days(session, start, date.today())
    totals = {"days": 0, "inserted": 0, "rejected": 0, "empty_days": 0}
    logger.info("%s: incremental from %s (%d trading days)", code, start, len(days))
    for d in days:
        bars = client.fetch_minutes_day(code, d)
        if not bars:
            totals["empty_days"] += 1  # KIS 보관 범위 밖 or 휴장 — 스킵
            continue
        res = upsert_minute_bars(session, inst.id, bars, source="kis")
        session.commit()  # 하루 단위 체크포인트
        totals["days"] += 1
        totals["inserted"] += res.inserted
        totals["rejected"] += res.rejected
        if totals["days"] % 20 == 0:
            logger.info("%s: %s — cumulative inserted=%d", code, d, totals["inserted"])
    return totals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", type=str, default=",".join(DEFAULT_CODES))
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    settings = get_settings()
    if not (settings.kis_app_key and settings.kis_app_secret):
        raise SystemExit("KIS keys not configured in .env")
    client = KisClient(KisAuth(settings.kis_app_key, settings.kis_app_secret, settings.kis_env))
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    with SessionLocal() as session:
        run = start_batch(session, "seed_minutes", {"codes": codes, "days": args.days})
        session.commit()
        report = {}
        try:
            for code in codes:
                report[code] = collect_code(session, client, code, args.days)
            finish_batch(session, run, "ok", {"report": report})
        except Exception as exc:
            finish_batch(session, run, "failed", {"error": str(exc)[:500], "report": report})
            raise
        finally:
            session.commit()
    logger.info("seed_minutes done: %s", report)


if __name__ == "__main__":
    main()
