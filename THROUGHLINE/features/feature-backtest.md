# Feature: 백테스트 3스텝 위저드

## 1. 목적

전략을 과거 데이터로 검증한다. 조건 설정 → 비동기 실행(진행률) → 결과의 3스텝 위저드로, RAVG v2.5 프리셋과 절제(ablation) 비교를 내장한다.

## 2. 범위

### 포함 범위

- Step 1 조건 설정: 자본금·기간·기준봉·유니버스·비용 + RAVG v2.5 프리셋(절제 플래그 5종) + 범용 조건식(지표 AND/OR)·프리셋 저장/복제
- Step 2 실행: Celery 잡 + WS 진행률 + 취소
- Step 3 결과: KPI·자산곡선(벤치마크)·월별 히트맵·underwater·거래내역 CSV·오버레이 비교(최대 5개)
- "실전매매로 전환" 버튼(동일 파라미터로 실전 포트 생성 — [feature-portfolio.md](feature-portfolio.md) 연계)

### 제외 범위

- 파라미터 스윕 자동화(그리드 서치) — 후순위
- 분봉 기반 백테스트 — v1은 일봉 기준(분봉은 차트 전용)

## 3. 사용자 시나리오

RAVG v2.5 프리셋 선택 → 2015~2025·자본 1억 설정 → 실행(진행률 확인) → KPI를 KODEX 200 매수보유와 비교 →
절제 플래그 "상승장 익절 제거"만 끄고 재실행 → 두 결과를 오버레이로 비교해 모듈 기여 확인.

## 4. 최종 합의안

체결 판정식·비용 모델·KPI 규약·잡 수명주기를 검토로 확정하고, 재현성의 전제로 `data_fingerprint`를 도입했다. 아래 §5·§7·§8이 확정 결과다.

### 검토 요약 (감사용)

- 참여 Agent: [Database Engineer](../personas/database-engineer.md), [QA](../personas/qa.md) (병렬 서브에이전트 2기)
- 핵심 쟁점과 결론: 벤치마크가 문서 간 불일치(PRD "KOSPI" vs 정본 "KODEX 200 매수보유") → 정본 우선으로 KODEX 200 기본 + KOSPI 병기.
  수정주가 소급 적용은 그 자체가 look-ahead·재현성 파괴 → 원본 보존 + `adj_version` + 잡별 `data_fingerprint`·stale 배지로 해소.
  "유니버스 전체 <60s"는 미검증 → 기본 유니버스를 유동성 상위 500으로 보수 정의하고 M2 실측 후 재확정.
- 남은 쟁점: 유니버스 정의·60s 실측 `deferred`(M2 종료, §15), RLS 물리 차단 `deferred`(Phase 3), 과표기준가 정밀 과세 `deferred`(후순위)
- 검토 로그: [discussion/review-backtest-20260828.md](../discussion/review-backtest-20260828.md)

## 5. 기능 요구사항

### 5.1 체결 시뮬레이션 (확정 규칙)

- 종가 신호 → 익일 체결. 매수 지정가 L: `Open ≤ L` → **Open 체결**(갭 유리 체결) / `Low ≤ L < Open` → **L 체결**(동가 포함) / `Low > L` → 미체결.
- 매도 지정가 S: `Open ≥ S` → Open 체결 / `High ≥ S` → S 체결 / 미달 → 미체결 (매수와 대칭).
- **갭 필터 우선**: 시가가 갭 필터 조건(전일종가 −1.5×ATR 이하)에 걸리면 당일 그리드 체결 판정 전에 전량 취소 — 당일 그리드 체결 0건.
- 익일 휴장·거래정지 시 주문은 취소(이월 없음) — 다음 신호일에 재계산 발주.
- 상폐 종목 잔여 포지션은 마지막 거래일 종가로 강제청산. 유니버스는 as-of 시점 상장 종목으로 구성(생존 편향 금지, `symbol_status_history` 기반).

### 5.2 비용 모델 (확정 규칙)

- 수수료·거래세는 Step 1 입력값(기본: 수수료 0.015%, KODEX 200 거래세 0).
- **슬리피지: 지정가 체결 0, 시장가성 청산(레짐 이탈·강제청산·상폐)에만 적용**(기본 0.1%).
- 레버리지 과세: v1은 **실현차익 × 15.4% 단순화**(손실 시 0, 이월 없음). 화면에 "세금 단순화 계산" 명시. 과표기준가 정밀 모델은 후순위.
- 보수: 일할(연 365일 기준) 평가액 차감 — 레버리지 0.64%, KODEX 200 0.15%. 벤치마크(매수보유)에도 KODEX 200 보수는 동일 적용.

### 5.3 KPI 규약 (확정)

- CAGR: 거래일 252 기준 연환산. 기간 1년 미만이면 CAGR 대신 누적수익률 표기.
- MDD·underwater: 일별 **종가 자산곡선** 기준(동일 정의 공유).
- 샤프: rf=0, 표본표준편차(ddof=1), √252.
- 거래 1건 = **로트 FIFO 라운드트립**([feature-strategy-engine.md §5.6](feature-strategy-engine.md)과 동일 회계). 미청산 로트는 승률 분모에서 제외하고 "미청산 n건" 별도 표기.
- 벤치마크: 기본 **KODEX 200 매수보유**(보수 반영), KOSPI 지수 보조 병기.

### 5.4 절제 플래그·오버레이

- RAVG v2.5 프리셋에 절제 플래그 5종(①상승장 익절 제거 ②하방 변동성 ③레짐 판정 단축 ④레버리지 모듈(Emax 1.30) ⑤갭 필터+잔여예산). 정본 §11의 검증 순서를 UI에 안내.
- **플래그 종속성**: ④ OFF → 레버리지 2트랙·강제청산 규칙 자동 비활성(회색 처리). ② OFF → E 공식을 v1(0.18/σ20)로 연동 교체. 무효 조합은 UI에서 선택 불가.
- 오버레이 최대 5개 — 6번째 추가는 거부(안내 메시지). 자산곡선은 초기자본=100 정규화. 기간·비용 파라미터 불일치 시 경고 배지. 정렬은 추가순.

### 5.5 look-ahead 방지

- 데이터 접근은 리포지토리 단일 게이트웨이 `ohlcv_asof(as_of)` 경유만 허용. 테스트에서 쿼리 로그 어서션으로 `trade_date > as_of` 접근 0건을 검증.

## 6. 비기능 요구사항

- 일봉 5년 단일 종목 < 5s, 기본 유니버스(유동성 상위 500) < 60s — M2 종료 시 실측([ARCHITECTURE §9](../ARCHITECTURE.md)).
- 유니버스 백테스트는 종목 루프가 아닌 벡터화(일괄 로드 + 열 지향 연산). OHLCV는 잡 시작 시 단일 쿼리 로드(N+1 금지), `(universe, period, adj_version)` 키로 Redis 캐시.

## 7. 데이터 설계

공통 규칙은 [ARCHITECTURE §3](../ARCHITECTURE.md), 시세 스키마는 [feature-market-data.md §7](feature-market-data.md) 참조. 이 기능 고유:

- `backtests(id, user_id, params JSONB, preset_id?, status[QUEUED|RUNNING|DONE|FAILED|CANCELED], progress, data_fingerprint, kpi JSONB, created_at)`
  — `data_fingerprint` = hash(심볼셋, 기간, adj_version, 최종 ingested_at). 현재 fingerprint와 불일치하면 결과에 `stale` 배지, 오버레이 혼합 차단.
- `backtest_equity(backtest_id, trade_date, equity, benchmark)` — 정규 테이블(잡당 ~1,250행).
- `backtest_trades` — 잡별 JSONB 아티팩트(행 폭증 방지). 보존: KPI·자산곡선 영구, 거래내역은 90일 후 purge(고정 잡 제외).
- `backtest_presets(id, user_id, name, params JSONB)` — 복제 지원.

## 8. API 설계

공통 계약은 [ARCHITECTURE §5](../ARCHITECTURE.md) 참조.

- `POST /backtests` → 202 + 잡 리소스. `GET /backtests/{id}`, `GET /backtests?cursor=`.
- **`POST /backtests/{id}/cancel`** → 워커 실제 중단, 상태 CANCELED, 부분 결과 미저장.
- `WS /ws/backtests/{id}` — 진행률(1% 단위 발행). Pub/Sub 유실 대비 진행률 스냅샷을 Redis 키에 병행 저장 — 늦게 접속/재접속 시 스냅샷 즉시 수신.
- 결과 저장은 잡 완료 시 **단일 트랜잭션**(부분 저장 없음) — 재시도(최대 2회, acks_late) 멱등 보장.

## 9. UI/UX 설계

- 젠포트식 3스텝: Step 1 좌측 폼 + 우측 프리뷰(예상 거래 수·기간 검증 — 워밍업 270일 미달 시작일은 폼에서 차단), Step 2 진행률 바 + 취소, Step 3 KPI 타일·차트·CSV.
- 비용·세금 단순화·데이터 기준시각 상시 표기. stale 배지·재실행 CTA.

## 10. 보안 요구사항

- 잡·프리셋·결과는 소유자 격리. 조건식 입력은 Pydantic 스키마 화이트리스트(지표명 enum)로 검증 — 임의 식 실행 금지.

## 11. 로그 / 분석 요구사항

- `backtest_run` 이벤트(성공 지표), 잡별 소요·상태 로그.

## 12. 테스트 시나리오

### 자동 테스트

- **look-ahead**: 전체 실행 후 각 신호일 d에 대해 d+1 시가 이후 봉 마스킹 재실행 → d일 주문표 비트 동일. 쿼리 로그 어서션 `trade_date > as_of` 0건.
- **체결 판정**: D1 매수(Open≤L/Low≤L<Open/Low>L, 동가 경계), D3 매도 대칭, D2 갭 필터 우선(당일 체결 0건).
- **비용**: 비용 0 vs 반영 — 수익률·샤프 단조 감소·거래횟수 불변. 레버리지 과세 (a)이익→15.4% (b)손실→0. 보수 일할(보유일 0·당일 왕복 케이스).
- **KPI 수기 대조**: 10영업일 고정셋 CAGR(252 규약), 자산곡선 100,120,90,130,80 → MDD −38.46%, 샤프 rf=0/ddof=1 고정셋, 승률 분모(미청산 제외).
- **잡 수명주기**: 진행률 단조 증가·완료 후 접속 시 스냅샷 수신, 취소 시 부분 결과 0행, 재시도 후 중복 행 0.
- **재현성**: 동일 프리셋 2회 → KPI·CSV 바이트 동일. adj_version 증가 후 → stale 배지 표시·오버레이 혼합 차단.
- **절제**: 전 플래그 ON = 프리셋 결과 일치, ④ OFF 시 레버리지 거래 0건, ② OFF 시 E가 v1 공식과 일치. 각 조합 KPI 골든 파일 회귀.
- **생존 편향**: 기간 내 상폐 종목 포함 유니버스 → 상폐일 강제청산 거래 존재.

### 수동 QA

- 위저드 흐름·프리뷰 정확성, 오버레이 5개 한도·경고 배지, CSV 내용 검산.

### 예외 케이스

- 워밍업 미달 시작일 차단, 시세 결측 구간 포함 실행 시 명시적 오류, 60s 초과 잡 타임아웃 처리.

### 회귀 테스트 영향

- 전략 모듈 골든([feature-strategy-engine.md §12](feature-strategy-engine.md))과 KPI 골든 파일이 회귀 기준선 — 파라미터 "고정" 항목 변경 시 실패해야 정상.

## 13. 완료 조건

- §12 자동 테스트가 실제로 실행되어 전부 통과(green)하고 결과가 [HISTORY.md](../HISTORY.md)에 기록됨
- RAVG v2 절제 5종 백테스트가 실데이터(10년 시딩)로 실행되어 결과 리포트 생성 — [PLAN.md](../PLAN.md) Phase 4 게이트
- 성능: 단일 종목 5년 < 5s 실측 기록

## 14. 참고 ADR

[ADR-001](../adr/001-compose-monolith.md), [ADR-002](../adr/002-timescaledb.md), [ADR-005](../adr/005-strategy-single-source.md), [ADR-006](../adr/006-ravg-v2-adoption.md)

## 15. 미결정 사항

### 사용자 확인 필요

- 없음

### 기본값으로 진행한 사항

- 기본 유니버스 = 유동성 상위 500, 과세 단순화 모델, 거래내역 90일 purge — [ASSUMPTIONS.md](../ASSUMPTIONS.md)

### 후순위 검토 사항

- 유니버스 전체(전 종목) 60s 목표의 실측·재정의(M2 종료 변경요청), RLS 물리 차단(Phase 3), 과표기준가 정밀 과세, 파라미터 스윕 자동화
