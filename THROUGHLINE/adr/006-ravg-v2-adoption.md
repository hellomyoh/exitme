# ADR-006: RAVG v2 전략 채택과 정본 규칙

## 상태

Accepted

## 배경

v1 알고리즘([SOURCES/basic_trade.md](../SOURCES/basic_trade.md))에 대한 검토의견이 버그 4건(그리드 익절의 추세 절단, 레버리지 노출한도 무력화, EMA20 회귀 매도, 레짐 판정 지연 50일)과 보완 4건을 제시했다.

## 선택지

1. v1 그대로 구현
2. 검토의견 전량 채택한 RAVG v2 채택

## 결정

2안. [SOURCES/trade_algorithm_final.md](../SOURCES/trade_algorithm_final.md)를 **전략 규칙의 정본**으로 채택한다.
문서 간 충돌 시 우선순위: trade_algorithm_final.md > REQUIREMENTS.md > trade_web_system.md ([REQUIREMENTS §13](../SOURCES/REQUIREMENTS.md)).
정본이 다루지 않는 구현 세부(워밍업 270 거래일, σ_down 하한 0.03, 전이 우선순위, 로트 FIFO, 호가 단위, 전술 트랙 지표는 레버리지 자체 시계열 등)는 [feature-strategy-engine.md](../features/feature-strategy-engine.md)가 확정한다 — 근거: [검토 로그](../discussion/review-strategy-engine-20260828.md).
벤치마크는 KODEX 200 매수보유로 통일하고 KOSPI는 보조 병기한다(정본 §11 우선, [검토 로그 K5](../discussion/review-backtest-20260828.md)).

## 이유

검토의견의 4개 버그는 위험 감소 없이 수익만 깎는 순손실 항목으로 논증이 타당했고, 배분 공식의 수학적 정합성(E∈[0,2]에서 자본합≤100%·실효노출=E)을 재검증으로 확인했다.

## 영향

절제 테스트 5종(정본 §11)이 백테스트 프리셋 요구사항이 됨. 정본 §10의 튜닝 파라미터는 Phase 4 백테스트 후 변경요청으로 확정.

## 관련 feature / ARCHITECTURE 항목

[feature-strategy-engine.md](../features/feature-strategy-engine.md), [feature-backtest.md](../features/feature-backtest.md)
