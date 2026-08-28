# PROGRESS.md

## 현재 Phase

Phase 1 (시세 데이터 파이프라인) — 진행 중 (Phase 0 완료). 브랜치 `feat/market-data`

## 완료된 작업

- 초기화(2026-08-28), Phase 0 완료: compose 7서비스 healthy·e2e 스모크·CI 정의
- Phase 1 코어: 스키마(하이퍼테이블)·KIS 클라이언트·pykrx 폴백·검증/멱등 적재·시딩 스크립트·daily_ingest·/ohlcv API — 테스트 18/18 통과

## 진행 중인 작업

- [자율 모드 2026-08-28] Phase 1 잔여 중 WS /ws/quotes·crontab 확정 구현 중. 실 시딩·3거래일 배치는 KIS 키 대기. 이후 Phase 2~6 순차 진행 예정

## 남은 작업

- Phase 1 잔여 → Phase 2~6 ([PLAN.md](PLAN.md))

## QA 상태

- 자동: 18/18 green (2026-08-28, HISTORY 기록). 수동: 실 KIS 배치 완주·키 마스킹 grep 미수행(키 대기)

## 다음 세션 첫 명령

"사용자가 .env에 KIS 키를 기입했는지 확인 후 `docker compose run --rm api python -m scripts.seed --years 10`으로 실 시딩을 완주하고, KODEX 200/레버리지 데이터 검증 리포트를 만든 뒤 Phase 1 잔여(WS quotes, crontab, 3거래일 배치 검증)를 진행하세요."
