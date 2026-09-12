# 검토 로그: RAVG 체결률 연구 (2026-09-12)

실행 방식: `parallel-subagents`

## 참여 페르소나와 실행 증거

- [Backend/Quant](../personas/backend-engineer.md), `/root/quant_review`, 2026-09-12 후속 작업. 입력: 현행 planner/backtest, 기존 boot/grid-cap 연구, 신규 연구 harness. 출력: 주문선·수량·금액 체결률 분리, 매수 가격만 이동과 TP 동시 변경 분리, 창 겹침·시장 레짐 초기화 교정 검토. 최종 JSON 수치와 비중첩 반대 결과까지 교차 확인. 파일 변경 없음.
- [QA](../personas/qa.md), `/root/qa_review`, 2026-09-12 후속 작업. 입력: 현행 생성·체결·무인 실행 분기, 순수 전략 테스트. 출력: 무주문/미도달/갭/현금의 코드 대응, submitted와 filled 구분, 원계획 수량과 발주 축소 수량 분리 필요. 63 passed, 2 warnings in 3.10s. 파일 변경·실주문·운영 DB 쓰기 없음.
- 주 에이전트 `/root`: READ ONLY 실제 시세 실험, 8변형 및 비용 민감도, 87창×8변형, 기본 동일성·가격·사유 분할 assertion 실행. [스크립트](../../apps/api/scripts/fill_rate_audit.py), [결과](../qa/fill-rate-results-20260912.json).

## 관점별 검토·근거

Quant: 기존 TIGER Grid 연구를 KODEX 결과로 대체하지 않음. Grid 계수 변경은 TP도 바꾸므로 buy-only wrapper를 추가. 같은 시장 레짐 prior를 전달하고 계좌만 초기화. 금액·수량률을 추가하여 깊은 3단의 주문선 분모 왜곡을 보완했다. 전체 결과가 불리해도 비중첩 8창 일부 평균은 유리하므로 보편적 열등을 주장하지 않는다.

QA: `planner.py` 현금 예약과 `backtest.py` 가격/갭/현금 분기를 읽고 원인 집계를 대조했다. 실전 `autoexec.py`는 수량 축소·submitted 이후 체결 확정 경로가 있으므로 모형 체결률을 실계좌 체결률로 대체할 수 없다. 실패 조건은 [QA F1~F7](../qa/fill-rate-study-20260912.md).

외부 조사(주 에이전트): 지정가/시장가의 가격·체결 교환관계와 비용 측정 필요성만 참조. 논문이 RAVG 수치를 인증한다고 주장하지 않는다.

- https://www.sec.gov/answers/limit.htm
- https://www.finra.org/investors/investing/investment-products/stocks/order-types
- https://www.sciencedirect.com/science/article/pii/S0304405X21000775

## 쟁점·상태·반영처

- 낮은 K200 체결률의 주된 기전: `resolved` — 현행 생성가격 1,943건 일치, 미체결의 97.15%는 가격 미도달(두 종목 합산).
- 높은 체결률이 높은 수익을 보장하는가: `resolved` — 현 표본에서 일관된 개선 증거 없음, 위험 악화 동반. [보고서](../docs/fill-rate-study-20260912.md).
- 지표 분모·TP 변경·창 독립성·prior 초기화: `resolved` — 구현과 보고서에서 각각 분리. 미래 기대수익 유의성은 주장하지 않음.
- 실계좌에서 실제로 낮은 체결이 발생하는 원인: `deferred(계좌·기간 지정 후 담당 개발자)` — F1/F2/F7.
- 공동 예산·로트 상태·현실적 체결/비용 및 독립 표본: `deferred(선행 결함 수정 및 데이터 보완 후 Quant/QA)` — F3~F6.
- 전략 개정: `deferred(현재 개선 근거 부족, 사용자 채택 지시 없음)` — 제품 파라미터·정본·ADR 변경 없음.

## 결론

현행 초기 10일 소량 진입은 초기 대기를 줄이는 효과가 있으나, 상시 고체결화는 수익률 개선과 동의어가 아니다. 일괄 시장가 전환·Grid 축소를 채택하지 않고 감사·실험 기록만 남긴다. 문서 인덱스·HISTORY·PROGRESS에 추적한다.
