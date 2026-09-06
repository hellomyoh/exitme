# feature-us-ltm — 미국 지수 LTM(Leveraged Trend-Momentum) 매매 공식

- 상태: 구현 (2026-09-06 사용자 지시 "LTM 을 미국 주식 거래 매매공식으로 적용")
- 연구 근거: [docs/ltv-strategy-study-20260906.md](../docs/ltv-strategy-study-20260906.md) (문헌·4차 개선·민감도·라오어/VR 비교·KOSPI 대입)
- 관련: [feature-us-trendfilter.md](feature-us-trendfilter.md) (TF — 유지, 1배 보수형), [feature-backtest.md](feature-backtest.md), [feature-trading-signal.md](feature-trading-signal.md)

## 1. 범위와 결정

| 항목 | 결정 |
|---|---|
| 대상 | QQQ(1배) + QLD(2배) — 미국 기본 · QQQ + TQQQ(3배) |
| 시뮬레이터 미국 옵션 | `LTM_QLD`(기본), `LTM_TQQQ`, `QQQ_TF`(TF 1배). **RAVG 미국 쌍(`QQQ_QLD`/`QQQ_TQQQ`)은 삭제** — 신규 잡 422, 기존 기록은 라벨만 유지 |
| 한국 | 변경 없음 — RAVG v2.5 유지 (LTM 대입 결과 MDD −45% vs RAVG −21%, 연구 §10) |
| 실행 | 종가 판정 → 다음 거래일 시가 시장가(B안). 예약주문 연동은 국내 전용이라 미국은 주문표 표기까지 |

## 2. 규칙 (단일 구현: `apps/api/app/strategy/ltm.py` `ltm_states`)

1. **보유 게이트**(TF 와 동일): 종가 > MA200 → ON. 보유 중 종가 < MA200×(1−2%) → OFF. OFF 는 전량 현금.
2. **레버리지 허용** — ON 상태에서 아래 둘을 모두 만족하면 목표 노출 E=2.0, 아니면 1.0:
   - 12개월(252거래일) 수익률 > 0
   - 최근 20거래일 안에 1배 종가 일간 −3% 이상 급락일 없음 (급락일마다 20일 재연장)
3. **비중**: E ≤ 1 → 1배 비중 E. E > 1 → 레버리지 비중 wL=(E−1)/(L−1), 1배 1−wL. QLD: 100% QLD. TQQQ: QQQ 50% + TQQQ 50%.
4. **리밸런스 밴드**: |목표 E − 현재 E| < 목표의 10% 면 거래 없음. 추세 전환(진입/이탈)·레버리지 on/off 는 즉시.
   목표 수량은 평가액의 **99%** 로 산정한다(현금 여유 1%, `cash_reserve`) — 일할 보수·수수료로 현금이 음수가 되어 전환 포트에 음수 현금이 시드되던 결함(2026-09-06 e2e) 방지. 노출 상한은 실질 1.98.
   그래도 현금이 바닥나면 보수는 **이연(`fee_due`)** 되어 다음 매도 대금에서 정산한다 — 현금 곡선은 구조적으로 음수가 되지 않고, 평가액에는 미지급 보수가 차감 반영된다.
5. **비용 모델**(백테스트): 편도 수수료 0.1% + 시장가 슬리피지 0.05%, 보수 QQQ 0.20% / QLD 0.95% / TQQQ 0.84% 일할. 세전·환율 미모델(연구와 동일).
6. **레짐 표기**: 레버리지 ON = BULL(상승장), 1배 = NEUTRAL(중립), 현금 = BEAR(하락장).

워밍업: max(200, 252) = 252거래일. 시뮬레이터는 시작일 이전 선행 봉을 함께 로드하므로(`load_bars_with_warmup`) 1년 미만 구간도 거래가 나온다.

## 3. 구현 지도

| 층 | 위치 | 내용 |
|---|---|---|
| 엔진 | `app/strategy/ltm.py` | `LTMParams`, `ltm_states`(규칙), `target_weights`, `run_ltm_backtest` → `BacktestResult`(정수 주, plans/fills/cash·qty 곡선, final_lots) |
| 디스패치 | `app/backtests.py` | `ETF_PAIRS` LTM_QLD/LTM_TQQQ, `LTM_ETFS`, `BacktestIn.etf` 패턴, `run_engine`(잡·일지·전환·워커 공통) |
| 신호 | `app/signals.py` | `_us_portfolio_orders`(포트 `params.etf` 로 TF/LTM 분기) → `_ltm_portfolio_orders`(목표 E vs 현재 E, 시장가 델타, PortfolioPlan 스냅샷 `strategy: "LTM"`) |
| 챗봇 | `app/chat.py` `order_sheet` | 같은 디스패처 사용 — UI 와 동일 주문표 |
| 웹 | `simulator/page.tsx` | 미국 카드 3종(LTM_QLD 기본), 부제 "LTM · TF 백테스트", 구 쌍 숨김 |
| 웹 | `portfolio/page.tsx` | 주문 종류 라벨·설명(`ltm_entry/exit/lever_on/lever_off/rebal`), 레버리지 종목명 `name_lev`(QLD/TQQQ) |

주문 종류: `ltm_entry`(추세 진입) · `ltm_exit`(이탈→현금) · `ltm_lever_on`(노출 2.0) · `ltm_lever_off`(1배 복귀) · `ltm_rebal`(밴드 이탈 리밸런스).

## 4. 실전 주문표 계약

- 입력: 포트 원장(신호 기준일 종가 시점 로트·현금, `_state_before`), QQQ·레버리지 최신 종가, `ltm_states` 마지막 상태.
- 출력: `strategy: "LTM"`, `regime`, `e_target`, `w_200`/`w_lev`, `indicators{close, ma200, gap_to_ma200, exit_level, mom12, shock_days_left, exposure}`, `orders[]`(매도 먼저), `name_lev`.
- 워밍업 부족(QQQ 봉 < 252): `status: "INSUFFICIENT_HISTORY"`, 주문 없음.
- 레버리지 레그에 다른 종목이 섞여 있으면 같은 레그로 취급(청산·리밸런스 대상). 표기는 포트 공식의 레버리지 종목명.
- 계획 스냅샷은 TF/RAVG 와 같은 규칙: 실행일 전까지 갱신, 실행일 도래 후 불변.

## 5. 검증 (2026-09-06)

- 단위 `tests/test_ltm.py` — 비중 함수, 게이트(워밍업·모멘텀·급락 브레이커 20일), 상승장 레버리지 유지·결과 규약, TQQQ 50/50, 하락장 현금·보유 시작 시드.
- 통합 `tests/test_ltm_api.py` — 구 RAVG 쌍 422, LTM_QLD 잡 완주(kpi·일지 종류), 실전 전환 → `/signals/daily` 전략 LTM·`name_lev` QLD.
- 실데이터 대조(제품 엔진, 2026-09-06 로컬 DB, 자본 $1,000,000): 연구 엔진과 달리 정수 주·시장가 슬리피지 0.05% 를 모델해 소폭 낮다.

| 쌍 | 구간 | CAGR | MDD | Sharpe | 체결 수 | 연구 엔진(§3·§4) |
|---|---|---|---|---|---|---|
| QQQ+QLD | 2007-08-22 ~ 2026-09-04 | 18.7% | −40.5% | 0.80 | 183 | 20.0% / −40.7% / 0.80 |
| QQQ+TQQQ | 2010-03-02 ~ 2026-09-04 | 18.8% | −36.9% | 0.79 | 183 | 20.4% / −37.5% |

  QLD 쌍 시작이 2007-08 인 것은 QLD 상장(2006-06) 뒤 워밍업 252거래일이 필요하기 때문(연구는 워밍업을 구간 밖에서 취함).

## 6. 남은 일·주의

- 미국 예약주문 연동은 없음(KIS 국내 TR 전용). 주문표는 표기·기록까지.
- 알고리즘 설정(관리자) 변수는 RAVG 전용 — LTM 상수는 코드 고정(`LTMParams`). 변수화는 별도 지시 시.
- 기존 미국 TF 포트는 `params.etf`가 `QQQ_TF`(또는 없음)라 TF 로 그대로 동작한다.
