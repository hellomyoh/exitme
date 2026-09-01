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
    grid_max: float = 0.04
    grid_steps: int = 3
    tick: int = 5              # ETF 호가 단위(원)
    cash_buffer: float = 0.005
    band: float = 0.05         # 리밸런싱 밴드 ±5%p — 그리드 신규매수에는 미적용
    gap_atr_mult: float = 1.5
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
