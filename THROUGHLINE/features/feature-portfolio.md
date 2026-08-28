# Feature: 실전매매 기록

## 1. 목적

수동 매수 등록 기반으로 매수 시점 기준 수익률을 추적하고, 백테스트에서 전환된 실전 포트의 성과를 관리한다.

## 2. 범위

### 포함 범위

- 매수/매도 등록(종목·수량·단가·일시, 차트 클릭 입력), 로트 FIFO 원장 + 평단 병기, 현금 입출금 등록
- 수익률 카드(비용 포함/제외 토글, 보유일수·연환산·최고최저 도달·목표/손절 진행 바)
- 계좌 수준 실현/미실현 손익·TWR·XIRR, 매매일지 메모·태그
- 백테스트→전환 포트(`backtest_id` 링크, 기대 성과 대비)

### 제외 범위

- 증권사 계좌 연동·자동 체결 반영(v1 수동 입력), 목표·손절 도달 푸시 알림(후순위)

## 3. 사용자 시나리오

주문표대로 HTS에서 체결 → 체결 내역을 매수 등록(또는 차트에서 클릭) → 수익률 카드에서 평단·수익률·목표 진행 확인 →
매매일지에 메모 → 대시보드에서 백테스트 기대 성과와 대비.

## 4. 최종 합의안

회계를 로트 FIFO로 통일(전략 엔진·백테스트와 동일)하고, XIRR의 전제인 입출금 등록을 포함 범위로 확정했다.

### 검토 요약 (감사용)

- 참여 Agent: [Backend/Quant Engineer](../personas/backend-engineer.md), [Security](../personas/security.md), [Product Manager](../personas/product-manager.md)
- 핵심 쟁점과 결론: 평단법 vs FIFO — 백테스트와 실전이 같은 매매에 다른 손익을 보이면 안 되므로 FIFO 통일, 화면은 평단 병기.
  XIRR은 입출금 없이는 계산 불가 → 입출금 등록을 범위에 추가. 암호화 대상은 수량·단가·금액(종목 코드는 평문).
- 남은 쟁점: 없음
- 검토 로그: [discussion/review-portfolio-20260828.md](../discussion/review-portfolio-20260828.md)

## 5. 기능 요구사항

- 등록: 매수·매도·입금·출금 4종 거래. 매도는 FIFO 자동 매칭 → 실현손익 산출. 평단은 표시용으로 병기.
- 수익률: 종목 `= (현재가 − 평단) / 평단`, 비용 포함 토글 시 수수료·세금 반영. 연환산은 보유 30일 미만 미표시(극단값 방지).
- 계좌: TWR(일별 체인), XIRR(입출금 현금흐름 기반). 목표가·손절가는 포지션별 설정, 진행 바 표시.
- 전환 포트: 백테스트 결과 화면의 버튼으로 생성, 파라미터·`backtest_id` 사본 보관.

## 6. 비기능 요구사항

- 현재가 갱신은 WS 구독, 카드 리렌더 시 열 흔들림 없음(tabular-nums).

## 7. 데이터 설계

- `portfolios(id, user_id, name, kind[manual|from_backtest], backtest_id?, params?)`.
- `transactions(id, portfolio_id, instrument_id?, kind[buy|sell|deposit|withdraw], qty🔒, price🔒, amount🔒, executed_at, memo, tags[])` — 🔒 = AES-GCM 암호화 필드.
- `lots(id, portfolio_id, instrument_id, qty_open🔒, price🔒, opened_at)` — FIFO 원장. 실현손익은 매도 시 계산·저장🔒.
- 공통 규칙 [ARCHITECTURE §3](../ARCHITECTURE.md), 암호화 [ARCHITECTURE §6](../ARCHITECTURE.md).

## 8. API 설계

- `POST /positions`(거래 등록), `GET /portfolio/summary`, `GET /portfolios/{id}/positions`, `PATCH /positions/{id}`(목표·손절·메모).

## 9. UI/UX 설계

- 수익률 카드 그리드 + 종목 상세(앵커 탭 + sticky 현재가 헤더). 비용 포함/제외 토글 상시 노출([REQUIREMENTS §7](../SOURCES/REQUIREMENTS.md)).

## 10. 보안 요구사항

- 수량·단가·금액 필드 AES-GCM 암호화 저장(DB 덤프에서 평문 미노출). 집계는 앱 레벨 복호 후 수행. 소유자 격리 필수.

## 11. 로그 / 분석 요구사항

- `portfolio_created_from_backtest` 이벤트(성공 지표 — 전환율 20%).

## 12. 테스트 시나리오

### 자동 테스트

- FIFO 매칭: 3단 분할매수 후 부분 매도 → 실현손익·잔여 로트 수기 대조. 평단 병기 값 일치.
- XIRR: 입금 2회 + 평가액 상승 고정셋 수기 대조, 입출금 미등록 시 XIRR 미표시. TWR 일별 체인 수기 대조.
- 암호화: DB 원시 조회로 qty/price/amount 평문 미검출. 비용 포함/제외 토글 계산.
- 연환산 30일 억제 경계(29일/30일).

### 수동 QA

- 차트 클릭 매수 등록 UX, 목표/손절 진행 바, 매매일지 태그 필터.

### 예외 케이스

- 보유 수량 초과 매도 거부, 과거 일시 소급 등록 시 FIFO 재계산, 상폐 종목 보유 시 처리 안내.

### 회귀 테스트 영향

- FIFO 회계는 전략 엔진·백테스트와 공유 개념 — 회계 규칙 변경 시 3개 기능 테스트 동시 확인.

## 13. 완료 조건

- §12 자동 테스트 실제 실행·전부 통과(green), [HISTORY.md](../HISTORY.md) 기록. 백테스트→전환→카드 표시 e2e 1회 통과.

## 14. 참고 ADR

[ADR-003](../adr/003-auth-jwt.md)

## 15. 미결정 사항

### 사용자 확인 필요

- 없음

### 기본값으로 진행한 사항

- 연환산 30일 억제, 암호화 대상 필드 — [ASSUMPTIONS.md](../ASSUMPTIONS.md)

### 후순위 검토 사항

- 목표·손절 브라우저 푸시 알림, 증권사 체결 내역 CSV 임포트
