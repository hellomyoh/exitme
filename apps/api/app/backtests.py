"""백테스트 잡 API — 202 + WS 진행률 + 취소, 결과 단일 트랜잭션 저장 (feature-backtest §8)."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.config import get_settings
from app.db import get_session
from app.models import Backtest, BacktestEquity, Instrument, OhlcvDaily

router = APIRouter()

PROGRESS_CH = "backtests:progress:{id}"
PROGRESS_KEY = "backtests:progress-snap:{id}"
CANCEL_KEY = "backtests:cancel:{id}"

# ETF 선택 (2026-08-28 채팅 지시): 주력 200 ETF 를 KODEX/TIGER 중 선택.
# 레버리지는 유동성이 큰 KODEX 레버리지(122630) 공통 사용 (ASSUMPTIONS 기록).
ETF_PAIRS = {
    "KODEX": ("069500", "122630"),
    "TIGER": ("102110", "122630"),
}
CODE_200, CODE_LEV = ETF_PAIRS["KODEX"]  # 기본값 (전략 정본 기준)


class Costs(BaseModel):
    commission: float = 0.00015
    slippage_market: float = 0.001
    lev_tax: float = 0.154
    fee_200: float = 0.0015
    fee_lev: float = 0.0064


class Flags(BaseModel):
    f1_no_tp_in_bull: bool = True
    f2_downside_vol: bool = True
    f3_fast_regime: bool = True
    f4_leverage: bool = True
    f5_gap_filter: bool = True


class BacktestIn(BaseModel):
    capital: int = Field(gt=1_000_000, le=100_000_000_000)
    date_from: date
    date_to: date
    etf: str = Field(default="KODEX", pattern="^(KODEX|TIGER)$")
    costs: Costs = Costs()
    flags: Flags = Flags()


def load_aligned_bars(session: Session, date_from: date, date_to: date,
                      codes: tuple[str, str] = ETF_PAIRS["KODEX"]):
    """(200 ETF, 레버리지) 일봉을 날짜 교집합으로 정렬 로드 + data_fingerprint 계산."""
    code_200, code_lev = codes
    out: dict[str, dict[str, dict]] = {}
    fingerprints = []
    for code in (code_200, code_lev):
        inst = session.scalar(select(Instrument).where(Instrument.code == code))
        if inst is None:
            raise HTTPException(status_code=409, detail=f"instrument {code} not seeded")
        rows = session.execute(
            select(OhlcvDaily)
            .where(OhlcvDaily.instrument_id == inst.id,
                   OhlcvDaily.trade_date >= date_from, OhlcvDaily.trade_date <= date_to)
            .order_by(OhlcvDaily.trade_date)
        ).scalars().all()
        max_ing = session.scalar(
            select(func.max(OhlcvDaily.ingested_at)).where(OhlcvDaily.instrument_id == inst.id)
        )
        fingerprints.append(f"{code}:{len(rows)}:{max_ing}")
        out[code] = {
            r.trade_date.isoformat(): {
                "date": r.trade_date.isoformat(),
                "open": r.open_raw * float(r.adj_factor), "high": r.high_raw * float(r.adj_factor),
                "low": r.low_raw * float(r.adj_factor), "close": r.close_raw * float(r.adj_factor),
                "volume": r.volume,
            } for r in rows
        }
    common = sorted(set(out[code_200]) & set(out[code_lev]))
    if len(common) < 30:
        raise HTTPException(status_code=409, detail=f"not enough aligned bars ({len(common)}) — run seeding first")
    fp = hashlib.md5(("|".join(fingerprints) + f"|{date_from}|{date_to}").encode()).hexdigest()
    return [out[code_200][d] for d in common], [out[code_lev][d] for d in common], fp


def pair_from_params(params: dict) -> tuple[str, str]:
    return ETF_PAIRS.get(params.get("etf", "KODEX"), ETF_PAIRS["KODEX"])


def current_fingerprint(session: Session, params: dict) -> str:
    _, _, fp = load_aligned_bars(session, date.fromisoformat(params["date_from"]),
                                 date.fromisoformat(params["date_to"]),
                                 codes=pair_from_params(params))
    return fp


@router.post("/backtests", status_code=202)
def create_backtest(body: BacktestIn, user_id: int = Depends(current_user_id),
                    session: Session = Depends(get_session)) -> dict:
    if body.date_from >= body.date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    bt = Backtest(user_id=user_id, params=json.loads(body.model_dump_json()), status="QUEUED")
    session.add(bt)
    from app.dashboard import record_event
    record_event(session, "backtest_run", user_id)
    session.commit()
    from app.worker import run_backtest_job

    run_backtest_job.apply_async(args=[bt.id], queue="backtest")
    return {"id": bt.id, "status": "QUEUED"}


@router.get("/backtests")
def list_backtests(cursor: int | None = None, limit: int = 20,
                   user_id: int = Depends(current_user_id),
                   session: Session = Depends(get_session)) -> dict:
    q = select(Backtest).where(Backtest.user_id == user_id).order_by(Backtest.id.desc()).limit(min(limit, 50))
    if cursor:
        q = q.where(Backtest.id < cursor)
    rows = session.scalars(q).all()
    return {
        "items": [
            {"id": r.id, "status": r.status, "progress": r.progress, "params": r.params,
             "kpi": r.kpi, "created_at": r.created_at.isoformat()} for r in rows
        ],
        "next_cursor": rows[-1].id if rows else None,
    }


def _get_owned(session: Session, bt_id: int, user_id: int) -> Backtest:
    bt = session.get(Backtest, bt_id)
    if bt is None or bt.user_id != user_id:
        raise HTTPException(status_code=404, detail="backtest not found")
    return bt


@router.get("/backtests/{bt_id}")
def get_backtest(bt_id: int, user_id: int = Depends(current_user_id),
                 session: Session = Depends(get_session)) -> dict:
    bt = _get_owned(session, bt_id, user_id)
    body: dict = {"id": bt.id, "status": bt.status, "progress": bt.progress,
                  "params": bt.params, "kpi": bt.kpi, "error": bt.error,
                  "data_fingerprint": bt.data_fingerprint, "stale": False}
    if bt.status == "DONE":
        try:
            body["stale"] = current_fingerprint(session, bt.params) != bt.data_fingerprint
        except HTTPException:
            body["stale"] = True
        rows = session.execute(
            select(BacktestEquity).where(BacktestEquity.backtest_id == bt.id)
            .order_by(BacktestEquity.trade_date)
        ).scalars().all()
        body["equity"] = [
            {"date": r.trade_date.isoformat(), "equity": float(r.equity),
             "benchmark": float(r.benchmark), "regime": r.regime, "exposure": float(r.exposure)}
            for r in rows
        ]
        body["trades"] = bt.trades or []
    return body


@router.post("/backtests/{bt_id}/cancel")
def cancel_backtest(bt_id: int, user_id: int = Depends(current_user_id),
                    session: Session = Depends(get_session)) -> dict:
    bt = _get_owned(session, bt_id, user_id)
    if bt.status not in ("QUEUED", "RUNNING"):
        raise HTTPException(status_code=409, detail=f"cannot cancel job in status {bt.status}")
    import redis as sync_redis

    r = sync_redis.from_url(get_settings().redis_url)
    r.set(CANCEL_KEY.format(id=bt_id), "1", ex=3600)
    return {"id": bt_id, "cancel_requested": True}


@router.websocket("/ws/backtests/{bt_id}")
async def ws_backtest_progress(ws: WebSocket, bt_id: int) -> None:
    """진행률 스트림 — 스냅샷 즉시 송신 후 채널 릴레이 (Pub/Sub 유실 대비, §8)."""
    await ws.accept()
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(PROGRESS_CH.format(id=bt_id))
    try:
        snap = await r.get(PROGRESS_KEY.format(id=bt_id))
        if snap:
            await ws.send_text(snap)
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            await ws.send_text(msg["data"])
            if json.loads(msg["data"]).get("status") in ("DONE", "FAILED", "CANCELED"):
                break
        await ws.close()
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(PROGRESS_CH.format(id=bt_id))
        await pubsub.aclose()
        await r.aclose()
    _ = asyncio  # (명시적 import 유지)
