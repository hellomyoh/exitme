"""시세 적재 서비스 — 검증 통과분만 멱등 INSERT (ON CONFLICT DO NOTHING).

원본(_raw)은 불변: 기존 행 UPDATE 금지 (ADR-002). 수정주가는 adj_factor 재계산으로만 반영.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import BatchRun, Instrument, OhlcvDaily
from app.services.validators import validate_bar

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    inserted: int = 0
    skipped_conflict: int = 0
    rejected: int = 0


def upsert_daily_bars(
    session: Session,
    instrument_id: int,
    bars: list[dict],
    source: str,
) -> IngestResult:
    """bars: [{trade_date, open, high, low, close, volume}] — 검증 실패 행은 거부하고 로그."""
    result = IngestResult()
    rows = []
    for b in bars:
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
