# 검토 로그: KODEX 연동 전략 감사 (2026-09-12)

실행 방식: `parallel-subagents`

## 참여 페르소나와 선정 이유

[Backend/Quant Engineer](../personas/backend-engineer.md): 배분·로트·체결·수치 반례. [QA](../personas/qa.md): 실전 동일성·날짜·fingerprint·테스트 공백. 주 에이전트는 권위 대조, 실제 시세 재실행, 외부 문헌 확인과 최종 판정을 담당했다.

## 실행 증거와 페르소나별 검토

- 식별자 `/root/quant_review`, 2026-09-12 재개. 입력: 현행 `45eb373`, 정본·ADR-007/010·전략 명세·planner/backtest/signals, 읽기 전용 검토. 출력: 실전 트랙 소실·전술 목표 축소 누락·공통 현금버퍼 누락·최초 레짐 명세 차이의 네 반례. 전략 테스트+절단 동일성 **63 passed, 2 warnings, 2.93s**. 근거: [보고서](../docs/kodex-linkage-audit-20260912.md) A1/A3/A8/A9 및 해당 코드 링크.
- 식별자 `/root/qa_review`, 2026-09-12 재개. 입력: 현행 signals/portfolio/backtest 데이터 정렬·비용·재현성, 관련 명세·QA. 출력: 로트 상태 저장 누락·날짜 미검증·fingerprint 공백·음수 비용·과거 target 의미·모델/포트 응답 혼합. `docker compose exec -T api pytest -q tests/test_strategy_planner.py tests/test_strategy_backtest.py tests/test_signals.py::test_truncated_backtest_final_plan_equals_full_run_plan` → **50 passed, 2 warnings, 2.31s**(63개 범위와 중복). 운영 DB 쓰기·파일 수정 없음.
- 주 에이전트 `/root`: 기본 62개 **2.34s 통과**, 실제 시세 READ ONLY FULL/OFF/추가 보수 0 비교, A3 주문 0건 반례 직접 실행. 서브에이전트의 모든 후보를 무비판적으로 채택하지 않고 수동 보유 근사의 기존 의도 및 과거 target의 실제 기록 날짜를 확인해 표현을 한정했다.
- 이전 중단된 연구 시도의 결과는 독립 검토 근거로 사용하지 않았다. 이번 문헌 조사는 주 에이전트가 수행했다.

## 외부 출처

- 공식 상품의 일별 2배 목표: https://m.samsungfund.com/eng/etf/product/view.do?id=2ETF25
- 보수 기준가격 반영: https://www.samsungfund.com/etf/lounge/notice-view.do?no=76133
- 펀드 자산에서 비용 차감: https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/updated
- Avellaneda·Zhang 저자 원문: https://math.nyu.edu/faculty/avellane/LeveragedETF20090515.pdf
- Moreira·Muir 논문: https://amoreira2.github.io/alan-moreira.github.io/VolPortfolios_published.pdf

## 쟁점과 충돌

- A1/A2: `resolved` — 실전과 백테스트 로트 의미론 불일치 확인. 초기 수동 보유 근사의 승인 자체와 신규 체결 상태 손실을 구분했다.
- A3/A8: `resolved` — 수치 반례로 축소·현금버퍼 누락 확인. 미체결로 실제 노출이 목표보다 낮아지는 일반 현상과 구분했다.
- A4: `resolved` — 추가 운용보수는 현재 명세대로 구현되었지만 실제 ETF 가격 비용모형에 문제. 수정 시 명세부터 처리한다.
- A5/A6/A10/A12: `resolved` — 날짜·fingerprint·비용 검증·응답 조합의 코드 공백 확인. 실제 계좌 손실 발생은 입증하지 않았다.
- A9/A11: `requires user decision` — 최초 Neutral의 의미와 과거 target 재생 계약은 수정 착수 때 권위를 명확히 해야 한다.
- A7 및 독립 표본 성과 검증: `deferred(데이터·비용 모델 정리 후 담당 개발자/Quant)` — 분배금·실제 세후 비용·체결 민감도·미사용 구간 필요.
- 결함 구현 수정: `deferred(사용자의 수정 지시 대기, 담당 개발자)` — 이번 요청은 감사·보고이며 운영 코드 변경을 포함하지 않는다.

## 결론과 반영처

연동 모듈은 현 표본에서 수익·샤프 개선과 낙폭 악화를 함께 보였고 수익률 최대화는 입증하지 못했다. [보고서](../docs/kodex-linkage-audit-20260912.md)와 [QA A1~A12](../qa/kodex-linkage-audit-20260912.md)에 전체 발견 사항·실패 조건을 추적했다. 기능 계약 변경을 결정하지 않았으므로 feature/ARCHITECTURE/ADR은 변경하지 않았다. 감사 결과는 HISTORY/PROGRESS에 기록한다.
