# ADR-001: docker compose 단일 서버 + api/worker 분리 아키텍처

## 상태

Accepted

## 배경

개인 투자자용 소규모 서비스로 초기 사용자는 운영자 중심 소수([REQUIREMENTS §1](../SOURCES/REQUIREMENTS.md)). 다만 백테스트는 수 초~수십 초의 계산 작업이라 요청-응답 프로세스에서 실행하면 UI가 블로킹된다.

## 선택지

1. 단일 FastAPI 프로세스 (BackgroundTasks)
2. docker compose 모놀리식 + Celery worker 분리 (원 PRD 안)
3. k8s 마이크로서비스

## 결정

2안. nginx/web/api/worker/scheduler/db/redis 7서비스 compose, 조회(api)와 계산(worker) 분리, 진행률은 Redis Pub/Sub → WS.
Celery 큐는 `backtest`(2)/`ingest`(2)로 분리해 수집 배치가 대화형 백테스트를 블로킹하지 않게 한다.

## 이유

1안은 워커 다운·재시도·진행률 요구([REQUIREMENTS §11](../SOURCES/REQUIREMENTS.md))를 충족 못 하고, 3안은 규모 대비 과잉. 큐 분리는 [검토 로그](../discussion/review-backtest-20260828.md) R8·[시세 검토](../discussion/review-market-data-20260828.md)의 수렴 결론.

## 영향

compose 파일 3종(base/override/prod), healthcheck 의무, 서비스 추가·포트 변경은 ADR 대상.

## 관련 feature / ARCHITECTURE 항목

[ARCHITECTURE §1·§2·§8](../ARCHITECTURE.md), [feature-backtest.md](../features/feature-backtest.md), [feature-market-data.md](../features/feature-market-data.md)
