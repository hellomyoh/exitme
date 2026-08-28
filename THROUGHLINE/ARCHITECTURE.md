# ARCHITECTURE.md — 횡단 계약

여러 기능에 공통 적용되는 계약 문서입니다. feature 문서는 이 문서를 참조하며 같은 내용을 중복 기술하지 않습니다.
**이 문서의 항목을 변경하려면 ADR을 작성하고 [adr/INDEX.md](adr/INDEX.md)를 갱신해야 합니다.**

## 1. 시스템 개요

StockLab — RAVG v2 매매 전략([SOURCES/trade_algorithm_final.md](SOURCES/trade_algorithm_final.md) 정본)을 내장한
백테스트 + 실전매매 기록 + 자산 대시보드 통합 웹. 실주문 체결은 범위 외(v1).

```text
Browser (Next.js SPA)
  → Nginx (리버스 프록시, WS 업그레이드)
    → web    : Next.js 15 SSR
    → api    : FastAPI REST + WebSocket
        → PostgreSQL 16 + TimescaleDB (OHLCV·시그널·계좌·포트)
        → Redis 7 (캐시 · Celery 브로커 · Pub/Sub)
        → Celery worker    : 백테스트 엔진 (pandas/vectorbt)
        → Celery beat      : 시세 수집 배치 + RAVG v2 일일 시그널 배치
            → 외부 시세: KIS Open API (주) + pykrx (보조)
```

**설계 원칙** (근거: [ADR-001](adr/001-compose-monolith.md))

- 조회(api)와 계산(worker)을 분리한다 — 장시간 백테스트가 UI를 막지 않는다. 진행률·결과는 Redis Pub/Sub → WS 스트리밍.
- 시세 원본은 TimescaleDB 하이퍼테이블 1곳에 정규화 저장한다 — 차트·백테스트·실전·전략 엔진이 동일 데이터 사용 ([ADR-002](adr/002-timescaledb.md)).
- **전략 코드 단일 소스**: 백테스트 엔진과 일일 시그널 엔진은 동일한 전략 모듈(`strategy/` 패키지)을 공유한다.
  같은 입력(OHLCV + 파라미터)에 같은 출력(주문표)이 나와야 하며, 전략 로직을 두 벌 구현하지 않는다 ([ADR-005](adr/005-strategy-single-source.md)).

## 2. 기술 스택 (확정)

| 계층 | 스택 | 비고 |
|---|---|---|
| 프론트 | Next.js 15 + TypeScript + Tailwind v4 + shadcn/ui | 차트: TradingView Lightweight Charts v5 (Apache-2.0, 지표 자체 구현) |
| API | FastAPI (Python 3.12) + SQLAlchemy 2 + Pydantic v2 | REST + `/ws` WebSocket |
| 워커 | Celery worker (`concurrency=4`) + Celery beat | api와 동일 이미지 |
| DB | timescale/timescaledb-ha:pg16 | volume `pgdata` |
| 캐시/브로커 | redis:7-alpine | 캐시 + Celery 브로커 + Pub/Sub |
| 인프라 | nginx:alpine, docker compose, GitHub Actions CI | dev/prod override 분리 |
| 시세 | KIS Open API (주) + pykrx (보조·시딩) | [ADR-004](adr/004-market-data-source.md) |

## 3. 데이터 모델 공통 규칙

- **시계열**(OHLCV, 일별 시그널 스냅샷, 자산곡선)은 TimescaleDB 하이퍼테이블, 그 외는 일반 정규화 테이블.
- ID 전략: 일반 테이블은 `BIGINT GENERATED ALWAYS AS IDENTITY`. 하이퍼테이블은 자연키(`(instrument_id, timeframe, ts)` 등) 복합 PK.
- 금액은 **정수(원)** (`BIGINT`), 수량은 정수, 비율·가중치는 `NUMERIC` (부동소수점으로 돈 계산 금지).
- 모든 테이블에 `created_at`/`updated_at` (`timestamptz`, UTC 저장·표시 시 KST 변환). 거래일 컬럼은 `DATE`(KST 기준 거래일).
- soft delete는 사용하지 않는다(v1). 삭제는 물리 삭제, 단 시그널·주문표·백테스트 결과는 삭제 대신 보존.
- **시그널·주문표는 append-only**: 재계산 시 새 버전 행을 추가하고 최신 버전을 조회한다. 기존 행 UPDATE 금지.
- **수정주가**: 원본 가격과 수정계수를 함께 보관하고 수정주가는 계산 필드로 제공한다. 액면분할 등 이벤트 발생 시
  재수집분과 기존 백테스트 결과의 정합성 검증이 필요하다 (상세는 [feature-backtest.md](features/feature-backtest.md) §7).
- 거래정지·상장폐지는 OHLCV 행 플래그가 아니라 종목 이벤트 테이블로 관리한다.

## 4. 네이밍 규칙

| 대상 | 규칙 | 예 |
|---|---|---|
| Python 모듈·함수·변수 | snake_case | `compute_regime()` |
| Python 클래스 | PascalCase | `SignalEngine` |
| TypeScript 변수·함수 | camelCase | `fetchOhlcv()` |
| TS 컴포넌트·타입 | PascalCase | `BacktestWizard` |
| DB 테이블·컬럼 | snake_case, 테이블은 복수형 | `backtests`, `order_sheets` |
| API 경로 | 복수 명사, 소문자 | `/backtests`, `/signals/daily` |
| 환경변수 | UPPER_SNAKE | `KIS_APP_KEY`, `DATABASE_URL` |
| 파일(문서) | kebab-case | `feature-strategy-engine.md` |

## 5. API 계약

- 프로토콜: REST(JSON) + WebSocket. Pydantic 스키마가 계약의 단일 정의(OpenAPI 자동 생성).
- 버전 전략: v1은 경로 프리픽스 없음. 파괴적 변경 시 `/v2/` 도입 (ADR 필요).
- **에러 포맷: problem+json (RFC 9457)** — `{"type","title","status","detail","instance"}`. 도메인 에러코드는 `type` URI 꼬리로 표현.
- 페이지네이션: 시계열은 `?from=&to=&limit=` 범위 조회, 목록은 커서 기반 `?cursor=&limit=` (기본 limit 50, 최대 1000).
- 주요 엔드포인트 (상세는 각 feature 문서):
  `GET /ohlcv` · `POST /backtests` · `GET /backtests/{id}` · `GET /signals/daily` · `POST /positions` · `GET /portfolio/summary` ·
  `WS /ws/quotes` · `WS /ws/backtests/{id}`
- 장시간 작업(백테스트)은 `202 Accepted` + 잡 리소스 반환, 진행률은 WS 구독.
- 모든 화면 응답에 시세 기준시각(`as_of`)과 지연 여부(`delayed`)를 포함한다 (정직한 수치 원칙).

## 6. 인증 / 세션 모델

근거: [ADR-003](adr/003-auth-jwt.md)

- JWT **access(단기, 15분) + refresh(장기, 14일, 회전)**. access는 메모리 보관, refresh는 httpOnly Secure 쿠키.
- 사용자 유형은 **회원 단일 등급**. 모든 리소스는 소유자(`user_id`) 기준 격리 — 쿼리 레벨에서 강제.
- WS는 접속 핸드셰이크 시 토큰 검증, 만료 시 재연결.
- 외부 API 키(KIS)는 서버 환경변수로만 보관, 클라이언트·저장소 노출 금지.
- 개인 계좌정보(매수 기록 등 금액 데이터)는 AES-GCM 애플리케이션 레벨 암호화 저장. 키는 `.env`(`ENCRYPTION_KEY`).

## 7. 공통 로그 / 모니터링

- 구조화 JSON 로그(stdout) — 필드: `ts, level, service, event, user_id?, detail`. compose `docker logs`로 수집(v1).
- 배치(시세 수집·일일 시그널)는 시작/종료/건수/실패 사유를 반드시 로그 + `batch_runs` 테이블에 기록.
- 시그널 배치 실패 시 주문표 미생성 상태를 API가 명시적으로 반환한다 (조용한 실패 금지).
- 분석 이벤트(성공 지표 측정용): `backtest_run`, `portfolio_created_from_backtest`, `visit` 최소 3종을 이벤트 테이블에 적재.

## 8. 환경 / 배포 구조

- `docker-compose.yml`(공통) + `docker-compose.override.yml`(개발: bind mount·hot reload) + `docker-compose.prod.yml`(멀티스테이지·replica).
- 환경변수는 `.env` 단일 관리, `.env.example`만 커밋. Secret은 절대 커밋 금지.
- 모든 서비스 `healthcheck` + `depends_on: condition: service_healthy`. 전체 기동 `docker compose up -d` 한 줄.
- 초기 시딩: `docker compose run --rm api python -m scripts.seed --years 10`.
- CI(GitHub Actions): 테스트 → 동일 compose로 e2e → 이미지 빌드. 배포는 사용자 승인 후.

## 9. 성능 기준선 (횡단)

| 항목 | 기준 |
|---|---|
| 차트 초기 로드 | p95 < 1.5s |
| 백테스트 (일봉 5년 단일 종목) | < 5s |
| 백테스트 (유니버스 전체) | < 60s |
| 일일 시그널 배치 | 장 마감 후 30분 내 완료 |
| 차트 렌더 | 5만 캔들 60fps (canvas) |

## 10. 법적 고지 (횡단)

모의·과거 데이터 기반이며 투자 권유가 아님을 시뮬레이터·실전·주문표 화면에 상시 명시한다.
수수료·세금·슬리피지·시세 지연을 화면에서 숨기지 않는다.

## 11. 변경 규칙

이 문서의 계약(§1~§10)을 변경하는 결정은 **반드시 ADR을 작성**하고 [adr/INDEX.md](adr/INDEX.md)를 갱신한 뒤,
영향 받는 feature 문서를 같은 commit에서 함께 갱신한다.
