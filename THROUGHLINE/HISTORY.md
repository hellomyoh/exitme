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
