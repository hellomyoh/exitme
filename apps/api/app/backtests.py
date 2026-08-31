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
    "QQQ_QLD": ("QQQ", "QLD"),      # 미국 — 센트 단위 (2026-08-31)
    "QQQ_TQQQ": ("QQQ", "TQQQ"),
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
    etf: str = Field(default="KODEX", pattern="^(KODEX|TIGER|QQQ_QLD|QQQ_TQQQ)$")
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


US_ETFS = {"QQQ_QLD", "QQQ_TQQQ"}


def market_of_etf(etf: str) -> str:
    return "US" if etf in US_ETFS else "KR"


def base_costs_for(etf: str) -> dict:
    """마켓별 기본 파라미터 — 미국은 센트 호가·미국 비용 모델·배율 (docs/us-backtest-20260831.md)."""
    if etf not in US_ETFS:
        return {}
    return {
        "tick": 1, "commission": 0.001, "slippage_market": 0.001, "lev_tax": 0.0,
        "fee_200": 0.002, "fee_lev": 0.0084 if etf == "QQQ_TQQQ" else 0.0095,
        "lev_multiple": 3.0 if etf == "QQQ_TQQQ" else 2.0,
    }


def params_from_job(p: dict):
    """잡 파라미터 dict → Params — 마켓 기본 + 사용자 알고리즘 스냅샷 + 비용 오버라이드 병합."""
    from app.strategy.params import AblationFlags, Params

    merged = {**base_costs_for(p.get("etf", "KODEX")), **p.get("algo", {}), **p.get("costs", {})}
    return Params(**merged, flags=AblationFlags(**p.get("flags", {})))


def user_algo_overrides(session: Session, user_id: int) -> dict:
    """사용자 알고리즘 오버라이드 — 검증된 키만 (app.settings.PARAM_REGISTRY)."""
    from app.models import UserSettings

    row = session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    return dict(row.algo_params) if row else {}


WARMUP_CAL_DAYS = 460  # min_history 270 거래일 ≈ 397 캘린더일 + 휴장 여유


def load_bars_with_warmup(session: Session, date_from: date, date_to: date,
                          codes: tuple[str, str] = ETF_PAIRS["KODEX"]):
    """지표 워밍업용 선행 봉을 시작일 이전에서 함께 로드 (2026-08-28 결함 수정).

    구간만 로드하면 1년 이하 백테스트가 전부 워밍업(270거래일)에 잠식되어 거래 0 이 된다.
    반환: (bars_200, bars_lev, fp, start_idx) — start_idx = 요청 시작일 이상 첫 봉.
    거래·자산곡선은 start_idx 부터 시작한다 (run_backtest 의 start_index).
    선행 데이터가 부족하면(start_idx < 270) 종전처럼 구간 앞부분이 워밍업으로 쓰인다.
    """
    from datetime import timedelta

    bars_200, bars_lev, fp = load_aligned_bars(
        session, date_from - timedelta(days=WARMUP_CAL_DAYS), date_to, codes)
    iso = date_from.isoformat()
    start_idx = next((i for i, b in enumerate(bars_200) if b["date"] >= iso), 0)
    return bars_200, bars_lev, fp, start_idx


def params_signature(p: dict) -> str:
    """잡의 실효 전략 파라미터 서명 — 기본값 변경도 결과를 바꾸므로 지문에 포함 (2026-09-01 결함 수정).

    이것이 없으면 파라미터 개정 후에도 '동일 조건 재사용'이 개정 전 결과를 돌려주고,
    기존 기록이 stale 로 표시되지 않는다.
    """
    import hashlib
    from dataclasses import asdict

    d = asdict(params_from_job(p))
    return hashlib.md5(repr(sorted(d.items())).encode()).hexdigest()[:12]


def current_fingerprint(session: Session, params: dict) -> str:
    _, _, fp, _ = load_bars_with_warmup(session, date.fromisoformat(params["date_from"]),
                                        date.fromisoformat(params["date_to"]),
                                        codes=pair_from_params(params))
    return f"{fp}|{params_signature(params)}"


@router.post("/backtests", status_code=202)
def create_backtest(body: BacktestIn, user_id: int = Depends(current_user_id),
                    session: Session = Depends(get_session)) -> dict:
    if body.date_from >= body.date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    job_params = json.loads(body.model_dump_json())
    algo = user_algo_overrides(session, user_id)
    if algo:
        job_params["algo"] = algo  # 실행 시점 설정 스냅샷 — 이후 설정 변경과 무관하게 재현 (2026-08-31)
    # 동일 조건 + 동일 데이터면 결과가 결정론적으로 같음 — 중복 기록 대신 기존 잡 재사용 (2026-08-31 검토).
    # 시세가 갱신됐으면(지문 상이) 새로 실행한다.
    dup = session.scalars(
        select(Backtest).where(Backtest.user_id == user_id, Backtest.status == "DONE",
                               Backtest.params == job_params)
        .order_by(Backtest.id.desc()).limit(1)
    ).first()
    if dup is not None:
        try:
            if dup.data_fingerprint == current_fingerprint(session, job_params):
                return {"id": dup.id, "status": "DONE", "reused": True}
        except HTTPException:
            pass
    bt = Backtest(user_id=user_id, params=job_params, status="QUEUED")
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


@router.get("/backtests/{bt_id}/journal")
def get_backtest_journal(bt_id: int, user_id: int = Depends(current_user_id),
                         session: Session = Depends(get_session)) -> dict:
    """일자별 매매 저널 — 장 시작 전 주문표(계획) + 체결 + 수익률·보유량.

    저장 파라미터로 결정론 재계산한다(수 백 ms). 시세가 갱신됐으면 stale=true.
    plans[i] 는 dates[i] 종가 기준 계획 = 다음 거래일(dates[i+1]) 장 시작 전 주문표.
    """
    from app.strategy.backtest import run_backtest
    from app.strategy.params import AblationFlags, Params

    bt = _get_owned(session, bt_id, user_id)
    if bt.status != "DONE":
        raise HTTPException(status_code=409, detail=f"journal available only for DONE jobs (status={bt.status})")
    p = bt.params
    bars_200, bars_lev, fp, start_idx = load_bars_with_warmup(
        session, date.fromisoformat(p["date_from"]), date.fromisoformat(p["date_to"]),
        codes=pair_from_params(p),
    )
    params = params_from_job(p)
    r = run_backtest(bars_200, bars_lev, float(p["capital"]), params,
                     start_index=start_idx, collect_plans=True)

    fills_by_date: dict[str, list] = {}
    for f in r.fills:
        fills_by_date.setdefault(f.date, []).append(
            {"instrument": f.instrument, "side": f.side, "kind": f.kind, "price": f.price, "qty": f.qty})

    capital = float(p["capital"])
    items = []
    for i, d in enumerate(r.dates):
        plan_i = r.plans[i] if i < len(r.plans) else None  # dates[i] 체결일의 계획 = plans[i-?]... 아래 주석 참조
        # r.dates[i] = bars[i+1] 체결일이고 r.plans[i] 는 bars[i] 종가 계획 → 인덱스 일치
        planned = [
            {"instrument": o.instrument, "side": o.side, "otype": o.otype,
             "kind": o.kind, "price": o.price, "qty": o.qty}
            for o in (plan_i.orders if plan_i and plan_i.status == "OK" else [])
        ]
        prev_eq = r.equity[i - 1] if i > 0 else capital
        eq_r, prev_r = round(r.equity[i]), round(prev_eq)
        items.append({
            "date": d,
            "regime": r.regimes[i],
            "exposure": r.exposures[i],
            "equity": eq_r,
            "day_return": (r.equity[i] / prev_eq - 1.0) if prev_eq else 0.0,
            "day_pnl": eq_r - prev_r,  # 표시 equity 증분과 일치 (검증 B10)
            "total_return": r.equity[i] / capital - 1.0,
            "cash": round(r.cash_curve[i]),
            "qty_200": r.qty_200[i],
            "qty_lev": r.qty_lev[i],
            "planned": planned,
            "fills": fills_by_date.get(d, []),
        })
    stale = f"{fp}|{params_signature(p)}" != bt.data_fingerprint
    return {"id": bt_id, "stale": stale, "items": items}


@router.delete("/backtests/{bt_id}")
def delete_backtest(bt_id: int, user_id: int = Depends(current_user_id),
                    session: Session = Depends(get_session)) -> dict:
    """백테스트 기록 삭제 (2026-08-28 지시) — 자산곡선 연쇄 삭제, 전환된 실전 포트는 링크만 해제."""
    from app.models import TradePortfolio

    bt = _get_owned(session, bt_id, user_id)
    if bt.status == "RUNNING":
        raise HTTPException(status_code=409, detail="running job — cancel it first")
    session.query(TradePortfolio).filter(TradePortfolio.backtest_id == bt_id).update({"backtest_id": None})
    session.query(BacktestEquity).filter(BacktestEquity.backtest_id == bt_id).delete()
    session.delete(bt)
    session.commit()
    return {"deleted": bt_id}


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
