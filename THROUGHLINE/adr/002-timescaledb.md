# ADR-002: TimescaleDB 단일 시세 저장소 (일봉/분봉 물리 분리)

## 상태

Accepted

## 배경

차트·백테스트·실전·전략 엔진이 동일 시세를 사용해야 하고([REQUIREMENTS §8](../SOURCES/REQUIREMENTS.md)), 일봉과 분봉의 밀도 차가 약 390배라 단일 하이퍼테이블로는 청크 간격을 정할 수 없다([검토 로그 R1](../discussion/review-backtest-20260828.md)).

## 선택지

1. 일반 PostgreSQL 테이블
2. TimescaleDB 단일 하이퍼테이블 (interval 컬럼 구분)
3. TimescaleDB `ohlcv_daily`(청크 1년) / `ohlcv_intraday`(청크 7일) 물리 분리 + 논리 뷰

## 결정

3안. "시세 원본 1곳 정규화" 원칙은 논리 뷰(UNION)로 충족한다.
수정주가는 `close_raw`(불변) + `adj_factor`(`adj_version` append-only) + `corporate_actions` 테이블로 원본을 보존한다.
거래정지·상폐는 `symbol_status_history` 시점 속성으로 관리한다(생존 편향 방지).

## 이유

청크 적정화(조회 성능)와 재현성(원본 불변) 요구를 동시에 만족하는 유일한 안. 원본 UPDATE는 골든 테스트·백테스트 재현성을 붕괴시킨다(검토 로그 R2).

## 영향

시세 적재·수정주가 재계산 로직이 다소 복잡해짐. 백테스트 잡은 `data_fingerprint`로 데이터 세대를 기록해야 함.

## 관련 feature / ARCHITECTURE 항목

[ARCHITECTURE §1·§3](../ARCHITECTURE.md), [feature-market-data.md](../features/feature-market-data.md), [feature-backtest.md](../features/feature-backtest.md)
