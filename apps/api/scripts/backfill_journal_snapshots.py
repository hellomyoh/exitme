"""매매일지 일별 스냅샷 소급 적재 (0028, 2026-09-12 지시) — 자산 추이의 일지별 선을 과거까지 잇는다.

기록(manual_journal_entries)의 일자·수량·단가·종목코드와 그날의 종가로 각 일지의 일별 평가액을 되살린다.
**근사다**(approx=True). 되살리지 못하는 것 둘:
  · 그날 실전매매 포트가 같은 계좌로 같은 종목을 들고 있었는지(중복 제외 상태) — 당시 포트 보유를 재생하지 않는다.
  · 시세가 DB 에 없는 종목 — 평가액 대신 취득원가로 둔다(현행 value 규약과 같음: 전 종목 가격이 있어야 평가액).

이미 있는 행은 건드리지 않는다(배치가 남긴 정확한 값 우선). --overwrite-approx 로 소급분만 다시 계산한다.

사용:
  docker compose exec -T api python -m scripts.backfill_journal_snapshots --dry-run
  docker compose exec -T api python -m scripts.backfill_journal_snapshots --days 120
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (AssetSnapshot, Instrument, JournalSnapshot, ManualJournal,
                        ManualJournalEntry, OhlcvDaily)


def close_series(session, codes: set[str], since: date) -> dict[str, list[tuple[date, int]]]:
    """종목코드 → [(일자, 종가)] 오름차순 (since 이전 마지막 종가 하나를 앞에 포함)."""
    out: dict[str, list[tuple[date, int]]] = {}
    for code in codes:
        inst = session.scalar(select(Instrument).where(Instrument.code == code))
        if inst is None:
            continue
        prior = session.execute(
            select(OhlcvDaily.trade_date, OhlcvDaily.close_raw)
            .where(OhlcvDaily.instrument_id == inst.id, OhlcvDaily.trade_date < since)
            .order_by(OhlcvDaily.trade_date.desc()).limit(1)).all()
        rows = session.execute(
            select(OhlcvDaily.trade_date, OhlcvDaily.close_raw)
            .where(OhlcvDaily.instrument_id == inst.id, OhlcvDaily.trade_date >= since)
            .order_by(OhlcvDaily.trade_date)).all()
        out[code] = [(d, int(c)) for d, c in list(prior) + list(rows)]
    return out


def close_at(series: list[tuple[date, int]], day: date) -> int | None:
    """day 이하 마지막 종가 (없으면 None)."""
    px = None
    for d, c in series:
        if d > day:
            break
        px = c
    return px


def run(days: int, dry_run: bool, overwrite_approx: bool) -> dict:
    stats: dict = {"journals": 0, "dates": 0, "written": 0, "skipped_existing": 0, "unpriced": 0}
    with SessionLocal() as s:
        today = max(s.scalars(select(AssetSnapshot.snap_date).order_by(AssetSnapshot.snap_date.desc())
                              .limit(1)).all() or [date.today()])
        since = today - timedelta(days=days)
        for j in s.scalars(select(ManualJournal).order_by(ManualJournal.id)).all():
            entries = s.scalars(select(ManualJournalEntry)
                                .where(ManualJournalEntry.journal_id == j.id)
                                .order_by(ManualJournalEntry.trade_date, ManualJournalEntry.id)).all()
            if not entries:
                continue
            stats["journals"] += 1
            codes = {e.code for e in entries if e.code}
            px = close_series(s, codes, since)
            have = {r.snap_date: r for r in s.scalars(select(JournalSnapshot)
                                                     .where(JournalSnapshot.journal_id == j.id)).all()}
            first_day = max(min(e.trade_date for e in entries), since)
            day = first_day
            while day <= today:
                stats["dates"] += 1
                old = have.get(day)
                if old is not None and not (overwrite_approx and old.approx):
                    stats["skipped_existing"] += 1
                    day += timedelta(days=1)
                    continue
                # 그날까지의 보유 (FIFO 수량만 — 평단은 원가 합으로 충분)
                qty: dict[str, int] = defaultdict(int)
                cost: dict[str, int] = defaultdict(int)
                for e in entries:
                    if e.trade_date > day or not e.code:
                        continue
                    if e.side == "buy":
                        qty[e.code] += e.qty
                        cost[e.code] += e.qty * e.price
                    else:
                        held = qty[e.code]
                        avg = (cost[e.code] / held) if held else 0
                        take = min(held, e.qty)
                        qty[e.code] -= take
                        cost[e.code] -= round(avg * take)
                live = {c: q for c, q in qty.items() if q > 0}
                total_cost = sum(cost[c] for c in live)
                prices = {c: close_at(px.get(c, []), day) for c in live}
                if live and all(p for p in prices.values()):
                    value = sum(live[c] * prices[c] for c in live)
                else:
                    value = total_cost           # 전 종목 가격이 없으면 원가 (현행 value 규약)
                    if live:
                        stats["unpriced"] += 1
                if live:
                    stats["written"] += 1
                    if not dry_run:
                        if old is not None:
                            old.value, old.cost, old.approx = int(value), int(total_cost), True
                        else:
                            s.add(JournalSnapshot(journal_id=j.id, snap_date=day, value=int(value),
                                                  cost=int(total_cost), counted=True, approx=True))
                day += timedelta(days=1)
        if not dry_run:
            s.commit()
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="매매일지 일별 스냅샷 소급 적재 (근사)")
    ap.add_argument("--days", type=int, default=400, help="최신 스냅샷일 기준 며칠까지 거슬러 올라갈지")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 건수만")
    ap.add_argument("--overwrite-approx", action="store_true", help="이미 있는 소급분(approx)도 다시 계산")
    a = ap.parse_args()
    out = run(a.days, a.dry_run, a.overwrite_approx)
    print(("[dry-run] " if a.dry_run else "") + " · ".join(f"{k}={v}" for k, v in out.items()))
