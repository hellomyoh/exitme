"""LTV — Leveraged Trend with Volatility sizing (미국 지수 추세 자산용 신규 매매 공식, 2026-09-06 연구).

대상: 나스닥100(QQQ 1배 + QLD 2배 / TQQQ 3배), S&P500(국내 상장 KODEX 미국S&P500 379800 + TIGER 미국S&P500레버리지 225040).
RAVG(그리드 왕복)는 장기 우상향·얕은 조정 자산에서 거래만 늘어 기각(us-backtest-20260831). 문헌 근거:
- 추세 게이트: 200일선/10개월선 위에서만 위험 자산 보유 — Faber(2007), Gayed·Bilello(2016, Dow Award)
- 레버리지 회전: 장기선 위에서만 레버리지, 아래서는 현금 — Gayed·Bilello LRS (1928~2015, 1.25x/2x/3x 전부 B&H 우위)
- 변동성 사이징: 노출 ∝ 목표σ/실현σ — Moreira·Muir(2017), Harvey 외(2018, 주식에서 샤프 개선·꼬리 축소)
- 레버리지 ETF 감가: 일일 리밸런스 드래그 ≈ L(L−1)/2·σ² — Cheng·Madhavan(2009) → 고변동 구간에서 레버리지 축소가 이론적으로도 옳다

규칙(일 1회, 종가 판정 → 다음 거래일 시가 체결):
1) 추세: 종가 > MA·(1+entry_buffer) 면 ON, 보유 중 종가 < MA·(1−exit_buffer) 면 OFF (히스테리시스). OFF 는 전량 현금.
2) 노출 E (ON 일 때): sigma_target 이 있으면 E = clip(sigma_target/σ_n, e_floor, e_max), 없으면 E = e_max(고정 레버리지).
   sigma_cap 이상 고변동이면 E ≤ 1.0. mom_filter 가 있고 그 기간 수익률 ≤ 0 이면 E ≤ 1.0 (레버리지는 모멘텀 확인 시에만).
3) 실행: E ≤ 1 → 1배 비중 E, 현금 1−E. E > 1 → 1배 w1 = 1 − (E−1)/(L−1), 레버리지(L배) wL = (E−1)/(L−1) (전액 투자, 실효 노출 E).
4) 리밸런스 밴드: |E_target − E_now| < band·max(E_target, 0.5) 면 거래 생략(왕복 비용 억제). 추세 전환은 즉시.
결과는 RAVG/TF 와 같은 compute_kpi 규약으로 낸다(시뮬레이터 비교 가능).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from app.strategy.backtest import ClosedTrade, compute_kpi


@dataclass(frozen=True)
class LTVParams:
    ma_len: int = 200
    entry_buffer: float = 0.0
    exit_buffer: float = 0.02
    sigma_window: int = 20
    sigma_target: float | None = 0.20   # None = 고정 레버리지(Gayed LRS)
    e_max: float = 2.0
    e_floor: float = 1.0                # ON 구간 최소 노출 (0 = 완전 변동성 스케일)
    sigma_cap: float | None = None      # σ 연율 임계 — 이상이면 레버리지 금지
    mom_filter: int | None = None       # 거래일 수 (예 252) — 이 기간 수익률 ≤ 0 이면 레버리지 금지
    band: float = 0.10
    # ── 2차 개선(2026-09-06): 레버리지 전용 빠른 게이트 — 보유 게이트(MA200)는 그대로 두고 E>1 만 더 빨리 끊는다
    lev_gate_ma: int | None = None      # 종가 < SMA(n) 이면 E ≤ 1 (예 50)
    lev_gate_dd: float | None = None    # 종가 < (1−dd)×최근 dd_window 일 최고 종가면 E ≤ 1 (예 0.05)
    dd_window: int = 60
    fast_sigma: int | None = None       # σ = max(σ_window, σ_fast) — 급변 시 빠른 축소 (예 10)
    # ── 3차 개선(2026-09-06): 요동 없이 급락에만 반응하는 장치
    shock_drop: float | None = None     # 1배 종가 일간 하락률이 이 값 이상이면 (예 0.03) 레버리지 차단 시작
    shock_days: int = 20                # 차단 유지 거래일 수 (그동안 E ≤ 1)
    ewma_lambda: float | None = None    # RiskMetrics EWMA 분산(λ, 예 0.94) — 단순 σ_window 대신 사용
    # ── 4차 개선(2026-09-06): 요동 구간(추세선 근처 왕복)에서만 레버리지를 끄는 조건
    lev_gate_dist: float | None = None  # 종가가 MA×(1+dist) 이상일 때만 E>1 (예 0.03)
    lev_min_age: int | None = None      # 추세 ON 판정 후 이 거래일 수가 지나야 E>1 (예 60)
    lev_multiple: float = 2.0           # 레버리지 ETF 배수
    commission: float = 0.001           # 편도
    slippage: float = 0.0005            # 시장가 체결 슬리피지(편도)
    fee_1x: float = 0.002               # 연 보수
    fee_lev: float = 0.0095
    monthly_only: bool = False          # 월말에만 판정 (Faber)


@dataclass
class LTVResult:
    dates: list[str]
    equity: list[float]
    exposure: list[float]        # 실효 노출 E (일별)
    regime_on: list[bool]
    trades: list[ClosedTrade]
    kpi: dict
    flips: int                   # 추세 ON/OFF 전환 횟수
    rebalances: int              # 체결이 발생한 날 수
    turnover: float              # 연평균 회전율 (거래대금 / 평균 자산)
    time_in_market: float        # ON 비율
    avg_exposure: float
    label: str = ""


def _sma(xs: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    acc = 0.0
    for i, x in enumerate(xs):
        acc += x
        if i >= n:
            acc -= xs[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def _realized_vol(closes: list[float], n: int) -> list[float | None]:
    """연율화 실현 변동성 (로그수익률 n일 표본표준편차 × √252)."""
    out: list[float | None] = [None] * len(closes)
    rets = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    for i in range(n, len(closes)):
        w = rets[i - n + 1:i + 1]
        mu = sum(w) / n
        var = sum((r - mu) ** 2 for r in w) / (n - 1)
        out[i] = math.sqrt(var) * math.sqrt(252)
    return out


def _ewma_vol(closes: list[float], lam: float, warmup: int) -> list[float | None]:
    """RiskMetrics EWMA 변동성 — σ²_t = λσ²_{t−1} + (1−λ)r²_t, 연율화. 단순 이동창보다 급변에 빨리 반응하고 서서히 식는다."""
    out: list[float | None] = [None] * len(closes)
    var = None
    for i in range(1, len(closes)):
        r = math.log(closes[i] / closes[i - 1])
        var = r * r if var is None else lam * var + (1 - lam) * r * r
        if i >= warmup:
            out[i] = math.sqrt(var * 252)
    return out


def drawdown_episodes(dates: list[str], equity: list[float], top: int = 5) -> list[dict]:
    """최대 낙폭 에피소드 — (고점일, 저점일, 낙폭, 회복일) 상위 top 개. 개선 대상 구간을 짚는 진단용."""
    eps: list[dict] = []
    peak, peak_i, trough, trough_i = equity[0], 0, equity[0], 0
    in_dd = False
    for i, v in enumerate(equity):
        if v >= peak:
            if in_dd:
                eps.append({"peak": dates[peak_i], "trough": dates[trough_i], "depth": trough / peak - 1.0,
                            "recovered": dates[i], "days": i - peak_i})
                in_dd = False
            peak, peak_i, trough, trough_i = v, i, v, i
        else:
            in_dd = True
            if v < trough:
                trough, trough_i = v, i
    if in_dd:
        eps.append({"peak": dates[peak_i], "trough": dates[trough_i], "depth": trough / peak - 1.0,
                    "recovered": None, "days": len(equity) - 1 - peak_i})
    eps.sort(key=lambda e: e["depth"])
    return eps[:top]


def run_ltv(bars_1x: list[dict], bars_lev: list[dict] | None, capital: float, p: LTVParams,
            start_index: int | None = None, label: str = "") -> LTVResult:
    """bars_*: [{date, open, close}] 날짜 정렬·교집합 정렬 상태. bars_lev 가 None 이면 e_max 는 1.0 으로 강제."""
    if bars_lev is None:
        p = replace(p, e_max=min(p.e_max, 1.0))
    n = len(bars_1x)
    dates = [b["date"] for b in bars_1x]
    c1 = [float(b["close"]) for b in bars_1x]
    o1 = [float(b["open"]) for b in bars_1x]
    cL = [float(b["close"]) for b in bars_lev] if bars_lev else [0.0] * n
    oL = [float(b["open"]) for b in bars_lev] if bars_lev else [0.0] * n
    ma = _sma(c1, p.ma_len)
    sig = _ewma_vol(c1, p.ewma_lambda, p.sigma_window) if p.ewma_lambda else _realized_vol(c1, p.sigma_window)
    if p.fast_sigma:
        fast = _realized_vol(c1, p.fast_sigma)
        sig = [max(a, b) if (a is not None and b is not None) else a for a, b in zip(sig, fast)]
    gate_ma = _sma(c1, p.lev_gate_ma) if p.lev_gate_ma else None
    roll_max: list[float | None] = [None] * n
    if p.lev_gate_dd is not None:
        for i in range(n):
            if i + 1 >= p.dd_window:
                roll_max[i] = max(c1[i + 1 - p.dd_window:i + 1])
    L = p.lev_multiple
    shock_until = -1                  # 이 인덱스까지 레버리지 차단
    on_since = -1                     # 추세 ON 으로 바뀐 인덱스 (연령 계산)

    cash = float(capital)
    q1 = qL = 0.0                     # 소수 주 허용(비교 실험용) — 실전은 정수화
    cost1 = costL = 0.0               # 평균 매입가(수수료 포함)
    on = False
    e_now = 0.0
    pending: tuple[float, float] | None = None   # 목표 (w1, wL) — 다음 시가에 체결
    flips = rebalances = 0
    traded_notional = 0.0
    trades: list[ClosedTrade] = []
    out_dates: list[str] = []
    equity: list[float] = []
    exposure: list[float] = []
    regime_on: list[bool] = []
    first = start_index if start_index is not None else 0
    active_start: int | None = None

    def month_end(i: int) -> bool:
        return i + 1 >= n or dates[i][:7] != dates[i + 1][:7]

    for i in range(first, n - 1):
        nxt = i + 1
        # ① 전일 계획 체결 (다음 시가, 시장가)
        if pending is not None:
            w1, wL = pending
            v = cash + q1 * o1[nxt] + qL * oL[nxt]
            t1 = w1 * v / o1[nxt]
            tL = (wL * v / oL[nxt]) if (bars_lev and wL > 0) else 0.0
            for leg, q_now, q_tgt, px, cost in (("1x", q1, t1, o1[nxt], cost1), ("lev", qL, tL, oL[nxt], costL)):
                dq = q_tgt - q_now
                if abs(dq) * px < 1e-9:
                    continue
                if dq > 0:
                    fill = px * (1 + p.slippage)
                    cash -= dq * fill * (1 + p.commission)
                    new_cost = (q_now * cost + dq * fill * (1 + p.commission)) / (q_now + dq)
                    if leg == "1x":
                        q1, cost1 = q_now + dq, new_cost
                    else:
                        qL, costL = q_now + dq, new_cost
                else:
                    sell = -dq
                    fill = px * (1 - p.slippage)
                    proceeds = sell * fill * (1 - p.commission)
                    cash += proceeds
                    pnl = proceeds - sell * cost
                    trades.append(ClosedTrade(leg, "ltv", int(round(sell)), cost, fill, i, nxt, pnl))
                    if leg == "1x":
                        q1 = q_now - sell
                    else:
                        qL = q_now - sell
                traded_notional += abs(dq) * px
            rebalances += 1
            pending = None
        # ② 보수 일할
        if q1 > 0:
            cash -= q1 * c1[nxt] * p.fee_1x / 365.0
        if qL > 0:
            cash -= qL * cL[nxt] * p.fee_lev / 365.0
        # ③ 종가 판정
        m = ma[nxt]
        s = sig[nxt]
        v = cash + q1 * c1[nxt] + qL * cL[nxt]
        e_now = ((q1 * c1[nxt] + qL * cL[nxt] * L) / v) if v > 0 else 0.0
        if m is None or s is None or (p.mom_filter and nxt < p.mom_filter):
            out_dates.append(dates[nxt]); equity.append(v); exposure.append(e_now); regime_on.append(False)
            continue
        if active_start is None:
            active_start = len(equity)
        judge = (not p.monthly_only) or month_end(nxt)
        c = c1[nxt]
        if p.shock_drop is not None and c1[nxt] / c1[nxt - 1] - 1.0 <= -p.shock_drop:
            shock_until = max(shock_until, nxt + p.shock_days)   # 급락일 → 레버리지 차단 연장
        if judge:
            was_on = on
            if not on and c > m * (1 + p.entry_buffer):
                on = True
            elif on and c < m * (1 - p.exit_buffer):
                on = False
            if on != was_on:
                flips += 1
                if on:
                    on_since = nxt
            if not on:
                e_t = 0.0
            else:
                e_t = p.e_max if p.sigma_target is None else max(p.e_floor, min(p.e_max, p.sigma_target / max(s, 1e-9)))
                if p.sigma_cap is not None and s >= p.sigma_cap:
                    e_t = min(e_t, 1.0)
                if p.mom_filter and c1[nxt] / c1[nxt - p.mom_filter] - 1.0 <= 0:
                    e_t = min(e_t, 1.0)
                if gate_ma is not None and (gate_ma[nxt] is None or c < gate_ma[nxt]):
                    e_t = min(e_t, 1.0)   # 레버리지 게이트: 단기선 아래면 1배까지만
                if p.lev_gate_dd is not None and (roll_max[nxt] is None or c < roll_max[nxt] * (1 - p.lev_gate_dd)):
                    e_t = min(e_t, 1.0)   # 레버리지 게이트: 최근 고점 대비 dd 이상 밀렸으면 1배까지만
                if nxt <= shock_until:
                    e_t = min(e_t, 1.0)   # 급락 서킷브레이커: 차단 기간엔 1배까지만
                if p.lev_gate_dist is not None and c < m * (1 + p.lev_gate_dist):
                    e_t = min(e_t, 1.0)   # 추세선 근처(요동 구간)에서는 1배까지만
                if p.lev_min_age is not None and (on_since < 0 or nxt - on_since < p.lev_min_age):
                    e_t = min(e_t, 1.0)   # 상승 판정이 충분히 오래되지 않았으면 1배까지만
                e_t = min(e_t, L if bars_lev else 1.0)
            flip_now = (on != was_on)
            if flip_now or abs(e_t - e_now) >= p.band * max(e_t, 0.5):
                if e_t <= 1.0:
                    pending = (e_t, 0.0)
                else:
                    wL = (e_t - 1.0) / (L - 1.0)
                    pending = (1.0 - wL, wL)
        out_dates.append(dates[nxt]); equity.append(v); exposure.append(e_now); regime_on.append(on)

    act = active_start or 0
    eq = equity[act:]
    kpi = compute_kpi(eq, eq[0] if eq else capital, trades)
    years = max(len(eq) / 252.0, 1e-9)
    avg_eq = (sum(eq) / len(eq)) if eq else capital
    on_days = sum(1 for r in regime_on[act:] if r)
    return LTVResult(out_dates, equity, exposure, regime_on, trades, kpi, flips, rebalances,
                     turnover=traded_notional / avg_eq / years,
                     time_in_market=on_days / max(len(eq), 1),
                     avg_exposure=(sum(exposure[act:]) / max(len(eq), 1)), label=label)


def buy_and_hold(bars: list[dict], capital: float, fee_annual: float = 0.002, start_index: int = 0) -> LTVResult:
    """비교용 매수보유 — 첫날 시가 진입, 보수 일할."""
    c = [float(b["close"]) for b in bars]
    o = [float(b["open"]) for b in bars]
    q = capital / o[start_index + 1] if start_index + 1 < len(bars) else 0.0
    cash = 0.0
    eq, dates = [], []
    for i in range(start_index + 1, len(bars)):
        cash -= q * c[i] * fee_annual / 365.0
        eq.append(cash + q * c[i]); dates.append(bars[i]["date"])
    kpi = compute_kpi(eq, eq[0] if eq else capital, [])
    return LTVResult(dates, eq, [1.0] * len(eq), [True] * len(eq), [], kpi, 0, 1, 0.0, 1.0, 1.0, label="B&H")
