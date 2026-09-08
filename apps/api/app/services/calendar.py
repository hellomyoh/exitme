"""거래일 캘린더 갱신 (2026-09-08, ADR-009 §2-3 선결) — KIS 국내휴장일조회(CTCA0903R)로 앞으로의 개장/휴장을 `trading_calendar` 에 채운다.

배경: 캘린더는 시딩(과거 일봉이 있는 날 = 개장) 이후 갱신 경로가 없어 2026-09-01 에서 멈춰 있었다. 단일 실행(ADR-009)은 실행일을
캘린더로 계산하므로 휴장이 등록되지 않으면 그날 09:01 은 '시가 확인 실패', 다음 거래일 09:01 은 '기준일 불일치'로 발주하지 않는다.
pykrx 는 미래 거래일을 주지 않아(2026-09-08 확인) KIS TR 을 쓴다 — 하루 단위 응답(주말 포함, `opnd_yn` = 개장 여부), 페이지당 24일.

사용: 배포 후 훅 `scripts/post-deploy.d/20-trading-calendar.sh` 가 `python -m app.services.calendar --days 120` 로, 워커가 매주 일요일 06:00 에.
멱등 — 이미 같은 값이면 건드리지 않고, 값이 바뀐 날(예: 임시 휴장)은 갱신하며 기록한다.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import TradingCalendar

logger = logging.getLogger(__name__)
DEFAULT_DAYS = 120


def refresh_trading_calendar(session: Session, client, start: date, end: date) -> dict:
    """[start, end] 의 개장/휴장을 KIS 로 받아 upsert. 반환: fetched·added·updated·changed(바뀐 날)·closed_weekdays(평일 휴장)."""
    rows = client.fetch_holidays(start, end)
    added = updated = 0
    changed: list[dict] = []
    for d, is_open in rows:
        cur = session.get(TradingCalendar, d)
        if cur is None:
            session.add(TradingCalendar(cal_date=d, is_open=is_open))
            added += 1
        elif bool(cur.is_open) != bool(is_open):
            changed.append({"date": d.isoformat(), "was": bool(cur.is_open), "now": bool(is_open)})
            cur.is_open = is_open
            updated += 1
    session.commit()
    out = {"start": start.isoformat(), "end": end.isoformat(), "fetched": len(rows), "added": added, "updated": updated,
           "changed": changed, "closed_weekdays": [d.isoformat() for d, o in rows if not o and d.weekday() < 5]}
    logger.info("trading calendar refreshed: %s", out)
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI — 배포 후 훅·수동 실행용. KIS 키가 없으면 2 로 끝나 훅이 실패로 드러낸다(캘린더 없이 무인 운영은 위험)."""
    p = argparse.ArgumentParser(description="KIS 국내휴장일조회로 trading_calendar 갱신")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"오늘부터 며칠까지 (기본 {DEFAULT_DAYS})")
    p.add_argument("--start", type=date.fromisoformat, default=None, help="시작일 (기본 오늘, KST)")
    a = p.parse_args(argv)
    from datetime import datetime, timezone

    from app.config import get_settings
    from app.db import SessionLocal
    from app.services.kis_auth import KisAuth
    from app.services.kis_client import KisClient

    st = get_settings()
    if not (st.kis_app_key and st.kis_app_secret):
        print(json.dumps({"error": "KIS keys not configured (.env KIS_APP_KEY/KIS_APP_SECRET)"}, ensure_ascii=False))
        return 2
    start = a.start or datetime.now(timezone(timedelta(hours=9))).date()
    client = KisClient(KisAuth(st.kis_app_key, st.kis_app_secret, st.kis_env))
    with SessionLocal() as s:
        out = refresh_trading_calendar(s, client, start, start + timedelta(days=a.days))
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
