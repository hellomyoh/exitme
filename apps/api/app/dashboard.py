"""대시보드 API — 스냅샷·추이·캘린더·기타 자산 (feature-dashboard §5·§8)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
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

    jas = journal_assets(session, user_id)
    journal = sum(ja["value"] for ja in jas if ja["counted"])  # 평가액(시세 없으면 원가)
    # 일지별 스냅샷 (0028, 2026-09-12) — 자산 추이에서 일지를 하나씩 선으로 그리기 위한 원천.
    # 사용자 합계(snap.journal)는 그대로 두고 같은 값의 구성 요소를 일지 단위로 남긴다.
    from app.models import JournalSnapshot

    for ja in jas:
        jstmt = pg_insert(JournalSnapshot.__table__).values(
            journal_id=ja["id"], snap_date=snap_date, value=ja["value"], cost=ja["cost"],
            counted=bool(ja["counted"]), approx=False)
        jstmt = jstmt.on_conflict_do_update(
            constraint="uq_journal_snapshots_jid_date",
            set_={"value": jstmt.excluded.value, "cost": jstmt.excluded.cost,
                  "counted": jstmt.excluded.counted, "approx": jstmt.excluded.approx})
        session.execute(jstmt)
    snap = session.scalar(select(AssetSnapshot).where(
        AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date == snap_date))
    if snap is None:
        snap = AssetSnapshot(user_id=user_id, snap_date=snap_date, total=0, stock=0, cash=0, other=0)
        session.add(snap)
    snap.stock, snap.cash, snap.other, snap.journal = kr_stock, kr_cash, other, journal
    snap.total = kr_stock + kr_cash + other + journal  # 불변식: Σ(KRW 포트 equity) + other + journal == total
    session.flush()
    return snap


def _live_price_overrides(session: Session, inst_ids: set[int]) -> tuple[dict[int, float], str | None]:
    """10초 폴링 캐시의 현재가 — {instrument_id: price}, 표본 시각. 캐시가 없으면 빈 dict (표시 전용)."""
    from app.models import Instrument
    from app.quotes import live_quotes

    if not inst_ids:
        return {}, None
    code_of = {i: (session.get(Instrument, i).code if session.get(Instrument, i) else None) for i in inst_ids}
    live = live_quotes([c for c in code_of.values() if c])
    if not live:
        return {}, None
    out = {i: float(live[c]["price"]) for i, c in code_of.items() if c in live and live[c].get("price")}
    at = max((v.get("as_of") or "") for v in live.values()) or None
    return out, at


def _prev_close_map_by_id(session: Session, inst_ids: set[int], before: date) -> dict[int, float]:
    """before(미포함) 이전 마지막 종가 — 오늘 손익의 기준값. 오늘 상장·오늘 첫 매수 종목은 값이 없다."""
    from app.portfolios import prev_close_before

    out: dict[int, float] = {}
    for iid in inst_ids:
        pc = prev_close_before(session, iid, before)
        if pc:
            out[iid] = float(pc)
    return out


def last_two_closes(session: Session, inst_ids: set[int]) -> dict[int, list[tuple[date, float]]]:
    """종목별 마지막 두 종가 [(날짜, 종가), …] 최신순 — '오늘 손익'의 두 기준점."""
    from app.models import OhlcvDaily

    out: dict[int, list[tuple[date, float]]] = {}
    for iid in inst_ids:
        rows = session.execute(
            select(OhlcvDaily.trade_date, OhlcvDaily.close_raw, OhlcvDaily.adj_factor)
            .where(OhlcvDaily.instrument_id == iid)
            .order_by(OhlcvDaily.trade_date.desc()).limit(2)
        ).all()
        out[iid] = [(d, c * float(a)) for d, c, a in rows]
    return out


def day_change_basis(session: Session, inst_ids: set[int], live_px: dict[int, float],
                     today: date | None = None) -> tuple[dict[int, float], dict[int, float], date | None]:
    """'오늘 손익'의 (현재가, 비교 기준가, 기준일) — 기준은 **지금 쓰는 가격 바로 직전 값** (2026-09-12 지시).

    - 실시간 시세가 있으면(장중·애프터마켓): 현재가 = 실시간, 기준 = **오늘 이전** 마지막 종가 → 오늘 하루의 변동.
      (오늘 정규장 종가가 이미 적재된 뒤에도 애프터마켓 시세가 흐르므로, 오늘 종가를 기준으로 삼으면 장 마감 후 변동만 보인다.)
    - 없으면(주말·공휴일·장 시작 전·일봉 적재 지연): 현재가 = 마지막 종가, 기준 = 그 직전 종가
      → **마지막 거래일의 변동**을 보여준다. 기준일 = 그 마지막 봉 날짜(화면에 표기해 오늘 것으로 오해하지 않게).

    종전에는 둘 다 "오늘 이전 마지막 종가"라 새 봉이 없는 날에는 같은 값이 되어 항상 0 이었다.
    종목마다 마지막 봉 날짜가 달라도(한국·미국 마감 시각 차이) 종목 단위로 두 봉을 보므로 자동으로 맞는다.
    """
    bars = last_two_closes(session, inst_ids)
    now_px: dict[int, float] = {}
    prev_px: dict[int, float] = {}
    stale_dates: list[date] = []
    for iid, rows in bars.items():
        if not rows:
            continue
        d0, c0 = rows[0]
        if iid in live_px:
            now_px[iid] = live_px[iid]
            # 오늘 이전의 마지막 종가 — 오늘 봉이 이미 있으면 그 앞 봉
            base = rows[1] if d0 >= (today or kst_today()) and len(rows) > 1 else rows[0]
            if base[0] < (today or kst_today()):
                prev_px[iid] = base[1]
        else:
            now_px[iid] = c0
            if len(rows) > 1:
                prev_px[iid] = rows[1][1]
                stale_dates.append(d0)
    # 기준일은 '오늘이 아닐 때'만 돌려준다 — 장 마감 후 오늘 봉이 있으면 그건 오늘의 변동이라 표기가 필요 없다
    as_of = max(stale_dates) if stale_dates and not live_px else None
    if as_of is not None and as_of >= (today or kst_today()):
        as_of = None
    return now_px, prev_px, as_of


def latest_bar_day(session: Session, inst_ids: set[int]) -> date | None:
    """보유 종목의 최신 일봉 날짜 — 총자산 '오늘 손익'의 기준일 (없으면 None)."""
    from app.models import OhlcvDaily

    if not inst_ids:
        return None
    return session.scalar(select(func.max(OhlcvDaily.trade_date))
                          .where(OhlcvDaily.instrument_id.in_(inst_ids)))


def live_kr_stock(session: Session, user_id: int) -> tuple[str | None, int]:
    """10초 폴링 캐시로 평가한 국내 포트 주식 평가액 합 — 캐시가 하나도 없으면 (None, 0).

    **표시 전용** (2026-09-10 지시). 스냅샷(asset_snapshots·portfolio_snapshots) 적재는 종가 기준을 그대로 두어야
    추이·전일 대비의 기준이 흔들리지 않는다 (2026-09-09 반쪽 스냅샷 사고, NOTES).
    """
    from app.models import Instrument, PositionLot
    from app.quotes import live_quotes

    pfs = session.scalars(select(TradePortfolio).where(
        TradePortfolio.user_id == user_id, TradePortfolio.market == "KR")).all()
    if not pfs:
        return None, 0
    pids = [p.id for p in pfs]
    inst_ids = {l.instrument_id for l in session.scalars(
        select(PositionLot).where(PositionLot.portfolio_id.in_(pids))).all() if l.qty_open > 0}
    if not inst_ids:
        return None, 0
    code_of = {i: (session.get(Instrument, i).code if session.get(Instrument, i) else None) for i in inst_ids}
    live = live_quotes([c for c in code_of.values() if c])
    if not live:
        return None, 0
    prices = dict(latest_closes(session, inst_ids))
    for iid, code in code_of.items():
        if code and code in live:
            prices[iid] = float(live[code]["price"])
    total = sum(_portfolio_state(session, pf.id, prices)[0] for pf in pfs)
    at = max((str(v.get("as_of") or "") for v in live.values()), default="")
    return (at or None), int(total)


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
    # 실시간 표시 (2026-09-10 지시) — 10초 폴링 캐시가 있으면 화면 숫자를 현재가로 바꾼다. 적재된 스냅샷은 종가 그대로,
    # 전일 대비도 같은 총액(total_now)으로 계산해 "표시된 총자산 − 어제 종가 총액"이 화면과 어긋나지 않게 한다
    live_at, live_stock = live_kr_stock(session, user_id)
    total_now = (live_stock + snap.cash + snap.other + (snap.journal or 0)) if live_at else snap.total
    stock_now = live_stock if live_at else snap.stock
    # 전일 대비·누적 손익은 외부 입출금을 차감한 순수 성과 (단순 Dietz, 검증 C-3·M-6)
    #
    # 기준일 (2026-09-12 지시): 오늘 봉이 아직 없으면(주말·공휴일·장 시작 전·일봉 적재 지연) 오늘 스냅샷은
    # 마지막 종가로 평가돼 어제 스냅샷과 같아지고 '오늘 0원' 이 된다. 그럴 때는 **마지막 봉 날짜(base_day)의
    # 스냅샷 vs 그 직전 스냅샷** 을 비교해 그날의 실제 변동을 보여주고, 화면에 기준일을 표기한다.
    from app.models import Instrument, ManualJournal, ManualJournalEntry, PositionLot

    user_inst = set(session.scalars(select(PositionLot.instrument_id).where(
        PositionLot.portfolio_id.in_(select(TradePortfolio.id).where(TradePortfolio.user_id == user_id)))).all())
    # 매매일지에만 보유가 있는 사용자도 기준일을 잡을 수 있게 일지 종목코드도 포함 (2026-09-12)
    jcodes = set(session.scalars(select(ManualJournalEntry.code).where(
        ManualJournalEntry.code.is_not(None),
        ManualJournalEntry.journal_id.in_(select(ManualJournal.id).where(ManualJournal.user_id == user_id)))).all())
    if jcodes:
        user_inst |= set(session.scalars(select(Instrument.id).where(Instrument.code.in_(jcodes))).all())
    base_day = latest_bar_day(session, user_inst)
    change = 0
    change_pct = None
    change_asof = None
    cur_total, cur_prev = total_now, prev
    if not live_at and base_day is not None and base_day < today:
        base_snap = session.scalar(select(AssetSnapshot).where(
            AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date == base_day))
        if base_snap is not None:
            older = session.scalars(select(AssetSnapshot).where(
                AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date < base_day)
                .order_by(AssetSnapshot.snap_date.desc()).limit(1)).first()
            if older is not None:
                cur_total, cur_prev, change_asof = int(base_snap.total), older, base_day
    if cur_prev:
        end_day = change_asof or today
        f = user_flows_between(session, user_id, cur_prev.snap_date, end_day)
        change = cur_total - cur_prev.total - f
        denom = cur_prev.total + f
        change_pct = change / denom if denom > 0 else None
    since_pct = None
    since_amount = None      # 누적 금액도 함께 (2026-09-10 지시) — 카드에 %만 있어 크기를 알 수 없었다
    if first and first.snap_date < today:
        f_all = user_flows_between(session, user_id, first.snap_date, today)
        denom = first.total + f_all
        since_amount = total_now - first.total - f_all
        since_pct = (since_amount / denom) if denom > 0 else None
    # 자산 내용 카드 — KR/US 구분 (feature-dashboard §5, 2026-09-02). US 는 센트, $ 표기는 웹 담당.
    from app.models import PositionLot

    def _market_breakdown(market: str) -> dict:
        """시장 카드 — 누적(보유 원가 대비)과 **오늘**(현재가 − 전일 종가, 전일 종가 평가액 대비)을 함께 (2026-09-10 지시).

        계좌 표와 같은 규칙: 현재가는 10초 폴링 캐시 우선·없으면 종가, 전일 종가가 없는 종목(오늘 신규 매수)은 오늘에서 뺀다.
        """
        pfs = session.scalars(select(TradePortfolio).where(
            TradePortfolio.user_id == user_id, TradePortfolio.market == market)).all()
        pf_ids = [p.id for p in pfs]
        inst_ids = set(session.scalars(select(PositionLot.instrument_id).where(
            PositionLot.portfolio_id.in_(pf_ids))).all()) if pf_ids else set()
        live_px, _at = _live_price_overrides(session, inst_ids)
        # 오늘 손익의 두 기준점 (2026-09-12) — 장중이면 실시간 vs 마지막 종가, 아니면 마지막 종가 vs 그 직전 종가
        now_px, prev_px, day_asof = day_change_basis(session, inst_ids, live_px)
        prices = {**latest_closes(session, inst_ids), **live_px}
        value = cost = 0
        for p in pfs:
            s, _c, co = _portfolio_state(session, p.id, prices)
            value += s
            cost += co
        day_change = prev_eval = 0.0
        qty_by_inst: dict[int, int] = {}
        for l in session.scalars(select(PositionLot).where(PositionLot.portfolio_id.in_(pf_ids or [0]))).all():
            if l.qty_open > 0:
                qty_by_inst[l.instrument_id] = qty_by_inst.get(l.instrument_id, 0) + l.qty_open
        for iid, qty in qty_by_inst.items():
            pc, cur = prev_px.get(iid), now_px.get(iid)
            if pc and cur:
                day_change += qty * (cur - pc)
                prev_eval += qty * pc
        pnl = value - cost
        return {"value": value, "cost": cost, "pnl": pnl,
                "pnl_pct": (pnl / cost) if cost > 0 else None,
                "day_change": round(day_change) if prev_eval > 0 else None,
                "day_change_pct": (day_change / prev_eval) if prev_eval > 0 else None,
                # None = 오늘(장중·오늘 봉 있음), 날짜 = 그날 종가 기준 변동 (주말·휴장·장 시작 전·적재 지연)
                "day_change_asof": day_asof.isoformat() if day_asof else None}

    # 포트별 분리 표기 (2026-09-02 지시) — 진행 중 실전매매 각각의 평가액·평가손익
    port_rows = []
    all_pfs = session.scalars(select(TradePortfolio).where(TradePortfolio.user_id == user_id)
                              .order_by(TradePortfolio.id)).all()
    all_inst = set(session.scalars(select(PositionLot.instrument_id).where(
        PositionLot.portfolio_id.in_([p.id for p in all_pfs]))).all()) if all_pfs else set()
    all_prices = latest_closes(session, all_inst)
    # 표시용 현재가 — 10초 폴링 캐시가 있으면 그것, 없으면 종가 (총자산 카드와 같은 기준, 2026-09-10).
    # 적재 스냅샷은 종가 그대로 (live_kr_stock 도큐스트링 참조).
    live_px, live_row_at = _live_price_overrides(session, all_inst)
    prices_now = {**all_prices, **live_px}
    # 오늘 손익의 기준 (2026-09-12) — 장중이면 마지막 종가, 아니면 마지막 종가의 직전 종가(= 그날의 변동)
    row_now_px, prev_px, row_day_asof = day_change_basis(session, all_inst, live_px)
    for pfr in all_pfs:
        stock_v, cash_v, cost_v = _portfolio_state(session, pfr.id, prices_now)
        if stock_v == 0 and cash_v == 0 and cost_v == 0:
            continue  # 활동 없는 빈 포트(기본 계좌 등)는 표기 생략
        pnl = stock_v - cost_v
        # 계좌별 도넛용 종목 구성 (2026-09-05 지시) — 종목별 수량·평가액
        from app.models import Instrument, PositionLot
        pos: dict[int, dict] = {}
        for l in session.scalars(select(PositionLot).where(PositionLot.portfolio_id == pfr.id)).all():
            it = pos.setdefault(l.instrument_id, {"qty": 0, "value": 0.0})
            it["qty"] += l.qty_open
            it["value"] += l.qty_open * prices_now.get(l.instrument_id, l.price)
        positions = []
        # 오늘 손익 (2026-09-10 지시) — 누적(원가 대비)과 달리 **전일 종가 평가액 대비**. 전일 종가가 없는 종목
        # (오늘 신규 매수·신규 상장)은 빼고 이름을 알린다 — 매입가와 비교하면 누적과 같아져 뜻이 흐려진다.
        day_change = prev_eval = 0.0
        day_missing: list[str] = []
        for iid, it in pos.items():
            if it["qty"] <= 0:
                continue
            inst = session.get(Instrument, iid)
            positions.append({"code": inst.code, "name": inst.name,
                              "qty": it["qty"], "value": round(it["value"])})
            pc, cur = prev_px.get(iid), row_now_px.get(iid)
            if pc and cur:
                day_change += it["qty"] * (cur - pc)
                prev_eval += it["qty"] * pc
            else:
                day_missing.append(inst.name)
        port_rows.append({
            "id": pfr.id, "name": pfr.name, "market": pfr.market,
            "equity": round(stock_v) + cash_v, "stock_value": round(stock_v), "cash": cash_v,
            "pnl": round(pnl), "pnl_pct": (pnl / cost_v) if cost_v > 0 else None,
            # 셀 수 있는 보유가 없으면(현금만·전일 종가 없음) 0 이 아니라 값 없음 — 화면은 '—'
            "day_change": round(day_change) if prev_eval > 0 else None,
            "day_change_pct": (day_change / prev_eval) if prev_eval > 0 else None,
            "day_missing": day_missing, "price_source": "live" if live_px else "close",
            "day_change_asof": row_day_asof.isoformat() if row_day_asof else None,
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
        "total": total_now, "stock": stock_now, "cash": snap.cash, "other": snap.other,
        "live_at": live_at,   # 10초 폴링 시세로 평가한 시각 (없으면 종가 기준, 2026-09-10)
        # 주식 거래 자산(실전매매 KRW 주식+현금)과 매매일지 종합 자산을 분리 표기 (2026-09-05 지시)
        "trading_total": stock_now + snap.cash,
        "journal": snap.journal or 0,
        "journals": journals,
        "portfolios": port_rows,
        "total_trend": total_by_date,
        "kr_trend": [v for _d, v in sorted(kr_by_date.items())],
        "us_trend": [v for _d, v in sorted(us_by_date.items())],
        "change_amount": change,
        "change_pct": change_pct,
        # None = 오늘 기준, 날짜 = 그날 종가 기준 (주말·휴장·장 시작 전·적재 지연, 2026-09-12)
        "change_asof": change_asof.isoformat() if change_asof else None,
        "since_inception_pct": since_pct, "since_inception_amount": since_amount,
        "kr_stock": _market_breakdown("KR"),
        "us_stock": _market_breakdown("US"),  # 값 단위: 센트 (환율 미도입 — KRW 합산 제외)
        "manual_assets": [
            {"id": m.id, "name": m.name, "category": m.category, "value": m.value} for m in manuals
        ],
    }


@router.get("/dashboard/live")
def dashboard_live(user_id: int = Depends(current_user_id), session: Session = Depends(get_session)) -> dict:
    """총자산 실시간 표시용 최소 응답 (2026-09-10 지시) — 10초 주기 호출을 전제로 가볍게.

    `/dashboard` 와 달리 **방문 기록·스냅샷 적재를 하지 않는다**. 저장된 오늘 스냅샷의 현금·기타는 그대로 두고 국내 주식과
    매매일지만 10초 폴링 캐시의 현재가로 다시 평가한다. 캐시가 없으면 `live_at=None` 만 돌려주고 화면은 폴링을 멈춘다.
    """
    today = kst_today()
    snap = session.scalar(select(AssetSnapshot).where(
        AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date == today))
    if snap is None:
        return {"live_at": None}          # 아직 오늘 스냅샷 없음 — /dashboard 한 번 열면 만들어진다
    live_at, live_stock = live_kr_stock(session, user_id)
    if live_at is None:
        return {"live_at": None}          # 장외·휴장·시세 없음
    from app.mjournal import journal_assets

    journal = int(sum(ja["value"] for ja in journal_assets(session, user_id) if ja["counted"]))
    cash, other = int(snap.cash), int(snap.other)
    total = live_stock + cash + other + journal
    prev = session.scalars(select(AssetSnapshot).where(
        AssetSnapshot.user_id == user_id, AssetSnapshot.snap_date < today)
        .order_by(AssetSnapshot.snap_date.desc()).limit(1)).first()
    change, change_pct = 0, None
    if prev:
        f = user_flows_between(session, user_id, prev.snap_date, today)
        change = total - int(prev.total) - f
        denom = int(prev.total) + f
        change_pct = (change / denom) if denom > 0 else None
    return {"live_at": live_at, "total": total, "stock": live_stock, "cash": cash, "other": other,
            "journal": journal, "trading_total": live_stock + cash,
            "change_amount": change, "change_pct": change_pct}


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
            "currency": "KRW" if pf.market == "KR" else "USD", "kind": "portfolio",
            "points": [{"date": r.snap_date.isoformat(), "equity": r.equity} for r in ps],
        })

    # 매매일지 자산 추이 — 일지별 한 줄씩 (0028, 2026-09-12 지시). 일지 스냅샷이 하나도 없으면(적재 전)
    # 종전처럼 사용자 합계(AssetSnapshot.journal) 한 줄로 폴백한다.
    from app.models import JournalSnapshot, ManualJournal

    def _weekly(pts: list[dict]) -> list[dict]:
        if key != "ALL" or len(pts) <= 366:
            return pts
        wk: dict[tuple[int, int], dict] = {}
        for p in pts:
            wk[date.fromisoformat(p["date"]).isocalendar()[:2]] = p
        return sorted(wk.values(), key=lambda p: p["date"])

    jrs = session.scalars(select(ManualJournal).where(ManualJournal.user_id == user_id)
                          .order_by(ManualJournal.id)).all()
    any_journal_series = False
    for j in jrs:
        js = session.scalars(
            select(JournalSnapshot).where(JournalSnapshot.journal_id == j.id,
                                          JournalSnapshot.snap_date >= since)
            .order_by(JournalSnapshot.snap_date)).all()
        pts = _weekly([{"date": r.snap_date.isoformat(), "equity": int(r.value)} for r in js if r.counted])
        if len(pts) >= 2 and any(p["equity"] > 0 for p in pts):
            series.append({"portfolio_id": None, "journal_id": j.id, "name": j.name, "market": "KR",
                           "currency": "KRW", "kind": "journal", "points": pts,
                           # 소급 재계산분이 섞여 있으면 화면에 근사임을 알린다
                           "approx": any(r.approx for r in js)})
            any_journal_series = True
    if not any_journal_series:
        jpts = _weekly([{"date": r.snap_date.isoformat(), "equity": int(r.journal)}
                        for r in rows if r.journal is not None])
        if len(jpts) >= 2 and any(p["equity"] > 0 for p in jpts):
            series.append({"portfolio_id": None, "name": "매매일지", "market": "KR",
                           "currency": "KRW", "kind": "journal", "points": jpts})

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
