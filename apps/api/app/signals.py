"""일일 시그널 서비스 + API — 전략 코드 단일 소스 (ADR-005).

시그널은 전체 이력을 백테스트 엔진으로 재계산한 마지막 계획(plan)이다 —
같은 코드 경로이므로 "백테스트 d일 절단 = 시그널 d일 주문표" 동일성이 구조적으로 성립한다.
모델 포트폴리오: 기본 자본(1억)으로 시딩 시작일부터 전략을 따라온 가상 포트.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import OrderSheetRow, SignalSnapshot, TradePortfolio
from app.strategy.backtest import run_backtest
from app.strategy.params import Params

router = APIRouter()

MODEL_CAPITAL = 100_000_000  # 모델 포트 기본 자본 (표시용 — 수량 산출 기준)


KST = timezone(timedelta(hours=9))
FREEZE_TIME = time(9, 0)   # 실행일 이 시각의 원장 상태로 주문표를 동결한다 (ADR-009) — 09:01 실행기가 같은 기준으로 계산·발주


def freeze_at(exec_day: date) -> datetime:
    """실행일 주문표의 동결 시각 (KST 09:00). 이 시각 전 등록(입출금·체결)은 즉시 반영, 이후 등록은 다음 주문표부터."""
    return datetime.combine(exec_day, FREEZE_TIME, tzinfo=KST)


def _next_exec_day(base_day: date, session: Session | None = None) -> date:
    """신호 기준일(base_day 종가)의 실행일 — 다음 거래일. 주말 스킵, 캘린더에 휴장으로 등록된 날도 스킵(행이 없으면 개장으로 간주)."""
    from datetime import timedelta as _td

    from app.models import TradingCalendar

    exec_day = base_day + _td(days=1)
    for _ in range(30):
        if exec_day.weekday() < 5:
            cal = session.get(TradingCalendar, exec_day) if session is not None else None
            if cal is None or cal.is_open:
                return exec_day
        exec_day += _td(days=1)
    return exec_day


def _plan_pending(exec_day: date) -> tuple[bool, str | None]:
    """다음 거래일 주문표가 아직 안 만들어진 상태인가 (2026-09-07 지시).

    주문표의 기준일은 DB 의 마지막 일봉이라, 장 마감 후 일봉 수집(16:05) 전에는
    exec_day 가 '이미 지나간 오늘'로 계산된다. 그 구간을 화면이 말하지 않으면
    사용자는 낡은 주문표를 오늘 것으로 오해한다.
    실행일이 미래면 정상, 오늘인데 아직 장중이면 오늘 실행분이라 정상.
    """
    from app.services.ingest import market_session_state

    today, closed = market_session_state("KOSPI")
    if exec_day > today or (exec_day == today and not closed):
        return False, None
    return True, ("아직 다음 거래일 주문표가 작성되지 않았습니다 — 장 마감 후 "
                  "16:05 시세 수집이 끝나면 다음 거래일 주문표가 표시됩니다. "
                  f"아래는 {exec_day.isoformat()} 실행 기준의 이전 주문표입니다.")


def _state_before(session: Session, pid: int, cutoff: date | datetime) -> tuple[list[dict], int]:
    """cutoff 이전에 체결된 거래만으로 로트·현금을 재구성 — B안 (2026-09-02) → 시각 동결 (ADR-009, 2026-09-08).

    주문표 = 동결 시각 직전 상태의 함수(정본 §8 "종가 신호 → 익일 발주"). cutoff 가 날짜면 그날 00:00 KST(미국 포트·종전 규칙),
    시각이면 그 시각(국내 포트 = 실행일 09:00 — 09:01 실행기가 같은 기준으로 계산·발주하므로 그 전 등록한 입출금·체결은 당일 주문표에
    바로 반영되고, 이후 등록은 다음 주문표부터). FIFO 의미론은 등록 경로(portfolios — opened_at ≤ 매도 시각 필터 포함)와 동일하며,
    동등성은 테스트(cutoff=미래 ↔ 현재 로트 테이블 일치)로 고정한다.
    반환: ([{instrument_id, qty, price}] 체결 시각순, 현금).
    """
    from app.models import TradeTransaction

    if isinstance(cutoff, datetime):
        cutoff_dt = cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=KST)
    else:
        cutoff_dt = datetime.combine(cutoff, time(0, 0), tzinfo=KST)
    txs = session.scalars(
        select(TradeTransaction).where(TradeTransaction.portfolio_id == pid)
        .order_by(TradeTransaction.executed_at, TradeTransaction.id)
    ).all()

    def kdt(t):
        dt = t.executed_at
        return dt.astimezone(KST) if dt.tzinfo else dt.replace(tzinfo=KST)

    cash = 0
    lots: list[dict] = []
    for t in txs:
        if kdt(t) >= cutoff_dt:
            continue
        if t.kind == "deposit":
            cash += t.amount
        elif t.kind == "withdraw":
            cash -= t.amount
        elif t.kind == "buy":
            cash -= t.qty * t.price
            lots.append({"instrument_id": t.instrument_id, "qty": t.qty,
                         "price": t.price, "opened_at": t.executed_at})
        elif t.kind == "sell":
            cash += t.qty * t.price
            remaining = t.qty
            for l in lots:
                if remaining <= 0:
                    break
                if l["instrument_id"] != t.instrument_id or l["opened_at"] > t.executed_at:
                    continue
                take = min(l["qty"], remaining)
                l["qty"] -= take
                remaining -= take
            lots = [l for l in lots if l["qty"] > 0]
    return lots, cash


def run_signal_batch(session: Session, target: date | None = None) -> SignalSnapshot:
    """시그널 배치 — append-only 버전 기록. 실패도 스냅샷으로 남긴다 (조용한 실패 금지)."""
    from app.backtests import load_aligned_bars

    try:
        bars_200, bars_lev, fp = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1))
    except Exception as exc:
        return _record(session, target or date.today(), "MISSING", detail={"error": str(exc)[:500]})

    signal_date = date.fromisoformat(bars_200[-1]["date"])  # 최신 종가 확정일
    if target is not None and signal_date < target:
        # 대상일 시세 미확보 → 발행 보류 (feature-strategy-engine §5.8)
        return _record(session, target, "MISSING",
                       detail={"last_bar": bars_200[-1]["date"], "reason": "market data not ingested yet"})
    try:
        result = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, Params(),
                              collect_plans=True, plan_final=True)
        last_plan = result.plans[-1]
    except Exception as exc:
        return _record(session, signal_date, "FAILED", detail={"error": str(exc)[:500]})

    snap = _record(
        session, signal_date,
        last_plan.status if last_plan.status != "OK" else "OK",
        regime=last_plan.regime.value, e=last_plan.e_target, w200=last_plan.w_200, wlev=last_plan.w_lev,
        gap=last_plan.gap_cancel_below, indicators=last_plan.indicators,
        detail={
            "model_capital": MODEL_CAPITAL,
            "model_equity": round(result.equity[-1]) if result.equity else MODEL_CAPITAL,
            "model_cash": round(result.cash_curve[-1]) if result.cash_curve else MODEL_CAPITAL,
            "model_qty_200": result.qty_200[-1] if result.qty_200 else 0,
            "model_qty_lev": result.qty_lev[-1] if result.qty_lev else 0,
            "plans": len(result.plans),
        },
    )
    for od in last_plan.orders:
        session.add(OrderSheetRow(signal_id=snap.id, instrument=od.instrument, side=od.side,
                                  otype=od.otype, qty=od.qty, price=od.price, kind=od.kind))
    session.commit()
    return snap


def _record(session: Session, trade_date: date, status: str, regime=None, e=None, w200=None,
            wlev=None, gap=None, indicators=None, detail=None) -> SignalSnapshot:
    prev = session.scalars(
        select(SignalSnapshot).where(SignalSnapshot.trade_date == trade_date)
    ).all()
    for s in prev:
        s.is_current = False
    snap = SignalSnapshot(
        trade_date=trade_date, version=len(prev) + 1, is_current=True, status=status,
        regime=regime, e_target=e, w_200=w200, w_lev=wlev, gap_cancel_below=gap,
        indicators={k: v for k, v in (indicators or {}).items() if v is not None},
        detail=detail or {},
    )
    session.add(snap)
    session.flush()
    return snap


def _portfolio_orders(session: Session, pid: int, user_id: int, force_freeze: bool = False,
                      now: datetime | None = None) -> dict:
    """내 실전 포트 기준 주문표 — 보유 로트·현금을 플래너 Portfolio 로 변환해 plan() 직접 실행 (ADR-005).

    force_freeze=True 는 09:01 실행기 전용 — 이 계산을 그날의 주문표로 동결한다(ADR-009). 화면 조회는 동결 뒤에는 스냅샷을 그대로 돌려준다.

    근사 규칙(ASSUMPTIONS): 실전 로트의 익절가는 '오늘 Grid' 기준 매수가×(1+Grid)로 부여,
    상승장이면 코어로 간주. 200 ETF 는 KODEX/TIGER 모두 K200 레그로 매핑.
    """
    from sqlalchemy import func

    from app.backtests import load_aligned_bars
    from app.models import Instrument, PositionLot, TradePortfolio, TradeTransaction
    from app.strategy.planner import K200, LEV, Lot, Portfolio, grid_ratio, plan, prepare
    from app.strategy.params import round_tick
    from app.strategy.regime import Regime

    pf_row = session.get(TradePortfolio, pid)
    if pf_row is None or pf_row.user_id != user_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="portfolio not found")

    from app.backtests import base_costs_for, user_algo_overrides
    if pf_row.market == "US":
        # 보유 레버리지에 따라 페어 결정 (TQQQ 보유 시 3배 파라미터)
        held_codes = {
            session.get(Instrument, l.instrument_id).code
            for l in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pid)).all()
        }
        if "TQQQ" in held_codes and "QLD" in held_codes:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="QLD 와 TQQQ 혼합 보유 — 한 포트에는 한 레버리지만 운용하세요")
        etf = "QQQ_TQQQ" if "TQQQ" in held_codes else "QQQ_QLD"
        codes = ("QQQ", "TQQQ" if "TQQQ" in held_codes else "QLD")
    else:
        # KR: 보유 중인 200 레그 종목을 주력으로 — TIGER 보유자는 TIGER 가격 기준 주문 (2026-09-01 지시)
        held = {
            session.get(Instrument, l.instrument_id).code
            for l in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pid)).all()
        }
        pref = (pf_row.params or {}).get("code_200") if pf_row.params else None
        if "102110" in held and "069500" not in held:
            code_200 = "102110"
        elif "069500" in held:
            code_200 = "069500"
        else:
            code_200 = pref or "069500"  # 보유 없으면 생성 시 선택한 조합 (기존 포트는 KODEX 유지)
        etf, codes = ("TIGER" if code_200 == "102110" else "KODEX"), (code_200, "122630")
    # 공식 결정 (2026-09-05 지시): 포트에 동결된 변수(params["algo"], 전환 시 스냅샷)가 있으면 그것만 —
    # 없으면(수동 포트·구형) 알고리즘 설정 추종. 포트별 공식이 섞이지 않는 격리 단위 = 포트 행.
    pf_algo = (pf_row.params or {}).get("algo")
    if isinstance(pf_algo, dict):
        from dataclasses import fields as _dcf
        _valid = {f.name for f in _dcf(Params)} - {"flags"}
        algo = {k: v for k, v in pf_algo.items() if k in _valid}  # 개정으로 사라진 키는 무시(견고성)
        algo_source = "portfolio"
    else:
        algo = user_algo_overrides(session, user_id)
        algo_source = "settings"
    bars_200, bars_lev, _ = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1), codes=codes)
    params = Params(**{**base_costs_for(etf), **algo})
    result = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, params)
    regime = Regime(result.regimes[-1])  # 시장 레짐은 가격만의 함수 — 포트와 무관

    def to_market(bars):
        return prepare([float(b["open"]) for b in bars], [float(b["high"]) for b in bars],
                       [float(b["low"]) for b in bars], [float(b["close"]) for b in bars], params)

    m200, mlev = to_market(bars_200), to_market(bars_lev)
    last = len(bars_200) - 1
    grid_today = grid_ratio(m200.atr20[last], m200.closes[last], params)
    base_day = date.fromisoformat(bars_200[last]["date"])
    exec_day = _next_exec_day(base_day, session if pf_row.market == "KR" else None)
    now = now or datetime.now(KST)

    # 계좌 상태 = 실행일 09:00 KST 직전 상태 — B안(2026-09-02) 의 동결 기준을 날짜에서 시각으로 (ADR-009, 2026-09-08).
    # 09:00 전 등록(입출금·체결)은 즉시 반영, 09:01 실행기가 같은 기준으로 발주하므로 HTS 주문장 = 화면. 이후 등록은 다음 주문표부터.
    cutoff = freeze_at(exec_day) if pf_row.market == "KR" else exec_day
    lot_rows, cash = _state_before(session, pid, cutoff)
    lots: list[Lot] = []
    qty_200 = qty_lev = 0
    SUPPORTED = ({"QQQ", "QLD", "TQQQ"} if pf_row.market == "US"
                 else {"069500", "102110", "122630"})
    LEV_CODES = {"122630", "QLD", "TQQQ"}
    for l in lot_rows:
        code = session.get(Instrument, l["instrument_id"]).code
        if code not in SUPPORTED:
            from fastapi import HTTPException
            raise HTTPException(status_code=409,
                                detail=f"전략 대상 외 종목({code}) 보유 — 이 포트 기준 주문표를 계산할 수 없습니다")
        if code in LEV_CODES:
            lots.append(Lot(LEV, l["qty"], l["price"], "lev_strat", None, 0))
            qty_lev += l["qty"]
        else:  # 1배 주력(069500/102110/QQQ) → 200 레그
            if regime is Regime.BULL and params.flags.f1_no_tp_in_bull:
                lots.append(Lot(K200, l["qty"], l["price"], "core", None, 0))
            else:
                # 익절 기준가 = 최근 종가 × (1+오늘 Grid) — 정본 §5.6 코어 편입 규칙 준용.
                # 평단 기준으로 하면 과거 매수분이 "이미 목표 도달"로 시작 즉시 전량 매도됨 (2026-08-28 검토)
                tp = round_tick(m200.closes[last] * (1 + grid_today), params.tick, up=True)
                lots.append(Lot(K200, l["qty"], l["price"], "grid", tp, 0))
            qty_200 += l["qty"]

    user_pf = Portfolio(cash=float(cash), lots=lots)
    # 소량 진입 부트스트랩 (ADR-010): 시작일 = max(포트 생성일, 첫 거래일) — 시작 패널이 입금을 직전 영업일로 소급 기록해도 생성일이 잡아 준다.
    # days_since_start = 시작일 **뒤** 거래일(봉) 수, 기준일까지. 오늘 시작 → 오늘 저녁 0일째. 거래가 없으면 None(자본 없음 → 부트스트랩 없음)
    days_since_start = None
    first_tx = session.scalar(select(func.min(TradeTransaction.executed_at)).where(TradeTransaction.portfolio_id == pid))
    if first_tx is not None:
        def _kd(dt):
            return (dt.astimezone(KST) if dt.tzinfo else dt.replace(tzinfo=KST)).date()
        start_day = max(_kd(first_tx), _kd(pf_row.created_at)) if pf_row.created_at else _kd(first_tx)
        # 콜드 스타트만 — 시작일까지 등록된 매수(보유분 입력·전환 시드)가 있으면 이미 보유로 시작한 포트라 대상 외 (2026-09-08 사용자 지적)
        started_with_holdings = any(_kd(t.executed_at) <= start_day for t in session.scalars(
            select(TradeTransaction).where(TradeTransaction.portfolio_id == pid, TradeTransaction.kind == "buy")).all())
        if not started_with_holdings:
            days_since_start = sum(1 for b in bars_200 if start_day < date.fromisoformat(b["date"]) <= base_day)
    p = plan(last, m200, mlev, regime, user_pf, params, days_since_start=days_since_start)
    # 계획 vs 등록 체결 대조 (2026-09-05 지시) — 실패해도 주문표는 떠야 하므로 방어적으로
    from app.broker import reconcile_for_portfolio
    try:
        reconcile = reconcile_for_portfolio(session, pid)
    except Exception:  # noqa: BLE001
        reconcile = None
    # 동결 공식 도움말 풍선용 라벨·기본값 (2026-09-05 지시)
    algo_detail: list[dict] = []
    if algo_source == "portfolio" and algo:
        from app.settings import PARAM_REGISTRY
        _labels = {k: lab for k, lab, *_rest in PARAM_REGISTRY}
        algo_detail = [{"key": k, "label": _labels.get(k, k), "value": v,
                        "default": getattr(Params(), k, None)} for k, v in sorted(algo.items())]
    # 표시용 병합 — 로트별 익절이 같은 가격이면 한 주문으로 (HTS 에는 하나로 넣으면 됨, 2026-08-29 검토)
    merged: dict[tuple, dict] = {}
    for o in p.orders:
        key = (o.instrument, o.side, o.otype, o.price, o.kind)
        if key in merged:
            merged[key]["qty"] += o.qty
        else:
            merged[key] = {"instrument": o.instrument, "side": o.side, "otype": o.otype,
                           "qty": o.qty, "price": o.price, "kind": o.kind}
    out = {
        "basis": "portfolio", "portfolio": {"id": pf_row.id, "name": pf_row.name},
        "exec_day": exec_day.isoformat(),  # 이 주문표의 실행일 — 오늘/예정 표시용 (2026-09-02)
        # 다음 거래일 주문표 미작성 구간 안내 (2026-09-07 지시)
        "pending": _plan_pending(exec_day)[0], "pending_note": _plan_pending(exec_day)[1],
        # 배치 스냅샷이 없어도 화면이 그릴 수 있게 레짐·노출·기준일·지표를 함께 준다 (2026-09-05: 챗봇과 화면 불일치)
        "signal_date": base_day.isoformat(), "regime": regime.value, "e_target": p.e_target,
        "indicators": {k: v for k, v in (p.indicators or {}).items() if v is not None},
        # 어떤 공식으로 계산했는지 표시용 (2026-09-05): portfolio = 전환 시 동결 변수, settings = 설정 추종
        "algo_source": algo_source, "algo_overrides": algo, "algo_detail": algo_detail,
        "reconcile": reconcile,  # 계획 vs 등록 체결 대조 경고 (2026-09-05 지시) — 표시만
        "code_200": codes[0], "name_200": {"069500": "KODEX 200", "102110": "TIGER 200", "QQQ": "QQQ"}.get(codes[0], codes[0]),
        "account": {"cash": cash, "qty_200": qty_200, "qty_lev": qty_lev,
                    "equity": round(user_pf.equity(m200.closes[last], mlev.closes[last]))},
        "orders": list(merged.values()),
        "gap_cancel_below": p.gap_cancel_below,
        "gap_cancel_exact": p.gap_cancel_exact,  # 09:01 실행기의 시가 판정용 정확값 (ADR-009)
        # 소량 진입 구간 표시 (ADR-010): {"day": n, "days": 10} — 부트스트랩 주문이 있는 날만
        "boot": ({"day": p.indicators.get("boot_day"), "days": p.indicators.get("boot_days")} if p.indicators.get("boot_day") else None),
    }
    # '그날의 주문표' 보존 — 일자별 매매 일지의 계획 vs 체결 대조 (2026-08-29 지시).
    # 주문표는 기준일(bars[last]) 종가 계획 = 다음 거래일 실행분이라 다음 거래일 키로 저장.
    # B안 이후 계획은 실행일 당일 체결과 무관하게 결정론적이라 upsert 갱신이 보존을 해치지 않는다.
    from app.models import PortfolioPlan
    row = session.scalar(select(PortfolioPlan).where(
        PortfolioPlan.portfolio_id == pid, PortfolioPlan.trade_date == exec_day))
    payload = {"regime": regime.value, "signal_date": base_day.isoformat(),
               "orders": out["orders"], "gap_cancel_below": p.gap_cancel_below,
               "gap_cancel_exact": p.gap_cancel_exact,  # 무인 실행의 시가 판정은 정확값 (2026-09-06)
               "account": out["account"], "e_target": p.e_target}
    from app.dashboard import kst_today

    existing = (row.payload or {}) if row is not None else {}
    if force_freeze:
        # 09:01 실행기 — 이 계산이 그날의 주문표다 (ADR-009). 이후 화면은 이 스냅샷을 그대로 보인다
        payload["frozen_at"] = now.isoformat(timespec="seconds")
        if row is None:
            session.add(PortfolioPlan(portfolio_id=pid, trade_date=exec_day, payload=payload))
        else:
            row.payload = payload
        out["frozen"], out["frozen_at"] = True, payload["frozen_at"]
    elif row is None:
        session.add(PortfolioPlan(portfolio_id=pid, trade_date=exec_day, payload=payload))
        out["frozen"] = False
    elif existing.get("frozen_at"):
        # 동결 뒤 — 재계산 결과 대신 09:01 발주 기준이 된 스냅샷을 보인다 (실행일 09:00 이후 등록은 다음 주문표부터)
        out["orders"] = list(existing.get("orders") or [])
        out["account"] = dict(existing.get("account") or out["account"])
        out["gap_cancel_below"] = existing.get("gap_cancel_below", out["gap_cancel_below"])
        out["frozen"], out["frozen_at"] = True, existing["frozen_at"]
    elif exec_day > kst_today() or (pf_row.market == "KR" and now < freeze_at(exec_day)):
        row.payload = payload  # 동결 전 — 최신 원장 상태로 갱신 (실시간 반영)
        out["frozen"] = False
    else:
        # 실행일 09:00 이후(미국은 실행일 도래 이후) 계획은 불변 — "그날 아침의 계획" 보존 (2026-09-02 지시). 화면은 재계산값
        out["frozen"] = False
    session.commit()
    return out


@router.get("/signals/journal")
def get_signal_journal(days: int = 20, market: str = "KR", _user: int = Depends(current_user_id),
                       session: Session = Depends(get_session)) -> dict:
    """모델 포트의 최근 매매 이력 — 주문표 신호의 맥락 (계획·체결·수익률·보유)."""
    from app.backtests import base_costs_for, load_aligned_bars

    try:
        if market == "US":
            from app.strategy.trendfilter import run_tf_backtest

            bars_200, _, _ = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1),
                                               codes=("QQQ", "QQQ"))
            r = run_tf_backtest(bars_200, MODEL_CAPITAL)
        else:
            bars_200, bars_lev, _ = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1))
            r = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, Params(), collect_plans=True)
    except Exception:
        return {"items": []}
    fills_by_date: dict[str, list] = {}
    for f in r.fills:
        fills_by_date.setdefault(f.date, []).append(
            {"instrument": f.instrument, "side": f.side, "kind": f.kind, "price": f.price, "qty": f.qty})
    items = []
    n = len(r.dates)
    for i in range(max(0, n - min(days, 120)), n):
        plan_i = r.plans[i] if i < len(r.plans) else None
        prev_eq = r.equity[i - 1] if i > 0 else MODEL_CAPITAL
        eq_r, prev_r = round(r.equity[i]), round(prev_eq)
        items.append({
            "date": r.dates[i], "regime": r.regimes[i],
            "equity": eq_r,
            "day_return": (r.equity[i] / prev_eq - 1.0) if prev_eq else 0.0,
            "day_pnl": eq_r - prev_r,
            "qty_200": r.qty_200[i], "qty_lev": r.qty_lev[i], "cash": round(r.cash_curve[i]),
            "planned": [
                {"instrument": o.instrument, "side": o.side, "kind": o.kind, "price": o.price, "qty": o.qty}
                for o in (plan_i.orders if plan_i and plan_i.status == "OK" else [])
            ],
            "fills": fills_by_date.get(r.dates[i], []),
        })
    items.reverse()
    return {"items": items}


def _live_us_model(session: Session, user_id: int) -> dict:
    """미국 모델 신호 — TF(추세 필터 보유) 전략, 라이브 계산 (2026-08-31 시장별 분리).

    모델 자본 $1,000,000(센트). 보유=BULL / 현금 대기=NEUTRAL 로 표기.
    """
    from app.backtests import load_aligned_bars as _load
    from app.strategy.trendfilter import run_tf_backtest

    try:
        bars, _, _ = _load(session, date(1990, 1, 1), date(2100, 1, 1), codes=("QQQ", "QQQ"))
    except Exception as exc:
        return {"status": "MISSING", "reason": str(exc)[:300], "market": "US"}
    result = run_tf_backtest(bars, MODEL_CAPITAL)
    lp = result.plans[-1]
    return {
        "status": lp.status, "trade_date": bars[-1]["date"], "version": 0, "market": "US",
        "strategy": "TF",
        "regime": lp.regime.value if lp.status == "OK" else None,
        "e_target": lp.e_target, "w_200": lp.w_200, "w_lev": 0.0,
        "gap_cancel_below": None,
        "indicators": {k: v for k, v in lp.indicators.items() if v is not None},
        "detail": {
            "model_capital": MODEL_CAPITAL,
            "model_equity": round(result.equity[-1]) if result.equity else MODEL_CAPITAL,
            "model_cash": round(result.cash_curve[-1]) if result.cash_curve else MODEL_CAPITAL,
            "model_qty_200": result.qty_200[-1] if result.qty_200 else 0,
            "model_qty_lev": 0,
        },
        "orders": [
            {"instrument": o.instrument, "side": o.side, "otype": o.otype,
             "qty": o.qty, "price": o.price, "kind": o.kind} for o in lp.orders
        ],
        "basis": "model",
    }


def _tf_portfolio_orders(session: Session, pf_row, pid: int) -> dict:
    """미국 포트 기준 TF 주문표 — 목표는 '전량 보유' 또는 '전량 현금' (2026-08-31).

    QLD/TQQQ 등 전략 외 보유는 항상 청산 대상으로 표기한다.
    """
    from app.backtests import load_aligned_bars as _load
    from app.models import Instrument
    from app.strategy.trendfilter import TF_CASH_RESERVE, TF_EXIT_BUFFER, TF_MA, run_tf_backtest
    from app.strategy.regime import Regime  # 모듈 상단 import 정리 때 빠져 미국 TF 포트 주문표가 NameError 로 실패하던 결함 (2026-09-06)

    bars, _, _ = _load(session, date(1990, 1, 1), date(2100, 1, 1), codes=("QQQ", "QQQ"))
    result = run_tf_backtest(bars, MODEL_CAPITAL)
    lp = result.plans[-1]
    want_hold = lp.status == "OK" and lp.regime is Regime.BULL
    base_day = date.fromisoformat(bars[-1]["date"])
    exec_day = _next_exec_day(base_day)

    # 계좌 상태 = 신호 기준일 종가 시점 — B안 (RAVG 쪽과 동일 계약, 2026-09-02)
    lot_rows, cash = _state_before(session, pid, exec_day)
    qty_qqq = qty_lev = 0
    for l in lot_rows:
        code = session.get(Instrument, l["instrument_id"]).code
        if code == "QQQ":
            qty_qqq += l["qty"]
        else:
            qty_lev += l["qty"]

    close = float(bars[-1]["close"])
    orders = []
    if qty_lev > 0:  # 전략 외 자산은 상태 무관 청산
        orders.append({"instrument": "LEV", "side": "sell", "otype": "market",
                       "qty": qty_lev, "price": None, "kind": "tf_exit"})
    if want_hold:
        est = int((cash + (qty_lev * close if qty_lev else 0)) * (1 - TF_CASH_RESERVE) / close) if close else 0  # 보수용 현금 여유
        if est > 0:
            orders.append({"instrument": "K200", "side": "buy", "otype": "market",
                           "qty": est, "price": None, "kind": "tf_entry"})
    elif qty_qqq > 0:
        orders.append({"instrument": "K200", "side": "sell", "otype": "market",
                       "qty": qty_qqq, "price": None, "kind": "tf_exit"})

    equity = round(cash + (qty_qqq + qty_lev) * close)
    out = {
        "basis": "portfolio", "strategy": "TF",
        "portfolio": {"id": pf_row.id, "name": pf_row.name},
        "exec_day": exec_day.isoformat(), "signal_date": base_day.isoformat(),
        "pending": _plan_pending(exec_day)[0], "pending_note": _plan_pending(exec_day)[1],
        "account": {"cash": cash, "qty_200": qty_qqq, "qty_lev": qty_lev, "equity": equity},
        "orders": orders, "gap_cancel_below": None,
    }
    # 계획 스냅샷 (일자별 일지) — B안 이후 결정론적 upsert
    from app.models import PortfolioPlan
    row = session.scalar(select(PortfolioPlan).where(
        PortfolioPlan.portfolio_id == pid, PortfolioPlan.trade_date == exec_day))
    payload = {"regime": lp.regime.value, "signal_date": base_day.isoformat(),
               "orders": out["orders"], "gap_cancel_below": None,
               "account": out["account"], "e_target": lp.e_target, "strategy": "TF"}
    from app.dashboard import kst_today
    if row is None:
        session.add(PortfolioPlan(portfolio_id=pid, trade_date=exec_day, payload=payload))
    elif exec_day > kst_today():
        row.payload = payload  # 아직 실행 전 — 최신 상태로 갱신
    # 실행일 도래(오늘·과거) 계획은 불변 — "그날 아침의 계획" 보존 (2026-09-02 지시)
    session.commit()
    return out


@router.get("/signals/daily")
def get_daily_signal(date_: date | None = Query(default=None, alias="date"),
                     portfolio_id: int | None = None,
                     market: str = "KR",
                     _user: int = Depends(current_user_id),
                     session: Session = Depends(get_session)) -> dict:
    if portfolio_id is None and market == "US":
        return _live_us_model(session, _user)
    q = select(SignalSnapshot).where(SignalSnapshot.is_current)
    if date_ is not None:
        q = q.where(SignalSnapshot.trade_date == date_)
    snap = session.scalars(q.order_by(SignalSnapshot.trade_date.desc()).limit(1)).first()
    if portfolio_id is not None:
        pf_row = session.get(TradePortfolio, portfolio_id)
        if pf_row is None or pf_row.user_id != _user:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="portfolio not found")
        if pf_row.market == "US":
            # 미국 포트 — TF 전략 기준 (2026-08-31 시장별 분리)
            base = _live_us_model(session, _user)
            if base["status"] != "OK":
                return base
            base.update(_us_portfolio_orders(session, pf_row, portfolio_id))  # TF 또는 LTM (포트 params.etf)
            return base
        # 내 실전 포트 기준 주문표 (2026-08-28 검토 반영) — 주문·계좌 현황을 내 포트 기준으로 교체.
        # 배치 스냅샷이 없거나 실패 상태여도 포트 기준 주문표는 시세로 직접 계산해 준다 (2026-09-05):
        # 챗봇 도구는 같은 계산을 바로 쓰는데 화면만 "시그널 없음"으로 나오던 불일치 해소.
        extra = _portfolio_orders(session, portfolio_id, _user)
        if snap is not None and snap.status == "OK":
            return {
                "status": snap.status, "trade_date": snap.trade_date.isoformat(), "version": snap.version,
                "regime": snap.regime, "e_target": float(snap.e_target) if snap.e_target is not None else None,
                "w_200": float(snap.w_200) if snap.w_200 is not None else None,
                "w_lev": float(snap.w_lev) if snap.w_lev is not None else None,
                "gap_cancel_below": extra["gap_cancel_below"] or snap.gap_cancel_below,
                "indicators": snap.indicators, "detail": snap.detail,
                **extra,
            }
        return {
            "status": "OK", "trade_date": extra["signal_date"], "version": None,
            "w_200": None, "w_lev": None, "detail": None,
            "snapshot_missing": True,   # 화면 안내용 — 배치 후 확정 표기로 바뀐다
            **extra,
        }
    if snap is None:
        return {"status": "MISSING", "reason": "시그널 배치가 아직 실행되지 않았습니다 — 시세 시딩 후 배치를 실행하세요"}
    orders = session.scalars(
        select(OrderSheetRow).where(OrderSheetRow.signal_id == snap.id).order_by(OrderSheetRow.id)
    ).all()
    return {
        "status": snap.status, "trade_date": snap.trade_date.isoformat(), "version": snap.version,
        "regime": snap.regime, "e_target": float(snap.e_target) if snap.e_target is not None else None,
        "w_200": float(snap.w_200) if snap.w_200 is not None else None,
        "w_lev": float(snap.w_lev) if snap.w_lev is not None else None,
        "gap_cancel_below": snap.gap_cancel_below,
        "indicators": snap.indicators, "detail": snap.detail,
        "orders": [
            {"instrument": o.instrument, "side": o.side, "otype": o.otype,
             "qty": o.qty, "price": o.price, "kind": o.kind} for o in orders
        ],
        "basis": "model",
    }


@router.get("/signals/history")
def get_signal_history(_user: int = Depends(current_user_id),
                       session: Session = Depends(get_session)) -> dict:
    """레짐·노출 이력 — 저장 스냅샷이 아니라 전략 재계산(결정론)으로 전체 구간 제공."""
    try:
        from app.backtests import load_aligned_bars

        bars_200, bars_lev, _ = load_aligned_bars(session, date(1990, 1, 1), date(2100, 1, 1))
        result = run_backtest(bars_200, bars_lev, MODEL_CAPITAL, Params())
        return {"items": [
            {"date": d, "regime": r, "exposure": e}
            for d, r, e in zip(result.dates, result.regimes, result.exposures)
        ]}
    except Exception:
        return {"items": []}


def _us_portfolio_orders(session: Session, pf_row, pid: int) -> dict:
    """미국 포트 전략 디스패처 (2026-09-06): params.etf 가 LTM_* 면 LTM, 아니면 TF(구형·기본)."""
    etf = str((pf_row.params or {}).get("etf") or "QQQ_TF")
    if etf.startswith("LTM_"):
        return _ltm_portfolio_orders(session, pf_row, pid, "TQQQ" if etf.endswith("TQQQ") else "QLD")
    return _tf_portfolio_orders(session, pf_row, pid)


def _ltm_portfolio_orders(session: Session, pf_row, pid: int, lev_code: str) -> dict:
    """미국 포트 기준 LTM 주문표 (2026-09-06 채택) — 규칙은 app/strategy/ltm.py 한 곳(ltm_states)에 있다.

    목표 노출 E(0 / 1 / 2) 를 현재 보유(QQQ·레버리지)와 비교해 시장가 리밸런스 주문을 만든다. 차이가 목표의 10% 미만이면
    주문 없음. 전량 현금(E=0)·첫 진입은 즉시. 다음 거래일 시가 체결 전제(B안).
    """
    from app.backtests import load_aligned_bars as _load
    from app.models import Instrument, PortfolioPlan
    from app.strategy.ltm import ltm_states, params_for, target_weights

    bars_1x, bars_lev, _ = _load(session, date(1990, 1, 1), date(2100, 1, 1), codes=("QQQ", lev_code))
    p = params_for(lev_code)
    st = ltm_states([float(b["close"]) for b in bars_1x], p)[-1]
    base_day = date.fromisoformat(bars_1x[-1]["date"])
    exec_day = _next_exec_day(base_day)
    lot_rows, cash = _state_before(session, pid, exec_day)
    q1 = qL = 0
    for l in lot_rows:
        code = session.get(Instrument, l["instrument_id"]).code
        if code == "QQQ":
            q1 += l["qty"]
        else:
            qL += l["qty"]  # 레버리지 레그(다른 레버리지 종목이 섞여 있으면 같은 레그로 취급해 청산·리밸런스 대상)
    c1, cL = float(bars_1x[-1]["close"]), float(bars_lev[-1]["close"])
    v = cash + q1 * c1 + qL * cL
    common = {"basis": "portfolio", "strategy": "LTM", "portfolio": {"id": pf_row.id, "name": pf_row.name},
              "exec_day": exec_day.isoformat(), "signal_date": base_day.isoformat(),
              "pending": _plan_pending(exec_day)[0], "pending_note": _plan_pending(exec_day)[1],
              "code_200": "QQQ", "name_200": "QQQ", "code_lev": lev_code, "name_lev": lev_code,
              "account": {"cash": cash, "qty_200": q1, "qty_lev": qL, "equity": round(v)}, "gap_cancel_below": None}
    if st is None:
        return {**common, "status": "INSUFFICIENT_HISTORY", "orders": [], "e_target": None, "regime": None}
    L = p.lev_multiple
    e_t = st["e_target"]
    e_now = ((q1 * c1 + qL * cL * L) / v) if v > 0 else 0.0
    w1, wL = target_weights(e_t, L)
    orders: list[dict] = []
    if abs(e_t - e_now) >= p.band * max(e_t, 0.5):
        v_inv = v * (1 - p.cash_reserve)  # 보수·수수료용 현금 여유 (엔진과 동일)
        t1 = int(w1 * v_inv / c1) if c1 > 0 else 0
        tL = int(wL * v_inv / cL) if (wL > 0 and cL > 0) else 0
        if e_t == 0:
            kind = "ltm_exit"
        elif q1 + qL == 0:
            kind = "ltm_entry"
        elif e_t > 1.0 and e_now <= 1.05:
            kind = "ltm_lever_on"
        elif e_t <= 1.0 and e_now > 1.05:
            kind = "ltm_lever_off"
        else:
            kind = "ltm_rebal"
        for inst, q_now, q_tgt in (("K200", q1, t1), ("LEV", qL, tL)):
            d = q_tgt - q_now
            if d != 0:
                orders.append({"instrument": inst, "side": "buy" if d > 0 else "sell", "otype": "market",
                               "qty": abs(d), "price": None, "kind": kind})
        orders.sort(key=lambda o: 0 if o["side"] == "sell" else 1)  # 매도(현금 확보) 먼저
    regime = "BEAR" if not st["on"] else ("BULL" if e_t > 1.0 else "NEUTRAL")
    out = {**common, "status": "OK", "regime": regime, "e_target": e_t, "w_200": w1, "w_lev": wL,
           "indicators": {"close": c1, "ma200": st["ma"], "gap_to_ma200": c1 / st["ma"] - 1, "exit_level": st["exit_level"],
                          "mom12": st["mom"], "shock_days_left": st["shock_left"], "exposure": e_now},
           "orders": orders}
    row = session.scalar(select(PortfolioPlan).where(PortfolioPlan.portfolio_id == pid, PortfolioPlan.trade_date == exec_day))
    payload = {"regime": regime, "signal_date": base_day.isoformat(), "orders": orders, "gap_cancel_below": None,
               "account": out["account"], "e_target": e_t, "strategy": "LTM"}
    from app.dashboard import kst_today
    if row is None:
        session.add(PortfolioPlan(portfolio_id=pid, trade_date=exec_day, payload=payload))
    elif exec_day > kst_today():
        row.payload = payload
    session.commit()
    return out
