"""대시보드 API — 스냅샷·추이·캘린더·기타 자산 (feature-dashboard §5·§8)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import AnalyticsEvent, AssetSnapshot, ManualAsset, TradePortfolio, User

router = APIRouter()

RANGES = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "ALL": 36500}

KST = timezone(timedelta(hours=9))


def kst_today() -> date:
    """한국 거래일 기준 오늘 — UTC date.today() 는 00~09시(KST) 사이 하루 밀림 (검증 M-7)."""
    return datetime.now(KST).date()


def user_flows_between(session: Session, user_id: int, d_from: date, d_to: date) -> int:
    """(d_from, d_to] 구간 사용자 전체 외부 현금흐름 합 — 입금 +, 출금 − (검증 C-3·M-6).

    입출금은 자산 증감이 아니라 이동이므로, 손익 표기는 반드시 흐름을 차감해야 한다.
    """
    from app.models import TradeTransaction

    pids = select(TradePortfolio.id).where(TradePortfolio.user_id == user_id,
                                            TradePortfolio.market == "KR")
    txs = session.scalars(select(TradeTransaction).where(
        TradeTransaction.portfolio_id.in_(pids),
        TradeTransaction.kind.in_(("deposit", "withdraw")))).all()
    total = 0
    for t in txs:
        d = t.executed_at.astimezone(KST).date() if t.executed_at.tzinfo else t.executed_at.date()
        if d_from < d <= d_to:
            total += t.amount if t.kind == "deposit" else -t.amount
    return total


def record_event(session: Session, kind: str, user_id: int | None) -> None:
    session.add(AnalyticsEvent(user_id=user_id, kind=kind))


def latest_closes(session: Session, inst_ids: set[int]) -> dict[int, float]:
    """종목 집합의 최신 종가 일괄 조회 — 로트당 개별 조회(N+1) 금지 (검토 B3)."""
    from app.models import OhlcvDaily

    if not inst_ids:
        return {}
    sub = (select(OhlcvDaily.instrument_id, OhlcvDaily.close_raw, OhlcvDaily.adj_factor)
           .where(OhlcvDaily.instrument_id.in_(inst_ids))
           .order_by(OhlcvDaily.instrument_id, OhlcvDaily.trade_date.desc())
           .distinct(OhlcvDaily.instrument_id))
    return {iid: close * float(adj) for iid, close, adj in session.execute(sub).all()}


def _portfolio_state(session: Session, pf_id: int, prices: dict[int, float]) -> tuple[int, int, int]:
    """포트의 (stock_value, cash, cost) — 정수 확정 (ADR-008). 시세 없으면 취득가 평가."""
    from app.models import PositionLot, TradeTransaction

    lots = session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pf_id)).all()
    stock = round(sum(l.qty_open * prices.get(l.instrument_id, l.price) for l in lots))
    cost = sum(l.qty_open * l.price for l in lots)
    cash = 0
    for t in session.scalars(select(TradeTransaction).where(
            TradeTransaction.portfolio_id == pf_id)).all():
        if t.kind == "deposit":
            cash += t.amount
        elif t.kind == "withdraw":
            cash -= t.amount
        elif t.kind == "buy":
            cash -= t.qty * t.price
        elif t.kind == "sell":
            cash += t.qty * t.price
    return stock, cash, cost


def compute_user_snapshot(session: Session, user_id: int, snap_date: date) -> AssetSnapshot:
    """포트 스냅샷(정수 원천)을 확정하고 사용자 스냅샷을 합산 유도 — 같은 트랜잭션 (ADR-008).

    적재는 ON CONFLICT 단일문(배치·열람 동시 실행 경합 제거, 검토 D2).
    US 포트는 센트 그대로 currency='USD' 로 적재하고 KRW 합산에서 제외한다.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models import PortfolioSnapshot, PositionLot

    pfs = session.scalars(select(TradePortfolio).where(
        TradePortfolio.user_id == user_id)).all()
    pf_ids = [p.id for p in pfs]
    inst_ids = set(session.scalars(select(PositionLot.instrument_id).where(
        PositionLot.portfolio_id.in_(pf_ids))).all()) if pf_ids else set()
    prices = latest_closes(session, inst_ids)

    kr_stock = kr_cash = 0
    for pf in pfs:
        stock, cash, _cost = _portfolio_state(session, pf.id, prices)
        currency = "KRW" if pf.market == "KR" else "USD"
        stmt = pg_insert(PortfolioSnapshot.__table__).values(
            portfolio_id=pf.id, snap_date=snap_date,
            equity=stock + cash, stock_value=stock, cash=cash, currency=currency,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_portfolio_snapshots_pid_date",
            set_={"equity": stmt.excluded.equity, "stock_value": stmt.excluded.stock_value,
                  "cash": stmt.excluded.cash, "currency": stmt.excluded.currency},
        )
        session.execute(stmt)
        if currency == "KRW":
            kr_stock += stock
            kr_cash += cash

    other = sum(m.value for m in session.scalars(
        select(ManualAsset).where(ManualAsset.user_id == user_id)).all())
    # 매매일지 자산 (0020, 2026-09-05 지시) — 진행 중 일지의 보유 취득원가 합. 실전매매와 같은 계좌면 제외(중복 방지)
    from app.mjournal import journal_assets

    journal = sum(ja["value"] for ja in journal_assets(session, user_id) if ja["counted"])  # 평가액(시세 없으면 원가)
    snap = session.scalar(select(AssetSnapshot).where(
        AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date == snap_date))
    if snap is None:
        snap = AssetSnapshot(user_id=user_id, snap_date=snap_date, total=0, stock=0, cash=0, other=0)
        session.add(snap)
    snap.stock, snap.cash, snap.other, snap.journal = kr_stock, kr_cash, other, journal
    snap.total = kr_stock + kr_cash + other + journal  # 불변식: Σ(KRW 포트 equity) + other + journal == total
    session.flush()
    return snap


@router.get("/dashboard")
def dashboard(user_id: int = Depends(current_user_id), session: Session = Depends(get_session)) -> dict:
    record_event(session, "visit", user_id)
    today = kst_today()
    snap = compute_user_snapshot(session, user_id, today)  # 열람 시점 최신화
    prev = session.scalars(
        select(AssetSnapshot).where(AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date < today)
        .order_by(AssetSnapshot.snap_date.desc()).limit(1)
    ).first()
    first = session.scalars(
        select(AssetSnapshot).where(AssetSnapshot.user_id == user_id)
        .order_by(AssetSnapshot.snap_date).limit(1)
    ).first()
    session.commit()
    manuals = session.scalars(select(ManualAsset).where(ManualAsset.user_id == user_id)).all()
    # 전일 대비·누적 손익은 외부 입출금을 차감한 순수 성과 (단순 Dietz, 검증 C-3·M-6)
    change = 0
    change_pct = None
    if prev:
        f = user_flows_between(session, user_id, prev.snap_date, today)
        change = snap.total - prev.total - f
        denom = prev.total + f
        change_pct = change / denom if denom > 0 else None
    since_pct = None
    if first and first.snap_date < today:
        f_all = user_flows_between(session, user_id, first.snap_date, today)
        denom = first.total + f_all
        since_pct = (snap.total - first.total - f_all) / denom if denom > 0 else None
    # 자산 내용 카드 — KR/US 구분 (feature-dashboard §5, 2026-09-02). US 는 센트, $ 표기는 웹 담당.
    from app.models import PositionLot

    def _market_breakdown(market: str) -> dict:
        pfs = session.scalars(select(TradePortfolio).where(
            TradePortfolio.user_id == user_id, TradePortfolio.market == market)).all()
        pf_ids = [p.id for p in pfs]
        inst_ids = set(session.scalars(select(PositionLot.instrument_id).where(
            PositionLot.portfolio_id.in_(pf_ids))).all()) if pf_ids else set()
        prices = latest_closes(session, inst_ids)
        value = cost = 0
        for p in pfs:
            s, _c, co = _portfolio_state(session, p.id, prices)
            value += s
            cost += co
        pnl = value - cost
        return {"value": value, "cost": cost, "pnl": pnl,
                "pnl_pct": (pnl / cost) if cost > 0 else None}

    # 포트별 분리 표기 (2026-09-02 지시) — 진행 중 실전매매 각각의 평가액·평가손익
    port_rows = []
    all_pfs = session.scalars(select(TradePortfolio).where(TradePortfolio.user_id == user_id)
                              .order_by(TradePortfolio.id)).all()
    all_inst = set(session.scalars(select(PositionLot.instrument_id).where(
        PositionLot.portfolio_id.in_([p.id for p in all_pfs]))).all()) if all_pfs else set()
    all_prices = latest_closes(session, all_inst)
    for pfr in all_pfs:
        stock_v, cash_v, cost_v = _portfolio_state(session, pfr.id, all_prices)
        if stock_v == 0 and cash_v == 0 and cost_v == 0:
            continue  # 활동 없는 빈 포트(기본 계좌 등)는 표기 생략
        pnl = stock_v - cost_v
        # 계좌별 도넛용 종목 구성 (2026-09-05 지시) — 종목별 수량·평가액
        from app.models import Instrument, PositionLot
        pos: dict[int, dict] = {}
        for l in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pfr.id)).all():
            it = pos.setdefault(l.instrument_id, {"qty": 0, "value": 0.0})
            it["qty"] += l.qty_open
            it["value"] += l.qty_open * all_prices.get(l.instrument_id, l.price)
        positions = []
        for iid, it in pos.items():
            if it["qty"] <= 0:
                continue
            inst = session.get(Instrument, iid)
            positions.append({"code": inst.code, "name": inst.name,
                              "qty": it["qty"], "value": round(it["value"])})
        port_rows.append({
            "id": pfr.id, "name": pfr.name, "market": pfr.market,
            "equity": round(stock_v) + cash_v, "stock_value": round(stock_v), "cash": cash_v,
            "pnl": round(pnl), "pnl_pct": (pnl / cost_v) if cost_v > 0 else None,
            "color": (pfr.params or {}).get("color"),  # 탭 배경색 (2026-09-05)
            "positions": positions,
        })
    # 스파크라인용 추세 (2026-09-05 지시) — 포트별·시장별 최근 45일 스냅샷 equity 시리즈
    from app.models import PortfolioSnapshot
    since = today - timedelta(days=45)
    snaps = session.execute(
        select(PortfolioSnapshot.portfolio_id, PortfolioSnapshot.snap_date,
               PortfolioSnapshot.equity, PortfolioSnapshot.currency)
        .where(PortfolioSnapshot.portfolio_id.in_([p["id"] for p in port_rows] or [0]),
               PortfolioSnapshot.snap_date >= since)
        .order_by(PortfolioSnapshot.snap_date)).all()
    by_port: dict[int, list[int]] = {}
    kr_by_date: dict = {}
    us_by_date: dict = {}
    total_by_date: dict = {}
    for pid_, d_, eq_, cur_ in snaps:
        by_port.setdefault(pid_, []).append(eq_)
        bucket = us_by_date if cur_ == "USD" else kr_by_date
        bucket[d_] = bucket.get(d_, 0) + eq_
    # 스냅샷은 매일 16:40 부터 쌓여 새 포트는 이틀이 지나야 선이 된다 — 그동안은 원장 일별 평가액으로 보완 (2026-09-05 지시)
    from app.models import TradeTransaction
    from app.portfolios import _daily_series

    for p in port_rows:
        tr = by_port.get(p["id"], [])
        if len(tr) < 2:
            txs = session.scalars(select(TradeTransaction).where(TradeTransaction.portfolio_id == p["id"])
                                  .order_by(TradeTransaction.executed_at, TradeTransaction.id)).all()
            try:
                ledger = [round(v) for d_, v, _f in _daily_series(session, p["id"], txs) if d_ >= since]
            except Exception:  # noqa: BLE001 — 추세는 보조 정보, 실패해도 대시보드는 떠야 한다
                ledger = []
            if len(ledger) >= 2:
                tr = ledger
        p["trend"] = tr
    # 총자산 추세는 사용자 스냅샷에서
    totals = session.scalars(select(AssetSnapshot).where(
        AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date >= since)
        .order_by(AssetSnapshot.snap_date)).all()
    total_by_date = [s_.total for s_ in totals]
    from app.mjournal import journal_assets

    journals = journal_assets(session, user_id)
    return {
        "total": snap.total, "stock": snap.stock, "cash": snap.cash, "other": snap.other,
        # 주식 거래 자산(실전매매 KRW 주식+현금)과 매매일지 종합 자산을 분리 표기 (2026-09-05 지시)
        "trading_total": snap.stock + snap.cash,
        "journal": snap.journal or 0,
        "journals": journals,
        "portfolios": port_rows,
        "total_trend": total_by_date,
        "kr_trend": [v for _d, v in sorted(kr_by_date.items())],
        "us_trend": [v for _d, v in sorted(us_by_date.items())],
        "change_amount": change,
        "change_pct": change_pct,
        "since_inception_pct": since_pct,
        "kr_stock": _market_breakdown("KR"),
        "us_stock": _market_breakdown("US"),  # 값 단위: 센트 (환율 미도입 — KRW 합산 제외)
        "manual_assets": [
            {"id": m.id, "name": m.name, "category": m.category, "value": m.value} for m in manuals
        ],
    }


@router.get("/portfolio/trend")
def trend(range_: str = "3M", user_id: int = Depends(current_user_id),
          session: Session = Depends(get_session)) -> dict:
    key = range_.upper()
    if key not in RANGES:
        raise HTTPException(status_code=422, detail=f"range must be one of {list(RANGES)}")
    since = kst_today() - timedelta(days=RANGES[key])
    rows = session.scalars(
        select(AssetSnapshot).where(AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date >= since)
        .order_by(AssetSnapshot.snap_date)
    ).all()

    # 포트별 다선 (ADR-008) — 기존 items 비파괴, series 추가. 웹은 currency='KRW' 만 그린다.
    from app.models import PortfolioSnapshot

    series = []
    pfs = session.scalars(select(TradePortfolio).where(
        TradePortfolio.user_id == user_id).order_by(TradePortfolio.id)).all()
    for pf in pfs:
        ps = session.scalars(
            select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == pf.id,
                                            PortfolioSnapshot.snap_date >= since)
            .order_by(PortfolioSnapshot.snap_date)
        ).all()
        if key == "ALL" and len(ps) > 366:
            # ALL 은 주 단위 샘플(각 ISO 주의 마지막 스냅샷) — 페이로드·복호 비용 통제 (검토 B4·D3)
            by_week: dict[tuple[int, int], PortfolioSnapshot] = {}
            for r in ps:
                by_week[r.snap_date.isocalendar()[:2]] = r
            ps = sorted(by_week.values(), key=lambda r: r.snap_date)
        if not ps:
            continue
        series.append({
            "portfolio_id": pf.id, "name": pf.name, "market": pf.market,
            "currency": "KRW" if pf.market == "KR" else "USD",
            "points": [{"date": r.snap_date.isoformat(), "equity": r.equity} for r in ps],
        })

    return {"items": [
        {"date": r.snap_date.isoformat(), "total": r.total, "stock": r.stock,
         "cash": r.cash, "other": r.other} for r in rows
    ], "series": series}


@router.get("/portfolio/calendar")
def calendar(month: str, user_id: int = Depends(current_user_id),
             session: Session = Depends(get_session)) -> dict:
    """일간 손익 캘린더 — 스냅샷 전일 대비 증감."""
    try:
        first = date.fromisoformat(month + "-01")
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = session.scalars(
        select(AssetSnapshot).where(AssetSnapshot.user_id == user_id,
                                    AssetSnapshot.snap_date >= first - timedelta(days=7),
                                    AssetSnapshot.snap_date < nxt)
        .order_by(AssetSnapshot.snap_date)
    ).all()
    items = []
    for prev, cur in zip(rows, rows[1:]):
        if cur.snap_date >= first:
            f = user_flows_between(session, user_id, prev.snap_date, cur.snap_date)
            items.append({"date": cur.snap_date.isoformat(), "pnl": cur.total - prev.total - f})
    return {"items": items}


class ManualAssetIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=40)
    value: int = Field(ge=0)


@router.post("/manual-assets", status_code=201)
def create_manual(body: ManualAssetIn, user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> dict:
    m = ManualAsset(user_id=user_id, name=body.name, category=body.category, value=body.value)
    session.add(m)
    session.commit()
    return {"id": m.id}


@router.patch("/manual-assets/{mid}")
def update_manual(mid: int, body: ManualAssetIn, user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> dict:
    m = session.get(ManualAsset, mid)
    if m is None or m.user_id != user_id:
        raise HTTPException(status_code=404, detail="not found")
    m.name, m.category, m.value = body.name, body.category, body.value
    session.commit()
    return {"id": m.id}


@router.delete("/manual-assets/{mid}")
def delete_manual(mid: int, user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> dict:
    m = session.get(ManualAsset, mid)
    if m is None or m.user_id != user_id:
        raise HTTPException(status_code=404, detail="not found")
    session.delete(m)
    session.commit()
    return {"deleted": mid}
