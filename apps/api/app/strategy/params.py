"""RAVG v2.5 파라미터 — 정본 trade_algorithm_final.md §10 + ADR-007 개정 3건 + feature-strategy-engine.md §5 확정값.

절제(ablation) 플래그 5종은 정본 §11 검증 계획 순서와 일치한다.
플래그 종속성(feature-backtest §5.4): f4 off → 레버리지 규칙 전체 비활성, f2 off → v1 총변동성 공식.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AblationFlags:
    f1_no_tp_in_bull: bool = True   # 상승장 익절 제거 (off → v1: 항상 1×Grid 익절)
    f2_downside_vol: bool = True    # 하방 변동성 (off → v1: 0.18/σ20)
    f3_fast_regime: bool = True     # MA20>MA60 판정 (off → v1: MA60 20일 기울기)
    f4_leverage: bool = True        # Emax 1.30 + w_LEV=E−1 (off → Emax 1.0, 레버리지 없음)
    f5_gap_filter: bool = True      # 갭 필터 + 잔여예산 규칙 (off → v1: 3단 전량 발주)


@dataclass(frozen=True)
class Params:
    # 노출 (정본 §5)
    target_downside_vol: float = 0.20  # 2026-08-31 사용자 승인 상향(0.13→0.20) — KR IS·OOS 동시 개선, t_OOS +3.02, MDD 불변
    target_total_vol_v1: float = 0.18
    blend_abs: float = 0.5
    sigma_down_floor: float = 0.03
    sigma_ref_window: int = 250
    emax_bull: float = 1.30
    emax_neutral: float = 0.65
    emax_bear: float = 0.20
    # 레짐 (정본 §4)
    regime_buffer: float = 0.02
    ma200_exit_buffer: float = 0.02  # 2026-08-31 승인 — BULL/BEAR 이탈의 MA200 다리 히스테리시스 (docs/regime-buffer-study-20260831.md, 3중 검증)
    slope_lookback_v1: int = 20
    # 그리드 (정본 §6)
    grid_coef: float = 0.75
    grid_min: float = 0.008
    # 2026-09-12 사용자 지시 4.0% → 0.025. 상한은 "고변동 + 그리드 가동" 국면에서만 물린다
    # (실측 바인딩 8.6년 중 49일, 전부 2026년). 시작일 89개 짝 비교에서 MDD 악화 0건·최악 +0.00%p,
    # 이득은 창 5개에 집중 — docs/grid-cap-study-20260912.md
    grid_max: float = 0.025
    grid_steps: int = 3
    # 그리드 예산 가중 — 균등 1/3 → 50/30/20 (2026-09-03 사용자 승인, docs/order-formula-study-20260903.md:
    # 실측 체결 확률 기준 기대 투입률 +35%, 10년 +14.7%p·샤프 0.98→1.00, MDD +1.0%p 교환).
    # 길이가 grid_steps 와 다르면 앞에서 grid_steps 개를 취해 합으로 정규화, 합이 0이면 균등 분배.
    grid_weights: tuple[float, ...] = (0.5, 0.3, 0.2)
    tick: int = 5              # ETF 호가 단위(원)
    cash_buffer: float = 0.005
    band: float = 0.05         # 리밸런싱 밴드 ±5%p — 그리드 신규매수에는 미적용
    gap_atr_mult: float = 1.5
    # 소량 진입 부트스트랩 (ADR-010, 2026-09-08 사용자 지시 "작은 수량으로 일단 시작한 뒤 정상 상태로") — 시작 후 boot_days 거래일 동안
    # K200 목표 미달분의 boot_frac 을 종가×(1−boot_delta·Grid) 지정가로 추가 매수(하락장은 ×boot_bear_mult), 그 날 그리드 예산은 (1−f).
    # docs/fast-entry-study-20260908.md: 234개 조합·3 표본 — 첫 체결 10일→1~2일, 250일 수익 차이 ≈ 0. boot_frac 0 또는 boot_days 0 = 끔
    boot_days: int = 10
    boot_frac: float = 0.15
    boot_delta: float = 0.0
    boot_bear_mult: float = 0.5
    # 연구용 (2026-09-09 사용자 제안 "이동평균선을 참고해 진입, 아주 소량") — 기본값은 현행과 동일. docs/boot-entry-price-study §5
    boot_price_ref: str = "close"   # close | ma{n} (지정가 = n일 이동평균, 종가 위면 시가 체결) | ma{n}min (지정가 = min(종가, MA n))
    boot_ma_filter: int = 0         # 0 | n — 종가 ≤ MA(n) 인 날만 초기 진입(눌림 매수), 그 외 날은 그리드만
    boot_otype: str = "limit"       # 연구용 (2026-09-09 사용자 지시 "시가로 들어가는 전략 시뮬레이션"): limit(지정가) | market(다음날 시가 시장가)
    boot_market_slippage: float = 0.0   # 연구용: 시가 시장가 매수의 체결가 가산율 (0 = 시가 그대로, 0.001 = +0.1%)
    # 레버리지 (정본 §7)
    lev_multiple: float = 2.0       # 레버리지 ETF 배율 — 국내 2배 기본, 해외 3배(TQQQ) 검토용 (2026-08-31)
    lev_strategic_ratio: float = 0.7
    lev_tact1_mult: float = 0.75
    lev_tact2_mult: float = 1.5
    sigma20_liquidate: float = 0.35  # 2026-08-31 사용자 승인 상향(25→35%) — 스윕: 10년 +140→+150%, MDD 불변, 위기 방어는 레짐 이탈이 선행
    # 워밍업·안전 (feature-strategy-engine §5.2·§5.3)
    min_history: int = 270
    # 비용 (feature-backtest §5.2)
    commission: float = 0.00015
    slippage_market: float = 0.001   # 시장가성 청산에만
    lev_tax: float = 0.154           # 실현차익 단순화 과세
    fee_200: float = 0.0015          # 연 보수 (일할 365)
    fee_lev: float = 0.0064
    flags: AblationFlags = field(default_factory=AblationFlags)


def round_tick(price: float, tick: int, up: bool) -> int:
    """호가 정규화 — 매수 내림 / 매도 올림 (feature-strategy-engine §5.5).

    이진 부동소수로 정확한 배수가 64,389.999…로 표현되는 하강을 방지하기 위해
    몫을 소수 9자리로 먼저 반올림한다 (2026-08-28 검증 D4).
    """
    import math

    q = round(price / tick, 9)
    return int((math.ceil(q) if up else math.floor(q)) * tick)
