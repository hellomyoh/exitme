"""시세 적재 서비스 — 검증 통과분만 멱등 INSERT (ON CONFLICT DO NOTHING).

원본(_raw)은 불변: 기존 행 UPDATE 금지 (ADR-002). 수정주가는 adj_factor 재계산으로만 반영.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time as _time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import BatchRun, Instrument, OhlcvDaily, OhlcvIntraday
from app.services.validators import validate_bar

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    inserted: int = 0
    skipped_conflict: int = 0
    rejected: int = 0


# ── 확정봉 가드 (2026-09-07 사고 대응) ────────────────────────────────────────────
# KIS 일봉 API 는 장 시작 전·장중에도 '오늘 날짜' 행을 돌려준다 — 거래량 0~수십의 미완성 봉이다.
# 이것이 적재되면 ON CONFLICT DO NOTHING(원본 불변, ADR-002) 때문에 마감 후 진짜 종가가
# 들어오지 못하고 영구 잔존해, 주문표 기준 종가·MA·ATR·σ·백테스트를 계속 오염시킨다.
# (2026-08-31 미국 ETF 사고 → NOTES, 2026-09-07 국내 재발: 08:40·09:26 KST 적재분이
#  102110 거래량 0 으로 09/08 주문표 5개 값 전부를 만들어냈다)
# 네 적재 경로(daily_ingest·mjournal 보충·seed·ingest_us_daily)가 모두 이 함수를 지나므로
# 여기 한 곳에서 막는다.
MARKET_CLOSE = {          # 시장 → (타임존, 정규장 마감 시각)
    "NASDAQ": ("America/New_York", _time(16, 0)),
    "NYSE": ("America/New_York", _time(16, 0)),
}
KR_CLOSE = ("Asia/Seoul", _time(15, 30))
# KRX 애프터마켓 (2026-09-14~, 16:00~20:00 실시간 접속매매): **주식·DR 만** 대상이고 ETF·ETN 은 제외된다.
# 주식의 그날 봉은 20:00 이 지나야 확정이라, 16:05 에 받은 값을 넣으면 ON CONFLICT DO NOTHING 때문에
# 저녁 거래가 반영되지 못한 채 굳는다. 그래서 종목 종류로 마감 시각을 나눈다 (KR ETF 15:30 / KR 주식 20:00).
KR_STOCK_CLOSE = ("Asia/Seoul", _time(20, 0))
AFTER_MARKET_TYPES = ("STOCK",)   # 애프터마켓 대상 — ETF·ETN 은 제외(정규장 마감이 그날의 끝)


def market_session_state(market: str, type_: str | None = None) -> tuple[date, bool]:
    """(그 시장 기준 오늘 날짜, 그날 거래가 끝났는지). 미지정 시장은 국내로 본다.

    국내 주식은 애프터마켓(~20:00)까지가 그날의 거래다. type_ 를 주지 않으면 정규장(15:30) 기준 —
    주문표 판정처럼 정규장만 보는 곳은 그대로 쓴다.
    """
    key = (market or "").upper()
    if key in MARKET_CLOSE:
        tz_name, close_at = MARKET_CLOSE[key]
    elif (type_ or "").upper() in AFTER_MARKET_TYPES:
        tz_name, close_at = KR_STOCK_CLOSE
    else:
        tz_name, close_at = KR_CLOSE
    now = datetime.now(ZoneInfo(tz_name))
    return now.date(), now.time() >= close_at


def bar_is_final(trade_date: date, market: str, type_: str | None = None) -> bool:
    """그 봉이 '확정된 과거 봉'인가 — 미래 날짜이거나, 오늘인데 아직 거래 중이면 False."""
    today, closed = market_session_state(market, type_)
    if trade_date > today:
        return False
    if trade_date == today and not closed:
        return False
    return True


def upsert_daily_bars(
    session: Session,
    instrument_id: int,
    bars: list[dict],
    source: str,
) -> IngestResult:
    """bars: [{trade_date, open, high, low, close, volume}] — 검증 실패 행은 거부하고 로그."""
    result = IngestResult()
    inst = session.get(Instrument, instrument_id)
    market = inst.market if inst is not None else "KOSPI"
    type_ = inst.type if inst is not None else None   # 국내 주식은 애프터마켓 종료(20:00) 후에야 확정 (2026-09-14)
    rows = []
    for b in bars:
        # 확정봉 가드 — 장중/장 시작 전에 받은 '오늘 봉'은 저장하지 않는다 (2026-09-07)
        if not bar_is_final(b["trade_date"], market, type_):
            result.rejected += 1
            logger.warning("reject unfinished bar instrument=%s(%s) date=%s volume=%s — 장 마감 전 수신",
                           instrument_id, market, b["trade_date"], b.get("volume"))
            continue
        errors = validate_bar(b["open"], b["high"], b["low"], b["close"], b["volume"])
        if errors:
            result.rejected += 1
            logger.warning(
                "reject bar instrument=%s date=%s errors=%s",
                instrument_id, b["trade_date"], [(e.field, e.reason) for e in errors],
            )
            continue
        rows.append(
            dict(
                instrument_id=instrument_id,
                trade_date=b["trade_date"],
                open_raw=b["open"],
                high_raw=b["high"],
                low_raw=b["low"],
                close_raw=b["close"],
                volume=b["volume"],
                adj_factor=1,
                source=source,
            )
        )
    if rows:
        # RETURNING 으로 실제 삽입 건수 계산 — 하이퍼테이블은 rowcount 를 신뢰할 수 없음(-1 반환, NOTES.md)
        stmt = (
            insert(OhlcvDaily)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["instrument_id", "trade_date"])
            .returning(OhlcvDaily.trade_date)
        )
        returned = session.execute(stmt).all()
        result.inserted = len(returned)
        result.skipped_conflict = len(rows) - result.inserted
    return result


def upsert_minute_bars(
    session: Session,
    instrument_id: int,
    bars: list,
    source: str,
    timeframe: str = "1m",
) -> IngestResult:
    """분봉 멱등 적재 — bars: MinuteBar 또는 동형 dict. 검증 통과분만."""
    result = IngestResult()
    rows = []
    for b in bars:
        o, h, l, c, v = b.open, b.high, b.low, b.close, b.volume
        errors = validate_bar(o, h, l, c, v)
        if errors:
            result.rejected += 1
            continue
        rows.append(dict(
            instrument_id=instrument_id, timeframe=timeframe, ts=b.ts,
            open_raw=o, high_raw=h, low_raw=l, close_raw=c, volume=v, source=source,
        ))
    if rows:
        stmt = (
            insert(OhlcvIntraday)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["instrument_id", "timeframe", "ts"])
            .returning(OhlcvIntraday.ts)
        )
        returned = session.execute(stmt).all()
        result.inserted = len(returned)
        result.skipped_conflict = len(rows) - result.inserted
    return result


def get_or_create_instrument(session: Session, code: str, name: str, market: str, type_: str = "ETF") -> Instrument:
    inst = session.scalar(select(Instrument).where(Instrument.code == code))
    if inst is None:
        inst = Instrument(code=code, name=name, market=market, type=type_)
        session.add(inst)
        session.flush()
    return inst


def start_batch(session: Session, kind: str, detail: dict | None = None) -> BatchRun:
    run = BatchRun(kind=kind, started_at=datetime.now(timezone.utc), status="running", detail=detail or {})
    session.add(run)
    session.flush()
    return run


def finish_batch(session: Session, run: BatchRun, status: str, detail: dict | None = None) -> None:
    run.finished_at = datetime.now(timezone.utc)
    run.status = status
    if detail:
        run.detail = {**run.detail, **detail}


# ── 미국 ETF 일봉 (2026-08-31 지시 — QQQ/QLD/TQQQ 를 DB 에 저장)
# 가격 컬럼이 정수라 미국 종목은 **센트 단위 정수**로 저장한다 (달러 ×100, 호가 1센트 = tick 1).
US_TICKERS: dict[str, tuple[str, str]] = {
    "QQQ": ("Invesco QQQ (나스닥100 1x)", "NAS"),
    "QLD": ("ProShares Ultra QQQ (2x)", "AMS"),
    "TQQQ": ("ProShares UltraPro QQQ (3x)", "NAS"),
}
US_DAILY_TR = "HHDFS76240000"  # 해외주식 기간별시세 — 100행/호출, BYMD 역방향 페이지네이션


def ingest_us_daily(session: Session, client, code: str) -> IngestResult:
    """미국 ETF 일봉 증분 수집 — 저장된 마지막 날짜 이후만 KIS 에서 받아 upsert.

    최초 실행이면 KIS 보존분 전체(약 19년)를 페이지네이션으로 수집한다.
    수정주가(MODP=1) 기준이며 센트 정수로 변환해 저장한다.
    """
    import time as _time

    from sqlalchemy import func as _func

    from app.models import OhlcvDaily

    name, excd = US_TICKERS[code]
    inst = get_or_create_instrument(session, code, name, "NASDAQ", "ETF")
    last: date | None = session.scalar(
        select(_func.max(OhlcvDaily.trade_date)).where(OhlcvDaily.instrument_id == inst.id))
    last_ymd = last.strftime("%Y%m%d") if last else None

    bars: list[dict] = []
    bymd, seen = "", set()
    while True:
        data = client._get("/uapi/overseas-price/v1/quotations/dailyprice", US_DAILY_TR, {
            "AUTH": "", "EXCD": excd, "SYMB": code, "GUBN": "0", "BYMD": bymd, "MODP": "1"})
        rows = [r for r in (data.get("output2") or []) if r.get("xymd") and r["xymd"] not in seen]
        if not rows:
            break
        stop = False
        for r in rows:
            seen.add(r["xymd"])
            if last_ymd and r["xymd"] <= last_ymd:
                stop = True
                continue  # 이미 저장된 구간 — 증분만
            d = r["xymd"]
            bars.append({
                "trade_date": date(int(d[:4]), int(d[4:6]), int(d[6:])),
                "open": round(float(r["open"]) * 100), "high": round(float(r["high"]) * 100),
                "low": round(float(r["low"]) * 100), "close": round(float(r["clos"]) * 100),
                "volume": int(r.get("tvol") or 0),
            })
        if stop:
            break
        bymd = f'{int(min(r["xymd"] for r in rows)) - 1:08d}'
        _time.sleep(0.2)
    bars.sort(key=lambda b: b["trade_date"])
    return upsert_daily_bars(session, inst.id, bars, source="kis")
