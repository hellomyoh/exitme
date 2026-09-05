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

## [2026-08-28] fix | 차트 dispose 크래시 + Windows 핫리로드 미반영

- 작업 내용: (1) "Object is disposed" — lightweight-charts remove() 후 ref 미초기화로 이중 remove → 차트/시뮬레이터/대시보드 3화면에 dispose 헬퍼(try/catch + ref null, cleanup 통일) 적용. (2) 옛 화면 서빙 — Windows bind mount 파일 이벤트 미전달로 Next dev stale 컴파일 → WATCHPACK_POLLING 적용. 두 증상의 결합이 "모든 메뉴에서 차트만 보임"(크래시로 클라이언트 라우팅 마비 + 리다이렉트 미반영)의 원인.
- 변경 파일: apps/web/app/{chart,simulator,dashboard}/page.tsx, docker-compose.override.yml
- 테스트 결과: / → 307 /dashboard, 전 페이지 200, 전역 내비 렌더, **핫리로드 실검증**(소스 수정 8초 내 반영 후 원복). 코드 구조상 이중 remove 불가.
- Git commit: fix: chart disposal crash and Windows hot-reload staleness

## [2026-08-28] fix | 백테스트 QUEUED 고착 — worker 이미지 stale

- 작업 내용: 백테스트가 QUEUED에서 멈춤 — worker 로그에서 `ModuleNotFoundError: cryptography` 확인. 원인: api/worker/scheduler 가 서비스별 개별 이미지를 빌드해 worker/scheduler 가 Phase 0 시점(3시간 전) 이미지로 실행 중이었음(그동안 `build api`만 재빌드). compose 를 단일 공유 이미지(`image: stocklab-api`)로 통일하고 force-recreate, 멈춘 잡 3건(44~46) 재큐잉.
- 변경 파일: docker-compose.yml, THROUGHLINE(NOTES)
- 테스트 결과: worker cryptography import OK, 재큐잉 잡 3건 전부 DONE(#46 KODEX +131.6% — 절제 리포트와 일치), **HTTP 엔드투엔드 신규 잡(#48 TIGER 2020~2026) DONE +92.3%** (api→redis→worker 전체 경로), worker/scheduler healthy.
- Git commit: fix: share single image across api/worker/scheduler

## [2026-08-28] feat | UI 전면 개편 — Tailwind v4 디자인 시스템 (사용자 지시)

- 작업 내용: 외부 레퍼런스 조사(2026 다크 대시보드/트레이딩 UI 패턴 — 표면 위계·낮은 대비 보더·무거운 웨이트·단일 액센트·점진 공개) 후 전면 개편. globals.css @theme 토큰(bg/surface/raised·line 8%·accent 앰버·상승적/하락청), 공용 프리미티브(components/ui.tsx — Card/Stat/Badge/Callout/GaugeBar/EmptyState + 포맷터), 전역 내비(active 상태·blur). 7개 화면 재구축: 주문표(레짐 히어로+배분 스택바+주문 테이블 배지+지시문 콜아웃+지표 한글 라벨·단위 포맷), 대시보드(벤토·에어리어 추이차트), 시뮬레이터(스텝 인디케이터·ETF 카드 선택·플래그 리스트·KPI 타일), 실전매매(스탯 8타일·포지션 카드·목표/손절 밴드), 차트(세그먼트 프리셋·MA 레전드), 로그인(카드).
- 변경 파일: apps/web/{app/globals.css, postcss.config.mjs, package.json, components/{ui,nav}.tsx, app/*(7화면)}
- 테스트 결과: next build green(타입·Tailwind 컴파일), / → 307 /dashboard, 전 페이지 200, 디자인 시스템 클래스 렌더 확인, web 로그 에러 0. vitest 4 passed(지표 — UI 변경 무관 확인).
- QA 결과: 반응형 그리드(md/sm 브레이크포인트)·AA 대비 토큰 적용. 실기기 수동 QA는 릴리즈 체크리스트에서.
- Git commit: feat: full UI redesign with Tailwind v4 design system

## [2026-08-28] feat | 라이트 테마·세션 유지·백테스트 매매 저널 + 멱등 수정 (사용자 지시 4건)

- 작업 내용: (1) 디자인 토큰 라이트 퍼스트 전환(백색 카드·잉크 10% 보더·앰버700 액센트, 차트 색 전면 교체). (2) 세션 유지 — refresh 쿠키 1h 롤링 + ensureSession(새로고침 시 silent refresh) 전 페이지 적용. (3) 백테스트 일자별 매매 저널 — 엔진에 체결(fills)·일별 현금/보유량 수집 추가, `GET /backtests/{id}/journal`(계획 주문표+체결+일간/누적 수익률+보유, 결정론 재계산), 시뮬레이터에 일자 접이식 리스트(기본 닫힘·거래 있는 날만 토글·더 보기). (4) 전체 사이즈 확대(본문 15px·타이틀 2xl·max-w-7xl·테이블 15px).
- 버그 수정: acks_late 재전달로 완료 잡이 재실행되어 UniqueViolation → FAILED 덮임 — DONE 재전달 무시 + 저장 전 잔여 행 삭제(명세 §8 멱등 준수). FAILED/QUEUED 잔여 18건 재큐잉 → 전량 DONE.
- 변경 파일: apps/api(auth.py TTL, worker.py 멱등, backtests.py journal, strategy/backtest.py fills), apps/web(globals.css, ui/nav, 6페이지), tests/test_backtest_api.py(journal)
- 테스트 결과: api **94 passed**(신규 journal — 계획·체결·보유 필드, 409), web 4 passed. 실검증: 쿠키 Max-Age=3600, 저널 1,633일·체결일 252일(#48 TIGER +92.3% 일치), 전 페이지 200, 잡 상태 DONE 42/CANCELED 9/FAILED 0.
- Git commit: feat: light theme, 1h session, daily trade journal; fix job idempotency

## [2026-08-28] feat | 주문표 재설계·수익그래프 분리·다중 포트 삭제 등 (사용자 지시 6건)

- 작업 내용: (1) 주문표 재설계 — 기준 안내 배너(모델 포트 = 초기 1억 가상 계좌), 모델 포트 현황 카드(평가액·현금·보유 — 시그널 detail 확장), **내 투자금 입력 → 내 계좌 기준 수량 환산 열**(localStorage), 주문별 "실행 조건" 설명 열. (2) 레짐 차이 도움말 — 클릭 접이식("상승/중립/하락 기준 보기", 롤오버→클릭 정정 반영). (3) 수익률 그래프에 종목 추세 — **하단 서브페인으로 분리**(전략 곡선 가림 방지, 면적형). (4) 실전매매 다중 포트 — `POST /portfolios`(생성)·`DELETE /portfolios/{id}`(거래·로트·메타 연쇄 삭제), UI 추가/삭제(확인 대화). (5) 시뮬레이터 폭 유지 — Step1 2컬럼 풀폭·Step2 풀폭·min-h 60vh(그래프 없어도 레이아웃 유지).
- 변경 파일: apps/api(signals.py detail, portfolios.py CRUD, tests), apps/web(signals/simulator/portfolio/dashboard, ui.tsx Tip·RegimeTip)
- 테스트 결과: api **95 passed** (신규: 다중 포트 생성·삭제 연쇄·격리), web 빌드 green, 전 페이지 200·로그 에러 0.
- Git commit: feat: order sheet redesign, per-day capital scaling, multi-portfolio delete
- 후속(같은 날): 매매 저널 주문/체결 구분선(세로 실선·모바일 점선) + 체결일 당일 손익 금액(요약 행·체결 헤더, day_pnl 필드 추가). api 7/7·전 페이지 200 검증. commit: feat: journal divider and daily pnl amount

## [2026-08-28] feat | 실전매매 개편 — 전환 이어받기·시작 메뉴·거래 내역·주문표 섹션 (사용자 지시 7건)

- 작업 내용: (1) 백테스트→실전 전환이 **종료 시점 상태를 시드** — 현금+보유 로트(원 체결가·일자)를 입금·매수 거래로 자동 등록, 총자산 = 백테스트 최종 평가액(±1% 검증). 미완료 잡 전환 409. run_backtest에 final_lots 추가. (2) 실전매매 **날짜별 거래 내역**(접이식·당일 실현손익 합계, GET /portfolio/transactions). (3) 실전매매 중간에 **오늘의 주문표 섹션**(전체 주문표 링크). (4·5) **새 실전매매 시작 패널** — "오늘부터 새로 시작"(기록 없음·선택 입금) / "현재 보유분 입력하고 시작"(종목·수량·평단 다행 입력 → 입금+매수 자동 등록). (6) 시뮬레이터 기록에 **입력 조건 표시**(기간·자본금·ETF·절제 OFF) + 결과 상단 조건 요약. (7) 자산곡선 **시리즈 on/off 토글**(전략/매수보유/종목 추세) + 오버레이·시리즈 의미 설명문.
- 변경 파일: apps/api(strategy/backtest.py final_lots, portfolios.py 전환 시드·transactions), tests / apps/web(portfolio·simulator)
- 테스트 결과: api **95 passed** (전환 시드 검증 — 총자산≈최종 평가액·시드 거래 존재, 미완료 409). 전 페이지 200·웹 로그 에러 0.
- Git commit: feat: live-trading revamp - conversion carry-over, start panel, tx history

## [2026-08-28] feat | 백테스트 기록 삭제 (사용자 지시 8)

- 작업 내용: `DELETE /backtests/{id}` — 자산곡선 연쇄 삭제, 전환된 실전 포트는 backtest_id 링크만 해제(포트 보존), RUNNING은 409(취소 먼저). 시뮬레이터 기록 행에 삭제 버튼(확인 대화, 현재 보던 결과면 Step 1 복귀).
- 테스트 결과: api **96 passed** (신규: 연쇄 삭제·포트 링크 해제·타인 404). /simulator 200.
- Git commit: feat: delete backtest records

## [2026-08-28] feat | 주문표 역할 재정의 — 내 실전 포트 기준 주문 + 신호 이력 (사용자 검토 반영)

- 작업 내용: 사용자 검토("주문표가 모델 기준 신호뿐 — 실전에 필요한 건 내 계좌 주문") 타당 판정. (1) `GET /signals/daily?portfolio_id=` — 실전 포트의 보유 로트·현금을 플래너 Portfolio 로 변환해 전략 규칙 그대로 실행(보유 로트 → 익절 주문 생성·잔여예산 반영, 근사 규칙 ASSUMPTIONS). (2) `GET /signals/journal` — 모델 포트 최근 매매 이력(계획·체결·수익률·보유). (3) 주문표 화면: 기준 선택(모델/내 실전매매), 내 계좌 현황 카드, "왜 이 주문인가" 상태 설명(보유 0 → 신규 그리드만), 최근 신호 이력 접이식. (4) 시뮬레이터 기본 시작일 = 1년 전.
- 테스트 결과: api **97 passed** (신규: 보유 로트 → tp 주문 생성·모델과 주문 상이·journal 필드). 전 페이지 200, journal 5일 실검증.
- Git commit: feat: portfolio-basis order sheet and signal history

## [2026-08-28] fix | 보유분 시작 기준가 옵션 + 스탯 타일 줄바꿈

- 작업 내용: (1) 보유분 입력 시작 시 평단을 비우면 **오늘 종가로 자동 등록 → 수익률 0%부터 추적**(실제 평단 입력 시 기존 수익 반영 — 두 의도 지원, 설명문 병기). (2) 스탯 타일 금액 줄바꿈("…650 / 원") — 값 whitespace-nowrap + 크기 19px, 포트 스탯 그리드를 auto-fit(minmax 150px)로 변경해 타일 단위로만 줄바꿈.
- 테스트 결과: 웹 빌드 green, /portfolio 200, 로그 에러 0.
- Git commit: fix: zero-start holdings option and stat tile wrapping

## [2026-08-28] feat | 실전매매 주문표 포트 기준 전환 + 수익률 그래프

- 작업 내용: (1) 사용자 질문("매도가 계산에서 제외된 이유") 검증 — 매도 규칙(익절·축소·레버리지 청산)은 전부 보유 전제이며, 보이던 주문표가 모델 포트(보유 0) 기준이라 매도가 없었음. 동일 보유 복제 포트로 실측: 내 포트 기준 조회 시 매도 3건(lev_liq 45주·tp 95,680원 680주·reduce 328주) 생성 확인. **실전매매의 "오늘의 주문표" 섹션을 선택 포트 기준으로 전환**(기본 계좌 포함). (2) 실전 수익률 그래프 — `GET /portfolio/equity`(TWR 지수 100 기준, 입출금 왜곡 제거·중간 입금에도 지수 연속 테스트), 카드 차트(30일 이하 포인트 마커, **오늘 시작 포트는 1일 안내 카드**로 표시).
- 테스트 결과: api **98 passed**, 전 페이지 200, 로그 에러 0.
- Git commit: feat: portfolio-basis order section and live equity curve

## [2026-08-28] fix | 매도 이중 계상·실전 익절 기준가 + 스탯 툴팁 (사용자 검증 반영)

- 작업 내용: (1) 사용자 검증 정당 — 익절 680 + 축소 328 = 1,008주 > 보유 680주(백테스트 체결 순서상 결과는 정상이나 주문서로서 이중 계상). 플래너를 축소 선확정 → 익절은 축소분(FIFO 선점) 제외 잔여에만 발행으로 수정, feature §5.5에 규칙 명문화. (2) 실전 로트 익절 기준가를 평단×(1+Grid) → **최근 종가×(1+Grid)**(정본 §5.6 준용)로 정정 — 과거 매수분이 시작 즉시 전량 익절되는 결함 해소. 목표 수익률은 고정이 아니라 "기준가 + 그날의 Grid%(ATR 기반 0.8~4%)". (3) 실전매매 스탯 8타일에 롤오버 도움말(Tip) 추가.
- 검증: 동일 보유 복제 재조회 — 레버 청산 45 / 축소 328 / **익절 352 @ 111,470원**, K200 매도 합계 = 680 = 보유 ✓. api **99 passed**(신규: 매도 합계 ≤ 보유).
- Git commit: fix: sell double-count, live tp basis, stat tooltips

## [2026-08-28] fix | 알고리즘 계산 공식 전수 검증 — 4에이전트 교차 검토 반영

- 작업 내용: 독립 검증 에이전트 4개(정본 대조·수치 재현·시뮬레이터 회계·실전 회계) 병렬 교차 검토 → 치명 2(레버리지 E≤1 방치, 전환일 밴드 트랩) + 중 10 + 경 다수 수정. 지표·레짐·E/w·그리드·체결 판정식은 전부 수치 일치 판정. 상세: [docs/formula-verification-20260828.md](docs/formula-verification-20260828.md).
- 주요 수정: 레버리지 매도 경로 게이트 재구성(E≤1 전량 청산·전술 이탈 상시 평가), 전환일·BEAR 축소 밴드 우회, 전환일 core 익절 발행, 목표 equity 기준(버퍼=현금 예약), 갭 임계 정확값 판정, 매수 수수료 pnl 귀속, KPI 워밍업 제외, TWR 기시흐름 규약(day0 포함), 대시보드 Dietz 흐름 조정, 실전 FIFO(opened_at)·소급 매도 409·출금 잔고 검증, XIRR 브래킷 확장, KST 날짜.
- 테스트 결과: 회귀 15건 신규 — api **114 passed** (stocklab_ci). 절제 리포트 재산출: FULL +131.6%→+140.2%, MDD −27.86%→−25.99%, 샤프 0.73→0.84.
- Git commit: fix: formula verification fixes across strategy and accounting

## [2026-08-28] fix | 시그널 이력 섹션 기준 연동 (사용자 검토 반영)

- 작업 내용: 주문 기준을 내 포트로 바꿔도 "최근 신호 이력"이 모델 시뮬 이력을 그대로 보여줘 내 계좌 기록으로 오독될 여지 — 기준=내 포트면 **그 포트에 실제 기록된 매매(체결)만** 날짜별로 표시하고, 시작 시드(보유분 등록·백테스트 이관)는 매매가 아니므로 제외. 오늘 시작한 포트는 빈 상태 안내("아직 매매 이력이 없습니다"). 기준=모델일 때만 기존 모델 이력 표시(제목에 "내 계좌 기록 아님" 명시). 기준 변경 시 이력 상태 초기화.
- 테스트 결과: tsc 무오류, /signals 200.
- Git commit: fix: signal history follows order basis

## [2026-08-28] fix | 조건부 지시문 기준 연동 + 백테스트 워밍업 선행 로드 (사용자 검토 반영)

- 작업 내용: (1) 조건부 지시문이 보유·주문과 무관하게 항상 3건 출력 — 그리드 매수 주문이 있을 때만 갭 취소, 레버리지 보유가 있을 때만 σ20·레짐 청산 지시문 표시. 청산이 이미 주문표에 포함된 날은 "이미 반영됨, 장중 감시 불요"로 대체, 해당 없으면 빈 상태 안내. 값 자체(갭 임계·σ20)는 가격만의 함수라 포트 무관이 정당함을 확인 — σ20 104.4%는 2026-07 말 시장 급변(3개 ETF 교차 검증) 실데이터로 판명. (2) **시뮬레이션 미동작 결함**: 백테스트가 요청 구간 안의 봉만 로드해 1년 이하 구간이 전부 워밍업(270거래일)에 잠식 → 거래 0·곡선 평평. `load_bars_with_warmup`(시작일 −460일 선행 로드 + run_backtest start_index)으로 수정 — 잡 실행·저널·지문·전환 4경로 일괄 적용. 기존 결과는 지문 변경으로 stale 처리되어 재실행 유도.
- 검증: 실데이터 1년(2025-08-28~2026-08-28, 6천만) — 선행 306봉, 곡선 243일(요청 시작일부터), 거래 68건. api **115 passed** (신규: 단기 구간 거래 발생 회귀).
- Git commit: fix: conditional directives follow basis; backtest warmup preload

## [2026-08-29] feat | 실전매매 일지 개편 + 주문표 카드 정비 (사용자 지시)

- 작업 내용: (1) **일자별 매매 일지** — 시뮬레이터 저널과 동일하게 날짜 행에 [장 시작 전 주문표 → 체결·입출금 → 일간 수익률·실현손익]을 통합. 주문표는 조회 시점에 `portfolio_plans`(0008 마이그레이션, 포트·날짜 upsert)로 스냅샷 보존해 다음 거래일 키로 기록. `GET /portfolio/journal` 신설(계획+체결+TWR 일수익 병합). 상단의 단독 '거래 등록' 카드와 '날짜별 거래 내역' 카드는 제거. (2) 오늘의 주문표 각 행에 **[체결 등록]** 버튼 — 구분·종목·수량·지정가를 채운 입력 폼이 열리고, 장 마감 후 실제 체결만 등록하면 다음 주문표에 반영. 입출금·직접 입력은 같은 카드의 접이식으로 이동. (3) 시그널 **목표 배분 카드 재구성** — "실효 노출 E ⓘ 42.8% / 한도(중립장) 65%" 라벨-게이지, 자산 구성은 굵은 스택 바(구간 내 % 레이블)+범례. 수치 검증: E=0.5×(0.13/0.4553)+0.5×(0.2599/0.4553)=0.4282 ✓, w200=E(중립) ✓, 현금=1−E ✓. (4) **내 투자금** 입력 설명 재작성 — "내 투자금 ÷ 모델 평가액" 비율 어림 환산임을 명시(5,000,000/240,230,824×333=6.93→6주 검증), 정확 계산은 포트 기준 선택 안내.
- 테스트 결과: api **116 passed** (신규: 일지 계획+체결 동반), tsc 무오류, 전 페이지 200.
- Git commit: feat: daily trading journal with plan snapshots and fill entry

## [2026-08-29] feat | 거래 오입력 정정(삭제→재생) + 포트 삭제 FK 수정

- 작업 내용: (1) 수동 등록 거래의 오입력 정정 — `DELETE /positions/{tx_id}` 신설. 등록이 로트를 즉시 변형하는 구조라 역산 대신 **남은 거래 전체를 시간순 재생**해 로트·실현손익·현금을 재구성(`_rebuild_ledger`). 재생 불가(매도가 보유 초과·출금이 현금 초과)면 409 + 어떤 거래를 먼저 지워야 하는지 안내. 일지의 체결 행에 ✕ 삭제 버튼(확인창) 추가 — 수정은 삭제 후 재입력 흐름. (2) 포트 삭제가 `portfolio_plans` FK 위반으로 실패 — 스냅샷 동반 삭제.
- 테스트 결과: api **118 passed** (신규: 삭제→재구성, 근거 매수 삭제 409 후 일관성, 스냅샷 있는 포트 삭제).
- Git commit: feat: transaction delete with ledger replay

## [2026-08-31] docs | 미국 이식 백테스트 (QQQ+QLD / QQQ+TQQQ) — KIS 해외 TR 수집

- 작업 내용: KIS 해외주식 기간별시세(HHDFS76240000)로 QQQ·QLD·TQQQ 전 이력(2007-08~) 수집(Yahoo 대신 한투 API — 사용자 지시). 센트 정수 스케일·미국 비용 모델로 현행 파라미터 그대로 스모크 백테스트. `Params.lev_multiple` 신설(3배 ETF 가치 ×2/3 보정, 기본 2.0 불변). 결과: 두 케이스 성립 — MDD −22% vs QQQ 매수보유 −53%(2008 포함), CAGR 10% vs 17%(방어 대가), 3배 배율 보정 등가성 실증(B2≈A), 무보정 시 노출 초과 확인. 상세: docs/us-backtest-20260831.md.
- 테스트 결과: 전략 44/44 통과 (기본값 불변).
- Git commit: feat: lev_multiple param and US market backtest study

## [2026-08-31] feat | 미국 ETF 일봉 DB 적재 (QQQ/QLD/TQQQ)

- 작업 내용: `ingest_us_daily`(KIS 해외 기간별시세, 증분·페이지네이션) 신설 후 전량 적재 — QQQ 4,788 / QLD 4,787 / TQQQ 4,166봉 (2007-08~2026-08, 센트 정수·NASDAQ 마켓, ASSUMPTIONS 규약 기록). 증분 재실행 0건·`load_aligned_bars(codes=("QQQ","QLD"))` 로드 검증. 스케줄 배치는 미등록(실험용).
- Git commit: feat: US ETF daily ingestion into DB

## [2026-08-31] feat | 한국/미국 마켓 분리 + 설정 메뉴 + 사이드바 개편 (사용자 지시)

- 작업 내용: (1) **마켓 분리** — `portfolios.market`(KR|US, 0009 마이그레이션), ETF_PAIRS 에 QQQ_QLD/QQQ_TQQQ, 마켓별 기본 파라미터(`base_costs_for`: 센트 호가·미국 비용·배율 3x)·`params_from_job` 일원화. 미국 신호는 라이브 계산(`_live_us_model`, 모델 $1M), 포트 기준 주문표는 포트 마켓 자동 인식(TQQQ 보유 시 3배 파라미터, QLD/TQQQ 혼합 409). KR/US 종목 교차 등록 409(통화 혼합 방지), 대시보드 KRW 스냅샷·흐름은 KR 포트 한정. (2) **설정 메뉴** — 일반(비밀번호 변경 `/auth/change-password`·세션·로그아웃)과 알고리즘(`user_settings` 테이블, PARAM_REGISTRY 23항목: 라벨·롤오버 도움말·범위 검증·기본값 disabled 표시·변경됨 배지·기본값 초기화). 오버라이드는 잡 생성 시점 스냅샷(`params["algo"]`)·포트 주문표·미국 신호에 적용, 공용 KR 모델 배치는 기본값. (3) **웹 개편** — 좌측 고정 사이드바(🇰🇷/🇺🇸/⚙️ 그룹, 모바일 슬라이드 오버), 주문표·시뮬레이터·실전매매가 `?market=US` 로 분기, 통화 표기 헬퍼(`lib/market.ts`: 센트→달러)·마켓별 종목 옵션·차트에 QQQ/QLD/TQQQ 추가. (4) 워커 잡 행 잠금(`with_for_update`) — 동시 실행 시 equity 중복 삽입 FAILED 결함 수정.
- 테스트 결과: api **121 passed** (신규: US 포트 분리·설정 왕복·비밀번호 변경). tsc 무오류, 전 페이지(마켓 변형 포함) 200, 미국 잡 E2E DONE(+126.8%, algo 스냅샷 확인).
- Git commit: feat: KR/US market separation, settings menu, sidebar redesign

## [2026-08-31] change | 레버리지 강제청산 σ 임계 25% → 35% (사용자 승인)

- 작업 내용: 사용자 지적("지수 3배 구간에 레버리지 4회는 과보수") 검증 — 1년 레버리지 실현 1,195만원(전략 수익의 15%) vs ETF 자체 +347%, 병목은 σ20≤25% 관문(상승장 217일 중 59일만 통과). 25→60% 단계 스윕: MDD 전 구간 불변(폭락 시 레짐 이탈이 선행 청산), 45%+ 는 게이트 무발동. **35% 채택**(10년 +140→+150%, 샤프 0.84→0.87, OOS t +1.58) — 안전판 보존과 개선의 절충. params·테스트(σ 0.36/0.40)·시그널 지시문 문구·feature 문서(변경 이력 신설) 일괄 갱신. 참고: 목표σ 0.20 동반안(10년 +210%, KR t_OOS +3.02)은 성격 변경이라 별도 승인 대기.
- 테스트 결과: api 122 passed.
- Git commit: change: raise sigma liquidation threshold to 35%

## [2026-08-31] change | ε=2% MA200 이탈 완충 + 목표σ 0.20 (사용자 승인 — 승인 3종 완결)

- 작업 내용: `ma200_exit_buffer=0.02` 신설(regime.py 이탈 다리 히스테리시스 — 진입·직행 무완충 유지) + `target_downside_vol` 0.13→0.20. 설정 레지스트리 등록, RegimeTip 완충 설명, 테스트 재산(E 공식 3점·경계 s=0.20·밴드 σ0.40) + ε 경계 골든 6종 신설. feature 문서 §5.4·변경 이력, regime-buffer-study 채택 확정 기록.
- **새 기본값 기준 성과** (σ청산 35% 포함 3종 반영): 한국 10년 **+230.8% / MDD −20.6% / 샤프 0.98** (변경 전 +140.2%/−26.0%/0.84), YTD +39.9%, 미국 19년 +720.3%/−24.8%/0.84. 오늘 신호 E 42.8→50.5%.
- 테스트 결과: api **123 passed**. 시그널 재생성 OK.
- Git commit: change: adopt ma200 exit hysteresis and raise target downside vol

## [2026-08-31] feat | 시장별 전략 분리 — 미국 TF(추세 필터 보유) 전략 (사용자 승인)

- 작업 내용: features/feature-us-trendfilter.md 참조 — TF 엔진 신설(trendfilter.py, BacktestResult 재사용), run_engine 디스패치(잡·저널·전환·워커), 미국 신호/포트 주문표 TF 교체(전량 매수/현금, 기준선 지시문), 시뮬레이터 미국 기본 옵션 QQQ_TF(RAVG 페어는 비교용 레거시), 주문 라벨 추세 진입/이탈. 부수 수정: 워커 결과 저장 직전 잠금 재획득 — RUNNING 커밋으로 풀린 잠금 탓에 동시 실행이 equity 중복 삽입하던 결함.
- 테스트: TF 단위 4종 신규, api **127 passed**. E2E: QQQ_TF 잡 +609.2%(2010~)·저널·포트 주문표 확인.
- Git commit: feat: US market switches to trend-filter strategy

## [2026-09-01] change | 전략 명칭 RAVG v2.5 채택 (ADR-007, 사용자 승인)

- 작업 내용: 2026-08-31 확정 개정 3건(σ청산 35%·목표σ 0.20은 정본 §10 튜닝 범위, `ma200_exit_buffer` ε=2%는 §4 상태머신 구조 변경) 중 ε 신설이 정본 튜닝 표 밖의 규칙 추가이고 성과 프로파일이 크게 달라져(10년 +140.2→+230.8%, MDD −26.0→−20.6%, 샤프 0.84→0.98) 버전 경계가 필요하다고 검토 — 사용자 결정으로 **v2.5** 채택. [ADR-007](adr/007-ravg-v25-adoption.md) 신설(개정 목록 고정·권위 체인 = 정본+ADR-007 > feature), 명칭 스윕(웹 UI 6곳·전략 모듈 독스트링 3곳·README/AGENTS·가이드 3종·feature 2종·인덱스). 정본·과거 일자 리포트·discussion 로그는 소급 개명하지 않음. 엔진 동작·파라미터 기본값 변경 없음.
- 테스트 결과: api 124 passed / 3 failed — 전부 사전 존재(변경 전 코드로 재현 확인): `test_from_backtest_conversion_seeds_state`(시딩 데이터 의존 equity 괴리), ws·journal 2건은 전체 실행에서만 실패하는 격리 문제(단독·부분 실행 통과). 전략 스위트(planner·backtest·signals) 전부 green. web tsc 무오류.
- Git commit: change: adopt RAVG v2.5 designation (ADR-007, user approved)

## [2026-09-01] feat | 관리자 계정 체계 — 공개 가입 차단·계정 발급·첫 로그인 강제 변경 (사용자 지시)

- 작업 내용: (1) 공개 가입 차단 — 로그인 화면 가입 버튼 제거 + `/auth/register` 기본 403 (`ALLOW_OPEN_REGISTRATION`, 테스트만 개방). 로그인 ID 는 이메일 형식 강제 해제(아이디 로그인). (2) 기본 관리자 부트스트랩 — 기동 시 `myoh`(is_admin) 자동 생성(멱등, 0010 마이그레이션: is_admin·must_change_password). (3) **계정 관리 메뉴**(관리자 전용, 사이드바 조건 노출) — 아이디/임시 비밀번호 지정 발급 + 목록(권한·첫 로그인 대기 상태). (4) 발급 계정 첫 로그인 강제 변경 — 로그인 응답 플래그 → 설정 화면 고정(셸 게이트, 다른 메뉴 진입 시 리디렉션), 비밀번호 변경 시 해제·대시보드 진입.
- 테스트: 관리자 플로우 회귀 1종 신규 — api **128 passed**. 스모크: 가입 403·admin 로그인·발급·첫 로그인 True·변경 후 해제·비관리자 403.
- Git commit: feat: admin-managed accounts with forced first-login password change

## [2026-09-01] docs | 분봉 체결 검증 완료 — 일봉 가정 오차 0.000%p (사용자 지시)

- 작업 내용: 지정가 체결만 1분봉 순서로 재생하는 대조 실행기로 1년(243일) 검증 — 수익·MDD·체결 80건 완전 일치(평가액 괴리 최대 111원). 부수 발견: 분봉(수정주가) vs 일봉(원주가) 가격 기준 불일치(계단형 분배락) — 무보정 시 +13.9%p 허위 개선. 상세: docs/minute-fill-study-20260901.md. TODO 완료 처리.
- Git commit: docs: minute-fill validation study

## [2026-09-01] fix | 테스트 DB 격리 코드 강제 + 개발 DB 오염 복구 (재발 사고)

- 작업 내용: (1) **오염 사고**: 컨테이너 안 `pytest` 직접 실행(v2.5 명명 작업 중의 전체 스위트 실행)이 compose 환경변수(개발 DB)로 통합 테스트를 돌려 `seed_synthetic` 합성 봉이 개발 DB에 유입 — 069500/122630 각 28개(휴장일 날짜만, ON CONFLICT 가 실데이터 날짜 차단), 102110 400개(실데이터 부재로 전량). 2026-08-28 사고의 재발로, 수동 격리 규칙의 한계 확인. (2) **복구**: `DELETE ... WHERE source='pykrx'`(456행, 사용자 실행) + 102110 KIS 10년 재시딩(2,369봉) → 휴장일 봉 0건, 기준 백테스트(+230.8%/−20.63%/0.98) 바이트 재현 확인. (3) **재발 방지(코드 강제)**: `tests/conftest.py`가 DATABASE_URL 의 DB 이름이 `_ci` 미접미면 stocklab_ci 로 강제 재지정 + 격리 DB 자동 생성·alembic 마이그레이션. qa/README·NOTES(탐지 쿼리 3종 포함) 갱신.
- 테스트 결과: 무오버라이드 `docker compose exec api python -m pytest -q` → **127 passed / 1 failed**(ws 플레이크 — 라이브 Redis 공유 경합, 단독 재실행 통과·NOTES 기록). 실행 후 개발 DB 무변화(3종목 kis 2,369봉 유지)·stocklab_ci 에 합성 봉 격리 적재 확인.
- Git commit: fix: enforce test DB isolation in conftest

## [2026-09-02] fix | 대시보드 손익 캘린더 툴팁 미표시 수정

- 작업 내용: "이번 달 일간 손익" 타일이 브라우저 기본 `title` 속성을 사용 — 약 1초 정지해야 뜨고 환경에 따라 미표시(사용자 보고). 앱 공통 group-hover 커스텀 툴팁 패턴(ui.tsx `Tip`과 동일 방식의 소형 변형)으로 교체: 즉시 표시, `날짜 · ±금액`(손익 색상), `role="img"`+aria-label 병기. `.card` 무클리핑 확인 후 타일 상단 중앙 배치.
- 테스트 결과: web tsc 무오류, /dashboard 200 (dev 핫리로드). 수동 QA(hover 즉시 표시)는 사용자 확인 대기.
- Git commit: fix: instant custom tooltip on daily pnl calendar

## [2026-09-02] feat | 대시보드 자산 구분(KR/US)·포트별 추이·손익 비율 (ADR-008)

- 작업 내용: 사용자 지시·결정(A안 $ 별도 표기 / 포트별 다선 / 이중 기준 비율) 반영. Multi-Agent 검토(병렬 서브에이전트 3기 — Backend·DB·QA, [discussion/review-dashboard-asset-breakdown-20260902.md](discussion/review-dashboard-asset-breakdown-20260902.md)) 후 구현: (1) **portfolio_snapshots 신설**(0011 — FK CASCADE·currency 비정규화·ON CONFLICT upsert)과 사용자 스냅샷의 **합산 유도** 재구성([ADR-008](adr/008-portfolio-snapshots.md)) + latest_close 일괄 조회(N+1 제거) + 배치 날짜 kst_today 통일(기존 UTC 결함 수정). (2) /dashboard 에 kr_stock/us_stock 카드(평가액·원가·손익 금액/%, US 는 센트→$ 표기), /portfolio/trend 에 series(포트별, 기존 items 비파괴, ALL 주단위 샘플). (3) /portfolio/summary 에 principal·invested_cost·net_pnl·net_pnl_pct(÷납입원금)·unrealized_pnl_pct(÷보유원가) — 분모≤0 → % null·금액 표시. (4) 거래 등록·삭제 시 당일 스냅샷 즉시 재계산(유령값 방지). (5) 웹: 자산 내용 카드·추이 다선(+범례)·실전매매 카드 % 병기.
- 테스트 결과: api **131 passed / 2 failed**(ws 플레이크 — 단독 재실행 2 passed, NOTES 기록 항목). 신규: 합산 불변식·KR/US 혼합 금지·중복 적재 유일성·삭제 후 스냅샷 정합·포트 삭제 CASCADE·분모 경계 3종. web tsc 무오류, /dashboard·/portfolio 200.
- Git commit: feat: market asset breakdown, per-portfolio trend, pnl ratios (ADR-008)

## [2026-09-02] fix | 오늘의 주문표 시점 동결 — B안: 신호 기준일 종가 시점 상태로 계산 (사용자 승인)

- 작업 내용: 사용자 보고("체결 등록 시 미체결 수량이 즉시 변동 — 오늘 주문은 변경되면 안 되는 것 아닌가") 검토 결과, 포트 기준 주문표가 조회 시점 원장(당일 체결 포함)으로 재계산되고 그 결과가 portfolio_plans 보존본까지 덮어쓰는 이중 결함 확인 — 정본 §8 "종가 신호 → 익일 발주" 주기 위반 + HTS 주문장과 화면 불일치. **B안(기준 시점 고정 재계산)** 채택: `_state_before(pid, exec_day)` 시점 재생(실행일 이전 체결만, FIFO 등록 경로와 동일 의미론)을 RAVG(`_portfolio_orders`)·TF(`_tf_portfolio_orders`) 공통 적용, exec_day 산출 일원화(`_next_exec_day`). 시작 등록(보유분·시작 입금)은 직전 영업일 15:30 KST 로 기록해 당일 주문표에 반영되도록 조정, 주문표 헤더에 기준 시점·"오늘 체결은 내일 반영" 명시. feature-portfolio §5·§12, ASSUMPTIONS 갱신.
- 테스트 결과: api **134 passed / 1 failed**(ws 플레이크 — NOTES 기록 항목, 단독 통과). 신규: 당일 체결 등록 후 주문표 불변 + 소급 등록 반영(자가 치유) + 시점 재생 ↔ 로트 테이블 동등성. 기존 포트 주문표 테스트 1건은 합성 봉 범위 내 시각으로 정정. web tsc 무오류.
- Git commit: fix: freeze today's order sheet at signal-date state (B-plan)

## [2026-09-02] feat | 계획 스냅샷 불변화 + 시뮬레이터 변수·보유 시작·실전 비교 (사용자 지시)

- 작업 내용: (1) **실행일 도래 계획 불변** — portfolio_plans upsert 가 미래 실행분만 갱신, 도래분은 최초 저장본 보존(사용자 보고: 일지의 그날 계획 수량이 사후 재계산으로 덮임 — 파라미터 개정·재조회 경로 확인). 회귀: kst_today 고정 후 재조회에도 orders 불변. (2) **시뮬레이터 확장**: 실행 폼에 알고리즘 변수 수동 입력(설정 레지스트리 기반, 기본값과 다른 항목만 잡 algo 로 — 서버 범위 검증), '보유 상태로 시작'(엔진 initial_lots — RAVG core/lev_strat 시딩, TF K200) — 잡 params 에 스냅샷되어 재현·전환 정합. (3) **실전 비교**: 백테스트에서 전환된 실전 포트가 있으면 결과 차트에 실전 TWR 지수 라인(초록) 오버레이. E2E: algo+holdings 잡 DONE(시작 평가 43.1M = 현금 30M+300주), 범위 초과 422. 주의: celery 워커는 코드 리로드가 없어 백엔드 변경 후 재시작 필요(운영 절차).
- 테스트: 계획 동결 회귀 신규 — api 136 passed 예정 확인.
- Git commit: feat: immutable arrived plans; simulator variables, seeded holdings, live comparison

## [2026-09-03] fix/feat | 시뮬 원금 기준·UX 일괄 수정 + 전량 익절 검토 (사용자 지시, PR #21~#34)

- 작업 내용: ① **보유 시작 시뮬 수익률 결함** — 현금만 분모로 써서 보유 평가액이 통째로 수익으로 잡히던 것(이틀 시뮬 +153.4%)을 원금=현금+보유 원가로 통일(KPI·벤치마크·일지, RAVG/TF), 사용자 시나리오 재실행 +4.80% = 실전 TWR 일치. ② 시뮬 결과에 현재 평가액·수익금 표기. ③ 계획 복구 스크립트(scripts/rebuild_plan.py, 시점 재생 dry-run/--apply) — 원격 27주 사례는 원장 자체가 재등록으로 바뀐 것으로 판명(복구 비대상). ④ 시작 등록 날짜를 최근 종가일로(밤 시작이 전일로 찍히던 문제), 일지 날짜·시각 KST 통일. ⑤ 잡 파라미터 null 크래시 핫픽스(#21). ⑥ UX: 카드 숫자 넘침·모바일 표 잘림(축약으로 한 화면), 포트 드롭다운→탭, '내 계좌(기본)' 별칭 탭 제거, 삭제 버튼 가시성, 새 실전매매 패널 열림 중 기존 내용 숨김, 시작 입금 메모 표시, 신생 포트 XIRR 억제(71만% 사례). ⑦ 세션 롤링 1h→3h. ⑧ **전량 익절 검토**: 10y 측정(익절일 매도 중앙값 100%·전량 59%)으로 단일 로트 근사가 설계 행동과 근접 확인 — 평단 역계산·코어 취급 기각, 부분 익절 운용 지침 문서화 (docs/manual-holdings-tp-review-20260903.md).
- 테스트: 137 passed (보유 시작 원금 회귀 신규), 모바일/데스크톱 헤드리스 검증 다수.
- Git commit: (PR #21~#34 머지)

## [2026-09-03] change | 그리드 예산 가중 50/30/20 채택 (사용자 승인, order-formula-study)

- 작업 내용: 주문표 공식 수리 검토([docs/order-formula-study-20260903.md](docs/order-formula-study-20260903.md) — E 탄력성 −1·배분 도함수·반사원리 체결 확률 캘리브레이션) 후속. 단계별 체결 확률이 실측 25%/6.3%/1.45%로 극단 비대칭인데 균등 1/3은 확률 무관 배분 → 기대 투입률 10.9%/일에 그침. **`Params.grid_weights=(0.5,0.3,0.2)` 신설**로 예산을 체결 빈도에 정렬(기대 투입률 +35%, 평균 심도 −8.6%): 3구간 백테스트 일관 개선(10년 +230.8→+245.5%, 샤프 0.98→1.00, 1구간 −1.1→−0.3%, 2구간 +158.4→+167.9%), MDD −20.6→−21.7% 교환. 길이 부족·합 0 균등 폴백, v1 설정 레지스트리 미노출. feature §5.5·이력, strategy-guide 갱신. E 공식·배분·grid_coef(고원 중앙)·clip·3단 구조는 유지 판정.
- 테스트 결과: api **136 passed / 1 failed**(ws 플레이크 — NOTES 항목, 단독 통과). 골든 갱신: 가중비 수렴 검증 + 균등 폴백 신설. 전략 스위트 55/55 green.
- Git commit: change: adopt weighted grid budget 50/30/20 (user approved)

## [2026-09-04] feat | 매매 도우미 챗봇 — OpenRouter tool-calling (사용자 지시)

- 작업 내용: 우측 슬라이드 챗 패널(플로팅 💬 로 열고 ✕ 로 닫기, 닫아도 대화 유지) + 백엔드 /chat SSE 하네스. OpenRouter(OpenAI 호환) tool-calling 루프(≤6회)로 읽기 전용·user_id 스코프 도구 7종(포트 목록/자산 요약/일지/주문표(실디스패치 동일)/백테스트/알고리즘 설정/시세) 실행. 무상태 서버(이력은 클라이언트, 최근 20턴). OPENROUTER_API_KEY 만 넣으면 동작(미설정 503 안내), OPENROUTER_MODEL 교체 가능. 시스템 프롬프트에 RAVG v2.5·TF 요지와 안전 규칙(數値 날조 금지·투자 권유 아님) 내장. 상세: features/feature-chatbot.md.
- 테스트: chat 하네스 4건 신규 — 전체 142 passed, tsc 클린, 헤드리스(열기/전송/503 말풍선/닫기/대화 유지) 확인.
- Git commit: feat: trading assistant chatbot — OpenRouter tool-calling harness, right-side panel

## [2026-09-05] feat | 포트별 공식 동결 — 시뮬 변수의 실전 승계 (사용자 지시)

- 작업 내용: 시뮬레이터에서 변수를 바꿔 검증한 뒤 '실전매매로 전환'하면 그 변수로 실전 주문표가 계산되도록 구조 변경. **DB 설계**: 새 테이블 없이 portfolios.params["algo"] 를 공식 동결 슬롯으로 정의 — 전환 시 잡의 algo 스냅샷을 항상 명시 저장({} = 기본값 동결), 키 부재 = 구형/수동 포트(설정 추종, 기존 동작 보존). 격리 단위 = 포트 행이라 한 계정에서 여러 공식 동시 운용 시에도 섞이지 않음. 주문표(_portfolio_orders)·계획 복구 스크립트가 동일 규칙으로 해석(개정으로 사라진 키는 무시), 응답에 algo_source/algo_overrides 노출, 실전 화면 캡션에 '공식: 이 포트 고정 (변수 N건)' 표시(툴팁에 값). 알고리즘 설정 변경은 추종 포트에만 적용.
- 테스트: 격리 2건 신규 — 같은 계정 두 포트 상이 공식 동시 운용(grid_coef 2.0 동결 vs 설정 0.3 추종, 그리드 가격 분리)·설정 변경 무영향·유령 키 견고성·전환 동결 저장. 전체 148 passed, tsc 클린.
- Git commit: feat: per-portfolio frozen strategy parameters from simulator conversion

## [2026-09-05] feat | Zenith(shadcn/ui) 스타일 디자인 개편 (사용자 지시, 스샷·dashboardpack 데모 기준)

- 작업 내용: 메뉴·본문 내용은 유지하고 디자인 토큰 층에서 전면 교체 — ① globals.css: 중립 징크 그레이 위계(bg #f6f7f8 · inset 사이드바 · 백색 카드 12px 라운드/연한 그림자), 강조 주황(텍스트 #c2410c AA·차트/마크 #f97316), 주 버튼 검정 솔리드, 상승 적/하락 청 국내 관례 유지. ② 사이드바: 검정 라운드 로고+ExitMe/AUTO TRADING 브랜드 블록, 메뉴별 라인 아이콘(navicons), 활성 = 백색 알약(테두리+그림자), 하단 사용자 카드(아바타·이름·역할·로그아웃 아이콘). ③ 하드코딩 앰버(#b45309) 12곳 → #f97316 (차트 라인·체크박스 등). 토큰 기반이라 전 페이지 일괄 적용.
- 테스트: tsc 클린, 대시보드·실전매매·시뮬레이터 헤드리스 스크린샷 육안 확인(레이아웃 붕괴 없음).
- Git commit: feat: Zenith-style design overhaul — tokens, sidebar, orange accent

## [2026-09-05] feat | 메뉴 개편 + 수동 주식 매매일지 (사용자 지시)

- 작업 내용: ① 한국 아이콘을 태극 문양 원형으로 교체(단순화 국기 도안이 국기로 안 읽힘). ② 한국/미국 그룹을 '주식 실전 매매' 단일 그룹으로 병합 — 서브메뉴 6종 유지, 항목 우측 시장 아이콘으로 구분. ③ **주식 매매일지** 신설(스프레드시트 대체): 일지 생성 시 이름·종목명·증권사·수수료율·제세금율 입력, 매일 입력은 구분·수량·단가(+날짜·이유 선택)만 — 실현손익(FIFO)·수익률·보유기간·비용·합계(총 실현손익/매도/매수/비용)·현재 보유를 서버가 계산. 일지 복수 등록(이름 구분), 보유 초과 매도 422, 삭제 CASCADE. DB 0014(manual_journals/entries), /mjournals API, /mjournal 화면(요약 카드+빠른 입력+기록 표).
- 테스트: FIFO 계산·요율·격리·삭제 2건 신규 — 전체 152 passed, tsc 클린, E2E(메뉴 병합·일지 생성·매수/매도 입력·자동 계산·삭제) 확인.
- Git commit: feat: merged trading menu, manual stock journal with FIFO computation

## [2026-09-05] change | 주문표 메뉴 제거 — 계산 근거를 실전매매로 이관 (사용자 승인)

- 작업 내용: 검토 결과 주문표 화면의 요소가 실전매매(주문 행·B안 기준)·대시보드(레짐)·시뮬레이터(모델 신호, ADR-005 절단 계획 동일성)로 전부 대체 가능 — 유일한 고유 가치였던 '계산 근거(지표값)·주문별 실행 조건'을 실전매매 주문표 카드의 접이식 섹션으로 이관하고 /signals 페이지·메뉴·검색 대상 제거. **백엔드는 전부 유지**(시그널 배치·계획 스냅샷·/signals/daily API — 실전매매·챗봇이 계속 사용).
- 테스트: 153 passed, 헤드리스(메뉴 제거·접이식 근거 표시), tsc 클린.
- Git commit: change: remove order-sheet menu, migrate calculation basis into live trading
