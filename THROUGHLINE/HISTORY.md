# HISTORY.md

## [2026-08-28] init | THROUGHLINE 프로젝트 초기화 (KICKOFF)

- 작업 내용: SOURCES/REQUIREMENTS.md(+참고자료 3종) 기반 초기화. 횡단 계약·기능명세 6종·QA·docs·페르소나·ADR 6종 생성. 핵심 기능 2종은 병렬 서브에이전트 4기로 검토(증거: discussion/ 로그), 나머지는 역할극 검토.
- 변경 파일: 루트 README.md·AGENTS.md·CLAUDE.md, THROUGHLINE/ 전체 (SOURCES 원본 제외)
- 테스트 결과: 해당 없음 (문서 산출물만 — 코드 없음)
- QA 결과: 해당 없음
- Git commit: chore: initialize project with THROUGHLINE (초기화 단일 commit)
- 특이사항: 검토에서 명세 공백 20여 건 발견 → feature 문서 합의안·ASSUMPTIONS 16건으로 확정. 유니버스 60s 목표는 M2 실측 후 재확정 예정.

## [2026-08-28] feat | Phase 0 인프라 + Phase 1 시세 파이프라인 코어 구현

- 작업 내용: docker compose 7서비스(nginx/web/api/worker/scheduler/db/redis, healthcheck·큐 분리), FastAPI(/health·/instruments·/ohlcv, problem+json), Next.js 15 최소 스캐폴드, Alembic 0001(하이퍼테이블 포함), KIS 인증·일봉·현재가 클라이언트(공식 GitHub 패턴, 140일 창 페이지네이션), pykrx 폴백, OHLC 검증, 멱등 적재(RETURNING), 시딩 스크립트(연 단위 체크포인트·빈 응답 실패 가드), Celery daily_ingest, GitHub Actions CI. 사용자 지시(채팅): 시세 API = KIS 확정, 키는 .env 수동 기입.
- 변경 파일: apps/api/**, apps/web/**, docker-compose*.yml, infra/nginx/, .github/workflows/ci.yml, .env.example, .gitignore + THROUGHLINE 문서 갱신(feature-market-data §5, PLAN, ASSUMPTIONS, NOTES)
- 테스트 결과: `docker compose run --rm api pytest -v tests/` → **18 passed** (validators 6, KIS mock 8, DB 통합 4 — alembic upgrade head 후). 시딩 스모크: pykrx 경로는 KRX 차단으로 SeedError 정상 발생(가드 검증), KIS 실 시딩은 키 기입 대기.
- QA 결과: compose 전 서비스 healthy, nginx 경유 e2e(/healthz·/api/health·web 페이지) 통과. 실 KIS 배치·3거래일 검증은 미수행(키 대기).
- Git commit: feat: bootstrap infra and market data pipeline (Phase 0/1)
- 특이사항: KRX가 pykrx 요청 차단(LOGOUT) → 시딩 KIS 1순위로 명세 갱신(권위 진단: 현실 확인 후 명세 수정). TimescaleDB rowcount=-1, busybox wget IPv6, setuptools<81 — NOTES.md 기록.

## [2026-08-28] feat | Phase 1 잔여 — WS quotes 릴레이 + beat crontab 확정

- 작업 내용: `WS /ws/quotes`(구독→캐시 즉시 송신 + Redis pub/sub 릴레이), `poll_quotes` 태스크(10초, 키/장중/거래일 가드), daily_ingest crontab KST 16:05(mon-fri) 확정.
- 변경 파일: apps/api/app/quotes.py(신규), main.py, worker.py, tests/test_ws_quotes.py(신규)
- 테스트 결과: `docker compose run --rm api pytest -q tests/` → **20 passed** (신규 WS 2건 포함)
- QA 결과: 자동 테스트만 (실 KIS 폴링은 키 대기)
- Git commit: feat: ws quotes relay and beat crontab (Phase 1)
- 특이사항: Phase 1 코드 전체 완료. 완료 조건 중 실 시딩·3거래일 배치만 키 대기.

## [2026-08-28] feat | Phase 2 — 차트 v1 + JWT 인증 스캐폴드

- 작업 내용: JWT 인증(register/login/refresh 회전, Secure httpOnly 쿠키), 차트 레이아웃·드로잉 저장 API(소유자 격리, 1MB 상한), 지표 모듈 py(app/strategy/indicators.py — SMA/EMA/Wilder ATR/RSI/σ·σ_down) + TS(lib/indicators.ts) 동일 수식, 픽스처 교차 검증, Lightweight Charts v5 차트 페이지(캔들+MA/EMA 오버레이+거래량+RSI 페인, 상승 적/하락 청, 수평선 드로잉 저장), 로그인 페이지. 마이그레이션 0002(users/chart_layouts/chart_drawings).
- 변경 파일: apps/api/app/{auth,charts,quotes}.py, app/strategy/, alembic 0002, tests 3종 / apps/web/{lib,app/chart,app/login,tests}
- 테스트 결과: `docker compose run --rm api pytest -q tests/` → **29 passed**. `docker compose run --rm web npx vitest run` → **4 passed** (py↔TS 오차 <1e-8). 화면 스모크: /chart·/login 200, 7서비스 healthy.
- QA 결과: 소유자 격리(타인 레이아웃 미노출·무토큰 401) 자동 검증. 60fps 수동 계측은 시딩 데이터 확보 후 (qa/manual-test-cases).
- Git commit: feat: chart v1 with JWT auth and indicator cross-validation (Phase 2)
- 특이사항: 드로잉 5종 중 수평선만 v1 구현(4종 TODO 백로그 등록). ATR=Wilder 채택.

## [2026-08-28] feat | Phase 3 — RAVG v2 전략 모듈 + 백테스트 엔진 + 3스텝 위저드

- 작업 내용: 전략 순수 함수 모듈(app/strategy/ — params·regime 상태머신·planner 주문표·backtest 시뮬레이터), 체결 규칙(D1~D3·갭 필터 우선), 비용 모델(수수료·지정가 슬리피지 0·레버리지 15.4% 단순과세·보수 일할), KPI(FIFO 라운드트립), 절제 플래그 5종, Celery 잡(진행률 1% 발행·취소·단일 트랜잭션 저장·data_fingerprint/stale), WS /ws/backtests/{id}, 3스텝 위저드 UI(오버레이 4개 정규화 비교·CSV·복제). 마이그레이션 0003.
- 변경 파일: apps/api/app/strategy/{params,regime,planner,backtest}.py, app/backtests.py, worker.py, models.py, alembic 0003, tests 3종 / apps/web/app/simulator/
- 테스트 결과: `pytest -q tests/` → **67 passed** (신규: 플래너 골든·경계 20, 시뮬레이터 13, 잡 API 5). **신규 DB에서도 67 passed** (CI 동등성 확인). 성능 실측: 5년 99ms / 10년 176ms (목표 5s).
- QA 결과: 골든 G1~G3·레짐 전이 전수·E=1.0 경계·워밍업·σ floor·갭 지시문·look-ahead 마스킹·재현성·비용 단조성·절제 독립성 자동 검증.
- Git commit: feat: RAVG v2 strategy engine, backtest jobs and wizard (Phase 3)
- 특이사항: v1 백테스트는 RAVG v2 전용(범용 조건식 백로그). 실데이터 절제 리포트는 시딩(키) 후 Phase 4에서.
