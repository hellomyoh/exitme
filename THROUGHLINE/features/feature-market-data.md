# Feature: 시세 데이터 파이프라인

## 1. 목적

KIS Open API(주) + pykrx(보조)로 일봉·분봉을 수집해 TimescaleDB에 정규화 저장한다. 차트·백테스트·실전·전략 엔진의 공통 토대.

## 2. 범위

### 포함 범위

- 10년 일봉 시딩 스크립트(`scripts.seed`), 장 마감 후 일봉·분봉 배치 수집, 장중 현재가 폴링(관심 종목), 수정주가·기업행위 관리, 거래일 캘린더, 종목 마스터·상태 이력

### 제외 범위

- 실시간 체결 틱 데이터, 재무제표 상세(v1은 시세 중심), 해외·파생

## 3. 사용자 시나리오

운영자가 `docker compose run --rm api python -m scripts.seed --years 10` 실행 → 10년 일봉 적재 →
이후 Celery beat이 매 거래일 장 마감 후 자동 수집 → 사용자 화면에는 항상 기준시각·지연 여부가 표기된다.

## 4. 최종 합의안

KIS/pykrx 우선순위, 검증 규칙, 시딩 체크포인트, 큐 분리를 확정했다.

### 검토 요약 (감사용)

- 참여 Agent: [System Architect](../personas/system-architect.md), [Database Engineer](../personas/database-engineer.md), [Security](../personas/security.md)
- 핵심 쟁점과 결론: 동일 데이터 불일치 시 KIS 우선·pykrx는 결측 보충/검증([ADR-004](../adr/004-market-data-source.md)).
  수집 배치가 백테스트를 블로킹하지 않도록 Celery 큐 분리. pykrx 비공식 크롤링 위험은 OHLC 관계식 검증으로 방어.
- 남은 쟁점: KIS 호출 한도 실측 `deferred`(Phase 1, §15)
- 검토 로그: [discussion/review-market-data-20260828.md](../discussion/review-market-data-20260828.md)

## 5. 기능 요구사항

- 시딩: 종목×기간 단위 체크포인트 기록, 중단 후 재실행 시 이어받기(`ON CONFLICT DO NOTHING`), 완료 리포트(적재 행수·실패 목록).
- 일일 배치: 장 마감 후 KIS로 당일 일봉 수집 → 검증 → 적재 → 전략 엔진 배치 트리거. 실패 시 pykrx 폴백, 최종 실패는 `batch_runs` 기록 + 주문표 보류.
- 검증 규칙: `low ≤ min(open,close)`, `max(open,close) ≤ high`, 거래량 ≥ 0, 결측·중복 거부. 통과분만 적재.
- 장중: 관심 종목 현재가 10초 폴링(기본값) → Redis 캐시 → `WS /ws/quotes` push. 한도 초과 시 주기 자동 확대 + "시세 지연" 플래그.
- 수정주가: `corporate_actions` 등록 → `adj_factor` 재계산(`adj_version` 증가) — 원본 `*_raw` 불변.
- 거래일 캘린더: pykrx 휴장일 시딩, 전략 엔진·백테스트의 단일 소스.

## 6. 비기능 요구사항

- 일일 배치는 장 마감 후 20분 내(전략 엔진 30분 예산 내 선행). 수집은 `ingest` 큐 — `backtest` 큐와 분리([ADR-001](../adr/001-compose-monolith.md)).

## 7. 데이터 설계

공통 규칙은 [ARCHITECTURE §3](../ARCHITECTURE.md) 참조.

- `ohlcv_daily(instrument_id, trade_date, open_raw, high_raw, low_raw, close_raw, volume, adj_factor, source, ingested_at)` — 하이퍼테이블, 청크 1년, PK `(instrument_id, trade_date)`.
- `ohlcv_intraday(instrument_id, ts, timeframe, o,h,l,c,v)` — 하이퍼테이블, 청크 7일.
- 논리 뷰 `ohlcv`(수정주가 계산 필드 포함)가 소비자 표준 인터페이스([ADR-002](../adr/002-timescaledb.md)).
- `instruments(id, code, name, type, …)`, `symbol_status_history(instrument_id, valid_from, valid_to, status[listed|halted|delisted])`.
- `corporate_actions(instrument_id, date, kind, ratio)`, `trading_calendar(date, is_open)`, `batch_runs(id, kind, started_at, finished_at, status, detail JSONB)`.

## 8. API 설계

- `GET /ohlcv?code=&timeframe=&from=&to=` — 수정주가 기준, `as_of`·`delayed` 포함.
- `GET /instruments?q=` — 종목 검색. `WS /ws/quotes` — 구독 코드별 현재가 push.

## 9. UI/UX 설계

해당 없음 (배치·API 중심. 기준시각 표기는 소비 화면들의 요구사항)

## 10. 보안 요구사항

- KIS 키는 `.env` 서버 보관, 요청 URL·헤더 로그 마스킹 필터 의무. 외부 응답은 스키마 검증 후만 신뢰(pykrx 오염 방어).

## 11. 로그 / 분석 요구사항

- 배치별 적재 행수·실패 사유·KIS/pykrx 불일치 목록을 `batch_runs`에 기록.

## 12. 테스트 시나리오

### 자동 테스트

- OHLC 검증 규칙 위반 행 거부, 중복 삽입 시 멱등, 시딩 중단→재실행 이어받기(중복 0행), KIS/pykrx 불일치 시 KIS 채택 + 로그, corporate_action 등록 → adj_version 증가·close_raw 불변, 캘린더 휴장일 조회.

### 수동 QA

- 실 KIS 계정으로 일일 배치 1회 완주, 로그에 키 마스킹 확인(grep으로 키 값 미검출).

### 예외 케이스

- KIS 장애 시 pykrx 폴백 + delayed 플래그, 폴링 한도 초과 시 주기 확대.

### 회귀 테스트 영향

- adj_version 갱신이 백테스트 stale 배지와 연동되는지([feature-backtest.md §7](feature-backtest.md)).

## 13. 완료 조건

- §12 자동 테스트 실제 실행·전부 통과(green), [HISTORY.md](../HISTORY.md) 기록
- 10년 시딩 완주 + KODEX 200/레버리지 데이터 검증 리포트
- 일일 배치가 실 스케줄로 3거래일 연속 성공

## 14. 참고 ADR

[ADR-002](../adr/002-timescaledb.md), [ADR-004](../adr/004-market-data-source.md)

## 15. 미결정 사항

### 사용자 확인 필요

- 없음

### 기본값으로 진행한 사항

- 폴링 10초, 청크 간격(일봉 1년/분봉 7일) — [ASSUMPTIONS.md](../ASSUMPTIONS.md)

### 후순위 검토 사항

- KIS 호출 한도 실측 후 폴링 주기 확정(Phase 1), 분봉 수집 범위(v1은 관심 종목 한정 검토)
