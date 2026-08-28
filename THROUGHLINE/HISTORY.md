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

## [2026-08-28] feat | Phase 4 — 일일 시그널 엔진 + 주문표

- 작업 내용: `run_backtest(plan_final=True)`로 최신 종가 기준 계획 생성(전략 코드 단일 소스 — 절단 백테스트와 바이트 동일 구조 보장), 시그널 배치(daily_ingest 성공 시 자동 체인, batch_runs 기록, MISSING/FAILED/INSUFFICIENT_HISTORY 명시 상태), signal_snapshots append-only + is_current partial unique, order_sheets, `GET /signals/daily`(로그인 필수)·`/signals/history`(재계산 기반), 주문표 화면(레짐 배지·E 게이지·주문 테이블·조건부 지시문·계산 근거). 마이그레이션 0004.
- 변경 파일: apps/api/app/signals.py(신규), worker.py, models.py, strategy/backtest.py(plan_final), alembic 0004, tests/test_signals.py / apps/web/app/signals/
- 테스트 결과: `pytest -q tests/` → **72 passed** (신규: R2 절단=전체 동일성 3컷, 배치 스냅샷·버전 체인 유일성·MISSING·API 인증)
- QA 결과: is_current 유일성(재실행 v2 승격) 자동 검증. 30분 배치 실측·절제 5종 실데이터 리포트는 KIS 키 대기.
- Git commit: feat: daily signal engine and order sheet (Phase 4)

## [2026-08-28] feat | Phase 5 — 실전매매 기록

- 작업 내용: AES-GCM 필드 암호화(EncryptedBigInt — 수량·단가·금액·실현손익·목표/손절), 거래 원장(buy/sell/deposit/withdraw), FIFO 로트 매칭·실현손익, 초과 매도 거부, XIRR(이분법)·TWR(일별 체인 재구성), 포지션 카드(연환산 30일 억제·최고/최저 도달·목표/손절 진행 바), 백테스트→실전 전환(파라미터·backtest_id 사본), 포트 UI + 차트 평단선 연동. 마이그레이션 0005.
- 변경 파일: apps/api/app/{crypto,portfolios}.py, models.py, alembic 0005, tests/test_portfolios.py / apps/web/app/portfolio/, simulator(전환 버튼), chart(평단선)
- 테스트 결과: `pytest -q tests/` → **83 passed** (신규 11: XIRR/TWR 수기 대조, FIFO 수기 대조 25만원, 암호화 at-rest 원시 조회 평문 0건, 초과 매도 409, 격리, 전환, 목표/손절)
- QA 결과: DB 원시 조회 평문 미검출 자동 검증. 매수 마커·음영은 TODO 백로그.
- Git commit: feat: trading journal with FIFO ledger and encryption (Phase 5)

## [2026-08-28] feat | Phase 6 — 자산 대시보드 + 마감 QA

- 작업 내용: 일별 자산 스냅샷(암호화, 배치 KST 16:40 + 열람 시 최신화), /dashboard(총자산·전일대비·구성), /portfolio/trend·calendar, 기타 자산 CRUD, 분석 이벤트 3종(visit·backtest_run·portfolio_created_from_backtest), 벤토 대시보드 UI(히어로·도넛·레짐 게이지·추이·손익 캘린더), 홈 내비게이션. 마이그레이션 0006.
- 변경 파일: apps/api/app/dashboard.py(신규), models.py, worker.py, backtests.py, portfolios.py, alembic 0006, tests/test_dashboard.py / apps/web/app/{dashboard,page.tsx}
- 테스트 결과: `pytest -q tests/` → **88 passed** / `npx vitest run` → **4 passed**. 전 화면 7종 HTTP 200, compose 7/7 healthy, nginx 경유 API 정상.
- QA 결과: 총자산=구성합 일치·전일대비·기타 자산 격리·이벤트 적재 자동 검증. 접근성·반응형 수동 QA는 릴리즈 시점 수행 예정.
- Git commit: feat: asset dashboard with snapshots and analytics events (Phase 6)
- 특이사항: nginx 업스트림 stale 502 — 재생성 후 nginx 재시작 필요(NOTES 기록, resolver 개선 TODO).

## [2026-08-28] feat | KIS 실데이터 운용 개시 — 시딩·1분봉 파이프라인·ETF 옵션·절제 리포트

- 작업 내용: (1) KIS 키 검증(실전 토큰·현재가) + Redis 공유 토큰 캐시(발급 분당 1회 403 대응)·0.15s 스로틀·500 지수 백오프. (2) 일봉 10년 실시딩 3종목 — 테스트 합성 데이터 오염 발견·복구(source='pykrx' 삭제), 테스트 DB 격리(stocklab_ci) 규칙화(qa/README). (3) 1분봉 파이프라인: FHKST03010230(120건/호출·1년 보관 실측), ohlcv_intraday 하이퍼테이블(0007), 증분 수집기(scripts.seed_minutes — DB 최신 ts 이후만 API 호출·하루 단위 체크포인트), daily_ingest 당일 분봉 자동 수집, /ohlcv timeframe=1m. (4) ETF 선택 옵션(KODEX/TIGER, 레버리지 공통) — API param·worker·UI 3화면. (5) 절제 5종 실데이터 리포트(docs/ablation-report-20260828.md) — Phase 4 게이트 완료. (6) nginx resolver 동적 업스트림, 첫 화면 → 대시보드 + 전역 내비(사용자 지시). (7) compose YAML 우발 손상 복구.
- 변경 파일: apps/api(kis_auth/kis_client/ingest/seed/seed_minutes/backtests/worker/main/models, alembic 0007, tests 2종+conftest), apps/web(layout/page/chart/simulator/portfolio), infra/nginx, THROUGHLINE(docs 리포트·qa·NOTES·TODO·ASSUMPTIONS·PLAN·PROGRESS)
- 테스트 결과: `pytest -q tests/` (stocklab_ci) → **93 passed** / vitest 4 passed. 실데이터: 일봉 3×2,367행(연 242~248·가격 연속성 검증), 분봉 069500 92,581·122630 92,524행(1년 완전, 오늘 381봉). 절제 7케이스 실행.
- QA 결과: 전 화면 200, / → 대시보드 리다이렉트, api 재시작 후 nginx 무재시작 200(동적 업스트림 검증), 시그널 배치 OK(2026-08-28 NEUTRAL·E 0.428), TIGER 백테스트 fingerprint 분리 검증.
- Git commit: feat: live KIS data ops, minute pipeline, ETF option (KODEX/TIGER)
- 특이사항: KRX pykrx 차단 지속. 절제 결과 ③ 레짐 판정은 v1 우세(+11.3%p) — TODO '레짐 판정 방식 재검토' 등록. 102110 분봉은 백그라운드 수집 중.
