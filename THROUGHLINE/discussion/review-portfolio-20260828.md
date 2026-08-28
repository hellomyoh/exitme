# 검토 로그: 실전매매 기록 (2026-08-28)

실행 방식: `role-play` (단일 에이전트가 페르소나 인스턴스를 로드하여 순차 검토)

## 참여 페르소나와 선정 이유

- [Backend/Quant Engineer Agent](../personas/backend-engineer.md) — TWR/XIRR·평단 재계산 수식 정확성
- [Security Agent](../personas/security.md) — 계좌정보 암호화 대상 확정
- [Product Manager Agent](../personas/product-manager.md) — 백테스트→실전 전환 루프 보전

## 페르소나별 검토

### Backend/Quant

- 위험: 평단 자동 재계산에서 매도 시 처리 미정의 — 평단 유지(이동평균법) vs 로트 차감. 전략 엔진이 로트 FIFO를 정본 회계로 채택([review-strategy-engine-20260828.md](review-strategy-engine-20260828.md) R7)했으므로 실전 기록도 동일 회계여야 백테스트 대비가 성립. 실패 조건: 같은 매매 이력에 두 화면(실전/백테스트)이 다른 실현손익 표시. 제안: 로트 FIFO 통일, 화면 표시는 평단 병기.
- 위험: XIRR은 입출금 현금흐름 시계열 필요 — 입출금 등록 기능이 REQUIREMENTS §3-5에 없음. 실패 조건: 입금 반영 없이 XIRR이 수익률을 과대/과소 계산. 제안: 현금 입출금 등록(일자·금액)을 이 기능 포함 범위로 명시. (근거: [REQUIREMENTS §3-5·§6](../SOURCES/REQUIREMENTS.md))
- 위험: 연환산 수익률은 보유 7일 등 단기간에 극단값 — 표시 억제 기준 필요. 제안: 보유 30일 미만은 연환산 미표시.

### Security

- 확정: 암호화(AES-GCM) 대상은 포지션 수량·단가·금액 필드. 종목 코드는 평문(검색·조인 필요). 실패 조건: DB 덤프에서 보유 금액 평문 노출. (근거: [ARCHITECTURE §6](../ARCHITECTURE.md))
- 위험: 암호화 필드는 DB 레벨 집계(SUM) 불가 — 대시보드 총자산 계산은 앱 레벨 복호 후 집계. 성능 영향은 보유 종목 수(개인 수십 건)에서 무시 가능.

### Product Manager

- 확인: "백테스트→실전 전환" 버튼이 만드는 실전 포트는 파라미터 사본을 보관해야 이후 성과 대비(대시보드 §3-6)가 성립 — 전환 시 `backtest_id` 링크 저장. (근거: REQUIREMENTS §3-7·§4-3)

## 쟁점과 충돌

1. 회계 방식(평단 vs FIFO) — FIFO로 통일, 화면은 평단 병기로 조정.

## 결론(합의안)과 반영처

- 로트 FIFO 통일·평단 병기, 입출금 등록 포함, 연환산 30일 억제, 암호화 대상 확정, `backtest_id` 링크 — 모두 `resolved` → [feature-portfolio.md](../features/feature-portfolio.md) §5·§7·§10
- **위험→테스트 추적**: FIFO 실현손익·XIRR 입출금 케이스·암호화 저장 확인은 feature §12 및 [qa/regression-checklist.md](../qa/regression-checklist.md)에 등재
