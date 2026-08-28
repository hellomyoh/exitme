"""초기 시세 시딩 — KIS 1순위, pykrx 폴백 (ADR-004).

사용: docker compose run --rm api python -m scripts.seed --years 10 [--codes 069500,122630]

- .env 에 KIS_APP_KEY/KIS_APP_SECRET 가 있으면 KIS 일봉 API(FHKST03010100)로 시딩한다
  (10년 ≈ 코드당 26회 호출). 키가 없으면 pykrx 를 시도한다.
  주의: KRX 데이터포털이 2026-08 현재 pykrx 요청을 차단(LOGOUT)하므로 실질적으로 KIS 키가 필요하다 (NOTES.md).
- (종목, 연도) 체크포인트로 이어받기, ON CONFLICT DO NOTHING 멱등.
- 빈 응답은 실패로 처리한다 — 체크포인트를 남기지 않고 중단 (조용한 0건 시딩 금지).
- 거래일 캘린더는 KODEX 200(069500) 일봉 존재일을 프록시로 적재.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import SeedCheckpoint, TradingCalendar
from app.services import pykrx_client
from app.services.ingest import finish_batch, get_or_create_instrument, start_batch, upsert_daily_bars
from app.services.kis_auth import KisAuth
from app.services.kis_client import KisClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("seed")

DEFAULT_CODES = {
    "069500": ("KODEX 200", "KOSPI"),
    "122630": ("KODEX 레버리지", "KOSPI"),
}
CALENDAR_PROXY = "069500"


class SeedError(RuntimeError):
    pass


def build_provider() -> tuple[str, "callable"]:
    """(source_name, fetch(code, start, end) -> list[dict]) — KIS 우선, 없으면 pykrx."""
    settings = get_settings()
    if settings.kis_app_key and settings.kis_app_secret:
        client = KisClient(KisAuth(settings.kis_app_key, settings.kis_app_secret, settings.kis_env))

        def fetch_kis(code: str, start: date, end: date) -> list[dict]:
            return [b.__dict__ for b in client.fetch_daily(code, start, end)]

        return "kis", fetch_kis
    logger.warning("KIS keys not configured (.env) — falling back to pykrx (may be blocked by KRX)")
    return "pykrx", pykrx_client.fetch_daily


def seed_calendar(session, fetch, start: date, end: date) -> int:
    proxy_bars = fetch(CALENDAR_PROXY, start, end)
    trading_days = {b["trade_date"] for b in proxy_bars}
    if not trading_days:
        raise SeedError("calendar proxy returned 0 bars — aborting (source blocked or misconfigured)")
    count = 0
    d = start
    while d <= end:
        if session.get(TradingCalendar, d) is None:
            session.add(TradingCalendar(cal_date=d, is_open=d in trading_days))
            count += 1
        d += timedelta(days=1)
    return count


def seed_code(session, fetch, source: str, code: str, name: str, market: str,
              start_year: int, end_year: int) -> dict:
    inst = get_or_create_instrument(session, code, name, market)
    totals = {"inserted": 0, "rejected": 0, "skipped_years": 0}
    for year in range(start_year, end_year + 1):
        done = session.scalar(
            select(SeedCheckpoint).where(SeedCheckpoint.code == code, SeedCheckpoint.year == year)
        )
        if done:
            totals["skipped_years"] += 1
            continue
        bars = fetch(code, date(year, 1, 1), min(date(year, 12, 31), date.today()))
        if not bars and year < date.today().year:
            # 과거 연도가 0건이면 소스 장애 — 체크포인트 없이 즉시 실패 (조용한 0건 금지)
            raise SeedError(f"{code} {year}: source returned 0 bars — aborting")
        res = upsert_daily_bars(session, inst.id, bars, source=source)
        session.add(SeedCheckpoint(code=code, year=year, row_count=res.inserted))
        session.commit()  # 연 단위 체크포인트 확정
        totals["inserted"] += res.inserted
        totals["rejected"] += res.rejected
        logger.info("%s %d: inserted=%d rejected=%d", code, year, res.inserted, res.rejected)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--codes", type=str, default=",".join(DEFAULT_CODES))
    args = parser.parse_args()

    end = date.today()
    start_year = end.year - args.years + 1
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    source, fetch = build_provider()

    with SessionLocal() as session:
        run = start_batch(session, "seed", {"years": args.years, "codes": codes, "source": source})
        session.commit()
        report: dict = {}
        try:
            cal_rows = seed_calendar(session, fetch, date(start_year, 1, 1), end)
            session.commit()
            logger.info("calendar rows added: %d", cal_rows)
            for code in codes:
                name, market = DEFAULT_CODES.get(code) or (pykrx_client.fetch_etf_name(code), "KOSPI")
                report[code] = seed_code(session, fetch, source, code, name, market, start_year, end.year)
            total_inserted = sum(r["inserted"] for r in report.values())
            skipped = sum(r["skipped_years"] for r in report.values())
            if total_inserted == 0 and skipped == 0:
                raise SeedError("seed inserted 0 rows overall")
            finish_batch(session, run, "ok", {"report": report})
        except Exception as exc:
            finish_batch(session, run, "failed", {"error": str(exc), "report": report})
            raise
        finally:
            session.commit()
    logger.info("seed done: %s", report)


if __name__ == "__main__":
    main()
