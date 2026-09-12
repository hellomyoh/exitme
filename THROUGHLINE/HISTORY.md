# HISTORY.md

## [2026-09-12] audit | RAVG v2.5 체결률·수익률 수치 검증

- 기준 `2591efa`(제품 `45eb373`). [보고서](docs/fill-rate-study-20260912.md), [QA](qa/fill-rate-study-20260912.md), [집계 JSON](qa/fill-rate-results-20260912.json), 독립 실행 `apps/api/scripts/fill_rate_audit.py` 추가. 제품 전략·설정·실주문 변경 없음.
- 실행: `docker compose exec -T api python -m scripts.fill_rate_audit --windows --sensitivity` → 8변형·87개 252거래일 창·비용 민감도 완료(exit 0). 수량/금액 지표 추가 후 옵션 없는 전 구간 재실행(exit 0). 비용 전부 0인 4변형도 별도 실행 완료.
- 자체 검증: 기본 wrapper 전체 결과 동일, 1,943개 그리드 지정가 일치, 매수 사유 합계 2,046=294+1,702+25+25. READ ONLY 시세 조회만 수행.
- 독립 QA: `docker compose exec -T api pytest -q tests/test_indicators.py tests/test_strategy_planner.py tests/test_strategy_backtest.py tests/test_bootstrap_entry.py tests/test_signals.py::test_truncated_backtest_final_plan_equals_full_run_plan` → **63 passed, 2 warnings in 3.10s**.
- 최종 재검증: 주 에이전트가 같은 테스트를 `python -m pytest -q`로 실행 → **63 passed, 2 warnings in 3.21s**(중복). 최종 연구 스크립트 `--sensitivity` 재실행(exit 0), 전 구간·비용 결과 28개 레코드가 저장 JSON과 동일. 87창 루프는 최종 재실행하지 않음(로직 불변).
- 결론: 체결률 상승이 수익 개선을 보장하지 않음. 현행→전일 종가 지정가 K200 주문선 체결률11.57→73.76%, CAGR16.68→15.87%, MDD−21.67→−28.11%. 비중첩 8창 일부 평균은 반대 방향이므로 보편적 열등·최적성은 주장하지 않음.
- 범위: 전체 브로커/UI·실계좌 QA 미수행. 신규 미사용 기간·현실 비용/분배금·실전 상태 결함 수정 후 재검증 필요. push·배포 없음.

## [2026-09-12] audit | KODEX 200·레버리지 연동 전략 검증

- 기준: `45eb373` / v0.25.0. [감사 보고서](docs/kodex-linkage-audit-20260912.md), [명령·후속 QA](qa/kodex-linkage-audit-20260912.md). 앱 코드·전략·운영 데이터 변경 없음.
- 주 에이전트 실행: `docker compose exec -T api python -m pytest -q tests/test_strategy_planner.py tests/test_strategy_backtest.py tests/test_indicators.py tests/test_bootstrap_entry.py` → **62 passed in 2.34s**.
- 독립 Quant 실행: 같은 범위 + `tests/test_signals.py::test_truncated_backtest_final_plan_equals_full_run_plan` → **63 passed, 2 warnings in 2.93s**. 중복 포함으로 두 수를 합산하지 않음.
- READ ONLY 실제 시세 백테스트: 2017-01-02~2026-09-10 각 2,374봉, 1억원. FULL 누적 +262.49%/CAGR 16.68%/MDD −21.67%, 레버리지 OFF +209.30%/14.48%/−18.58%. 비용·분배금 모델 한계 때문에 미래 수익·실전 세후 성과를 보증하지 않음.
- 별도 반례: E 목표 1.10·실제 근사 노출 1.16·기존 전술 보유에서 주문 0건 재현. 실전 트랙 소실·익절 스냅샷·현금 예약·데이터 무결성 후속 조건 기록.
- QA: 전체 API·브로커·UI·실계좌 검수 미수행. 감사만 완료, 결함 수정 미착수. 배포·실제 주문·push 없음.

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

## [2026-09-05] feat | 증권사 조회 연동 — 체결 자동 가져오기 + 주문표 대조 경고 (사용자 지시)

- 작업 내용: 시장 조사(docs/market-research)에서 도출된 "조회 전용 연동" 을 구현. ① **계획 vs 등록 체결 대조**: 실행일이 도래한 최신 계획 스냅샷과 그날 등록 거래를 레그·방향별로 비교해 미이행/계획 외/수량 불일치를 주문표 카드 상단 경고 배너로 표시(**표시만 — 자동 수정 없음**, 사용자 지시). ② **체결 자동 가져오기**: KIS 일별주문체결조회(TTTC0081R 계열, 연속조회 페이징·응답 필드 방어적 파싱)로 최근 N일 체결을 조회해 미리보기 → 실행. broker_ref(주문번호:일자) 유니크로 멱등, 등록 후 _rebuild_ledger 로 로트·FIFO·실현손익 일관성 유지. ③ 포트당 증권사 자격(앱키·시크릿·계좌번호)을 AES-GCM 암호화 컬럼(EncryptedText, 0016)에 저장하고 응답은 마스킹. **주문 TR 은 구현하지 않음** — 자동 발주 미도입 원칙 유지.
- 테스트: 대조 4케이스(일치/미이행/수량 불일치/계획 외)·자격 CRUD·마스킹·타인 접근 404·미연동 409·미리보기 무등록·중복 방지 — 전체 159 passed, tsc 클린, 헤드리스 UI 확인.
- Git commit: feat: read-only broker link — auto fill import and plan reconciliation warnings

## [2026-09-05] change | 증권사 계좌를 설정에서 관리 — 실전매매는 선택만 (사용자 검토 요청)

- 작업 내용: 자격이 포트에 1:1 종속(포트마다 키 재입력)이던 구조를 **계정 단위 계좌 목록 + 포트 참조**로 변경(0017: broker_credentials.portfolio_id 제거·label 추가, portfolios.broker_credential_id FK ON DELETE SET NULL, 기존 연결은 마이그레이션에서 이관). 일반 설정에 '증권사 계좌' 카드(등록·계좌 조회로 상품코드 확인·연결된 포트 표시·삭제), 실전매매는 **드롭다운 선택**만(키 입력 없음, 미등록 시 설정 링크 안내). 한 계좌를 여러 포트에 재사용 가능, 계좌 삭제 시 연결 자동 해제.
- 테스트: 계좌 CRUD·마스킹·재사용(두 포트 연결)·타인 계좌 연결 차단·삭제 시 해제·미연결 409 — 전체 161 passed, tsc 클린, E2E(설정 등록 → 실전매매 선택 → 연결됨) 확인.
- Git commit: change: manage broker accounts in settings, select them per portfolio

## [2026-09-06] feat | 미국 매매 공식 LTM 적용 — 시뮬레이터·실전 주문표, 미국 RAVG 쌍 삭제 (사용자 지시 "LTM 을 미국 주식 거래 매매공식으로 적용… TF 만 남기고")

- 작업 내용: features/feature-us-ltm.md 참조. 연구(docs/ltv-strategy-study-20260906.md)에서 채택한 LTM 을 제품 엔진으로 옮김 — `app/strategy/ltm.py`(`ltm_states` 규칙 단일 구현: MA200 게이트·2% 이탈, 12M 모멘텀>0 ∧ 20일 내 −3% 급락 없음 → 노출 2.0, 아니면 1.0, OFF 현금; 밴드 10%; 다음날 시가 시장가; 정수 주; `BacktestResult` 반환). 시뮬레이터 미국 옵션 = `LTM_QLD`(기본)·`LTM_TQQQ`·`QQQ_TF`, **RAVG 미국 쌍(QQQ_QLD/QQQ_TQQQ) 삭제**(신규 잡 422, 기존 기록은 라벨만). `run_engine` 디스패치(잡·일지·전환·워커 공통), 미국 포트 주문표를 포트 `params.etf` 로 TF/LTM 분기(`_us_portfolio_orders`, 챗봇 order_sheet 동일), LTM 주문 종류 라벨·설명·레버리지 종목명(QLD/TQQQ) 표기. 한국은 RAVG 유지(연구 §10).
- 테스트: `tests/test_ltm.py`(비중·게이트·급락 브레이커·상승장 레버리지·TQQQ 50/50·하락장 현금·보유 시작) + `tests/test_ltm_api.py`(구 쌍 422, LTM 잡 완주·일지 종류, 실전 전환 → `/signals/daily` 전략 LTM) — 전체 스위트 결과는 아래 Git commit 시점 기록 참조.
- Git commit: feat: adopt LTM as the US trading formula, drop US RAVG pairs (keep TF) (#109)
- 후속 수정(같은 날): 실데이터 e2e(LTM_QLD 2015~2026 잡 → 전환)에서 시드 현금이 −$388 로 나옴 — 전액 투자 후 일할 보수가 현금을 음수로 밀던 결함. 목표 수량을 평가액의 99% 로 산정(`cash_reserve` 1%, 엔진·주문표 동일). 실데이터 대조 QQQ+QLD CAGR 18.7% / MDD −40.5% / Sharpe 0.80. (TF 엔진도 같은 방식으로 보수를 현금에서 차감하나 이번 범위 밖 — 별도 검토 대상)
- Git commit: fix: keep a 1% cash reserve in LTM sizing so fees cannot drive cash negative (#110)

## [2026-09-06] fix | TF 엔진 현금 여유 1% — 장기 보유 시 보수 차감으로 현금 음수 방지 (사용자 지시 "요거까지 처리하고 tag 생성")

- 작업 내용: LTM 에서 고친 것과 같은 결함이 TF 에도 있었다(전량 매수 뒤 일할 보수 0.20% 를 현금에서 차감 → 장기 보유 시 음수). `TF_CASH_RESERVE` 1% 를 두어 매수 수량을 현금의 99% 로 산정 — 백테스트 엔진(체결·계획)과 실전 주문표(`_tf_portfolio_orders`) 동일. 추가로 TF·LTM 두 엔진 모두 현금이 바닥나면 보수를 **이연(fee_due)** 해 다음 매도 대금에서 정산하도록 바꿔(평가액에는 차감 반영) 현금 곡선이 구조적으로 음수가 되지 않게 했다 — 여유 1% 만으로는 급등 장기 보유(합성 상승장 테스트)에서 보수가 여유를 넘어섰기 때문. 실데이터 TF QQQ 2007~2026: CAGR 11.9% / MDD −24.5% / 거래 29회, 최소 현금 +$6,494.
- 테스트: `test_tf_cash_never_negative_on_long_hold`(1,500봉 상승장 보유 — 현금 최소 ≥ 0, 잔여 < 2%), LTM 상승장 테스트에도 현금 ≥ 0 단언 추가. 전체 스위트 결과는 커밋 메시지 참조.
- Git commit: fix: keep a 1% cash reserve in TF sizing as well; bump version to 0.3.1 (#112)

## [2026-09-06] feat | 가이드 메뉴 신설 — 매매 공식별 설명 페이지 (사용자 지시 "수식 제외, 설명만, 읽기 쉽게")

- 작업 내용: 사이드바에 "📖 가이드" 그룹(매매 공식 개요 · RAVG · TF · LTM) 추가 — 매매일지 그룹 뒤, 설정 앞. `/guide`(세 공식 비교표·공통 원칙·시뮬레이터→실전 흐름·용어), `/guide/ravg`(레짐·노출·그리드·매도·레버리지 두 트랙·안전장치·하루 흐름·주문표 라벨), `/guide/tf`(두 줄 규칙·2% 완충·200일선 이유·실측), `/guide/ltm`(세 층 구조·안전장치 4종·라벨·실측·KOSPI 미적용 이유). 공용 조각 `components/guide.tsx`(GuideShell 탭·Section·Lead·Steps·Bullets·Table·ProsCons·Labels). 수식·계수는 쓰지 않고 '무엇을 왜 하는지'만 서술. 실측 숫자는 당일 로컬 DB 결과(TF 11.9%/−24.5%/29회, LTM 18.7%/−40.5%, 18.8%/−36.9%)만 인용.
- 테스트: tsc 클린, 헤드리스로 4 페이지 렌더·탭 활성·사이드바 그룹 순서 확인 (아래 커밋 기준).
- Git commit: feat: add guide menu with plain-language pages for RAVG, TF and LTM (#113)

## [2026-09-06] feat | 미국 실전매매 공식 선택(TF/LTM) + 가이드 보강 — 비교 그래프·표, TF/LTM 설명 추가 (사용자 지시 3건)

- 작업 내용: ① 실전매매(미국) "새 실전매매 시작"에 **매매 공식 선택**(LTM·QLD 기본 / LTM·TQQQ / TF·QQQ 1배) 추가 — `POST /portfolios` `etf`(미지정 시 LTM_QLD), 이름·색 편집 패널에서 **공식 변경**(`PATCH /portfolios/{id}` `etf`, 미국 포트만·다음 주문표부터 적용), 탭 줄에 공식 배지(가이드 링크), 포트 목록 응답에 `etf`. 이전에는 수동 생성 미국 포트가 항상 TF 였고 LTM 은 시뮬레이터 전환으로만 만들 수 있었다. ② 가이드 TF 페이지에 "비싸게 사서 싸게 파는 것 아닌가?"(손익 비대칭 표·2008/2020 사례), "전량 매수·매도가 어떻게 수익이 되나"(후행 200일선·2020~2026 경로 표), "TF 의 장점 — 수익이 아니라 낙폭"(5항·선택 기준) 추가. LTM 페이지에 "왜 TF 위에 레버리지를 얹으면 수익이 커지나"(게이트 동일·재진입 1배·비교표·선택 기준) 추가 — 대화에서 답한 내용을 그대로 옮김. ③ 가이드 개요에 **공식별 비교 섹션**: 한국/미국 구분 요약표(장점·단점·선택 이유), 공식별 블록(전략 vs 단순 보유 자산곡선 그래프 — 인라인 SVG 로그축·호버, 요약 지표표, 장점/단점/이럴 때 선택). 그래프 데이터는 실데이터로 엔진을 돌린 월말 곡선(`app/guide/compare-data.ts`, 기준 2026-09-04): 한국 RAVG(TIGER 200 + KODEX 레버리지, 2018-01~) vs TIGER 200 보유, 미국 TF·LTM(QLD) vs QQQ 보유(+QLD 보유), 미국은 두 공식이 모두 판정을 시작한 2008-08 부터 같은 창.
- 실측(비교 창): KR RAVG 15.9% / −21.0% / ×3.5 vs 보유 15.4% / −40.9%; US(2008-08~) TF 13.1% / −24.5% / ×9.2, LTM 19.8% / −40.5% / ×26, QQQ 보유 17.3% / −46.1% / ×17.6, QLD 보유 27.1% / −75.4%.
- 부수 결함 수정: 미국 TF 포트 주문표(`_tf_portfolio_orders`)가 `Regime` 미임포트로 NameError — 수동 생성 미국 포트의 주문표가 실패하던 문제. 새 테스트가 TF 경로를 처음으로 실제 실행해 드러났다.
- 테스트: `test_manual_us_portfolio_formula_select_and_change`(기본 LTM_QLD·명시 TF·구 키 422·목록 etf·PATCH 변경 후 주문표 전략 전환·한국 포트 422). 전체 스위트·tsc·헤드리스 결과는 커밋 메시지 참조.
- Git commit: feat: choose TF/LTM per US portfolio; guide comparison charts and TF/LTM explanations (#114)

## [2026-09-06] change | 가이드 비교 개편 — 낙폭 그래프·위기 구간 표·같은 낙폭에서의 수익 (사용자 검토 4건 + "낙폭만 부각" 지시)

- 검토 결론(실데이터): ① TF 는 실제로 보유보다 덜 번다(5년 구간 승률 1%) — 그래프 왜곡이 아니라 사실. RAVG·LTM 은 보유보다 높은데 로그 축에서 겹쳐 보였다. ② 2008 제외 시 TF 의 MDD 우위는 21%p→10%p 로 줄고(2009-07 이후 −24.5% vs −35%), 2020 같은 급락은 못 피하며(−24.5% vs −29%) 횡보장(2015~16)엔 보유보다 더 빠진다. LTM 은 2008 제외 시 MDD(−40.5%) 가 QQQ 보유(−35%)보다 크다 → "낙폭이 작다" 문구 삭제. RAVG 는 2008 없이도 위기마다 절반 이하(2020 −17% vs −38%, 2022 −14% vs −36%), −20% 아래 체류 0.2% vs 34%. ③ 낙폭이 그래프에 안 보임 → 낙폭(수면 아래) 그래프 도입. ④ 보유 대신 **같은 최대 낙폭으로 맞춘 보유**(비중 축소)와 비교 — RAVG 15.9% vs 7.8%, TF 13.1% vs 8.4%, LTM 19.8% vs 14.7%(QQQ)/13.4%(QLD).
- 작업 내용: `components/guide-chart.tsx` 에 `DrawdownChart`(고점 대비 낙폭, 월중 최저, 면 겹침, 최저점 라벨, 호버)·`YearStrip`(연도별 수익 띠, 5%p 이상 우위 굵게) 추가, 자산곡선·낙폭 그래프에 위기 구간 음영(보유 −20% 이상 하락 국면, 고점→저점). 개요 페이지 블록을 ① 같은 낙폭에서의 수익 헤드라인 → ② 자산곡선 → ③ 낙폭 그래프 → ④ 위기 구간 표(보유/공식 낙폭·회복 기간) → ⑤ −20% 아래 체류 시간·낙폭 대비 수익 → ⑥ 연도별 띠 → 장점/단점/선택 순으로 재구성. 데이터 `compare-data.ts` v2(월말 곡선 + 월중 최저 낙폭 + 위기 표 + 체류 시간 + 위험 등가 + 연도별). TF 페이지 약점에 급락·횡보·5년 승률 명시 + "느린 하락장 보험, 보험료 연 4%p"; LTM 페이지에 "강점은 낙폭이 아니라 수익" 단락.
- 테스트: tsc·헤드리스(그래프 6개·헤드라인·표·호버·모바일 폭) — 커밋 메시지 참조.
- Git commit: change: guide comparison — drawdown charts, crisis table, risk-equal return headline (#115)

## [2026-09-06] fix | 시뮬레이터 지난 결과에 LTM 잡이 안 보이던 결함 (사용자 보고 "실행하고 지난결과에 등록이 안되는거 같은데")

- 원인: 지난 결과 목록의 시장 판정이 `etf.startsWith("QQQ")` 였다 → `LTM_QLD`/`LTM_TQQQ` 잡은 한국으로 분류되어 미국 목록에서 사라지고 한국 목록에 섞였다. 잡 자체는 정상 저장·완료(DB #140~142 DONE). 부수: 8건을 먼저 자른 뒤 시장을 걸러 다른 시장 잡이 많으면 목록이 비었고, 완료 직후 목록을 다시 읽지 않아 메뉴로 돌아오면 새 결과가 빠져 있었다.
- 수정: 시장 판정을 `ETF_INFO[etf].market` 기준으로, 시장 필터 후 8건 자르기, 결과 표시 시 `loadHistory()` 재호출, 뱃지를 짧은 이름(TF / LTM·QLD / LTM·TQQQ / RAVG·QLD(구))으로.
- 테스트: tsc, 헤드리스(미국 목록에 #142 LTM·QLD 표시·한국 목록에 LTM 없음·실행 후 재진입 시 새 잡이 첫 항목) — 커밋 메시지 참조.
- Git commit: fix: simulator past results — classify LTM jobs as US, filter before slicing, refresh after run (#116)

## [2026-09-06] feat | 매매일지 보유 평단 대비 일별 수익률 라인 (사용자 검토 요청 → "제안한 방식대로 구현")

- 작업 내용: docs/mjournal-broker-link-review-20260905.md §3-2 참조. `GET /mjournals/{jid}/return-series`(종목별 보유 구간 % 시리즈·종합·현재 수익률·안내), DB 일봉 부족분은 KIS 일봉으로 보충·적재(`_ensure_daily_bars`, 일지 연결 계좌 키 → .env 키), 마지막 점은 연결 계좌 현재가. 현황 카드에 탭 "보유 수익률 (%)"(기본) / "누적 실현손익 (원)", 구간별 라인·0% 기준선·종합 점선·청산 종목 토글, 도넛 풍선에 평가 수익률·보유 기간. 기록 입력에 종목코드(선택) 필드(`EntryIn.code`) — 코드가 있어야 시세를 붙일 수 있다.
- 테스트: `test_journal_return_series_fifo_avg_and_segments`, `test_journal_return_series_fetches_missing_bars` — 전체 스위트·tsc·헤드리스는 커밋 메시지 참조.
- Git commit: feat: journal daily return vs average cost — return-series API, KIS bar backfill, chart tabs (#117)

## [2026-09-06] change | deploy.sh — 같은 버전이면 중지, 롤백 보호, `restart` 모드 (사용자 지시 2건)

- 작업 내용: ① 태그 배포 시 체크아웃 직후 실행 중인 `/api/health` 버전과 비교해 **같은 버전이면 재빌드하지 않고 중지**(저장소를 배포 전 ref 로 되돌리고 --stash 로 치운 변경도 복구), **낮은 버전이면 중지**(롤백 보호). `--force` 로 강제. 브랜치 배포(main)는 VERSION 이 같아도 코드가 바뀔 수 있어 경고만 하고 진행. ② `scripts/deploy.sh restart [서비스…]` — 코드·이미지 변경 없이 `docker compose restart` 후 헬스·상태 출력(코드 반영 아님을 명시). 헬스 대기를 `wait_health` 함수로 통합. README 업데이트 절·스크립트 도움말 갱신.
- 테스트: `bash -n` 구문 검사, 로컬 재실행 시나리오(같은 버전 중지·--force 진행·restart) 는 원격 서버에서만 가능 — 검증 결과는 커밋 메시지 참조.
- Git commit: change: deploy.sh — stop on same version, protect against downgrade, add restart mode (#119)

## [2026-09-06] change | 매매일지 카드 정리 — 매매 비용을 실현손익 카드의 작은 글씨로 (사용자 지시)

- 작업 내용: "매매 비용" 카드를 없애고 그 값을 실현손익 카드 하단 작은 글씨("매매 비용 N원 (수수료 + 제세금) 차감 후 금액입니다")로 옮겼다 — 비용은 이미 실현손익에 반영된 값이라 독립 카드로 자리를 차지할 이유가 없었다. 카드가 4→3개가 되며 빈 칸이 생기므로 "총 손익 (실현 + 평가)" 카드를 두 칸으로 넓혔다.
- 테스트: tsc, 헤드리스(작은 글씨 표기·카드 수·모바일 폭·페이지 에러 없음).
- Git commit: change: journal cards — fold trading cost into the realized card as a small note (#121)

## [2026-09-06] feat | 매매일지 계좌 평가금액 카드 (주식 평가액 + 예수금) — 사용자 검토 요청 → 승인

- 작업 내용: docs/mjournal-broker-link-review-20260905.md §3-3 참조. `fetch_balance()` 로 잔고 조회에서 예수금(output2)까지 함께 읽어 현재가 캐시(120초)에 담는다 — 추가 API 호출 없음. 일지가 **계좌 주식을 전부 담고 있을 때만**(계좌 보유 ⊆ 일지 보유, 수량 일치, 일지에 계좌 밖 종목 없음) `account_total = 주식 평가액 + 예수금` 을 계산해 "계좌 평가금액" 카드로 보인다. 커버리지가 깨지면(부분 관리·수동 종목 섞임) 카드를 감춘다. 대시보드 총자산에는 예수금을 넣지 않는다(한 계좌를 여러 일지에 연결할 수 있어 중복). 예수금 D+2 기준은 풍선말에 명시.
- 실데이터 확인: 연금저축 주식 1,392,445 + 예수금 96,022 = 1,488,467 / 한투-삼성 2,810,500 + 5,035 = 2,815,535, 두 일지 모두 커버리지 충족.
- 테스트: `test_journal_account_total_only_when_journal_covers_account`(합계·대시보드 미포함·계좌 밖 종목·수량 불일치) — 전체 201 passed.
- Git commit: feat: journal account value card — stock valuation plus cash from the linked account (#122)

## [2026-09-06] change | 매매일지 카드 순서·크기 (사용자 지시)

- 작업 내용: 계좌 평가금액 → 총 손익 → 평가손익 → 실현손익 순. `auto-rows-fr` + `Stat className="h-full"`(Stat 에 className prop 추가) 로 네 카드 동일 크기 — 래퍼 div 가 카드 높이를 칸에 못 맞추던 문제 해소. 매도 금액은 총 손익 카드로.
- Git commit: change: journal cards — account value first, equal-size 2x2 grid (#123)

## [2026-09-06] fix | 대시보드 매매일지 자산 — 계좌 단위 제외를 종목 단위 제외로 (사용자 검토 요청 → 승인)

- 원인: 2026-09-05 규칙 "일지가 실전매매 포트와 같은 증권사 계좌면 총자산에서 일지 제외"가, 포트가 **현금만** 들고 있고 주식은 일지에 있는 실데이터에서 일지 주식 4,202,945원을 통째로 누락시켰다(대시보드 매매일지 0원). 범례의 "취득원가"도 고정 문구여서 현재가 평가와 어긋났다.
- 수정: `journal_assets` 가 같은 계좌 포트의 **실제 보유 종목**(잔여 로트 > 0, 코드 → 정규화 이름 매칭)만 일지에서 빼고 나머지 종목 가치는 총자산에 넣는다(`value` = 포함분, `excluded` 목록, `counted` = 전부 겹칠 때만 False). 대시보드 표는 "포함 / 일부 포함 — 제외 종목 / 제외", 범례는 "평가액 / 일부 취득원가 / 취득원가"를 상태별로.
- 테스트: `test_journal_same_account_as_portfolio_dedupes_by_instrument`(현금만 → 전부 포함, 같은 종목 보유 → 그 종목만 제외·이름 매칭, 전부 겹침 → 제외). 기존 계좌 단위 테스트를 대체.
- Git commit: fix: dashboard journal assets — exclude only instruments the linked portfolio actually holds (#124)

## [2026-09-06] change | 매매일지 카드 위계 — 계좌 평가금액을 크기·강조색으로, 손익 부호색은 유지 (사용자 검토 요청 → 제안 채택)

- 검토: "계좌 평가금액만 붉은색, 나머지 검정" 안은 붉/파가 이익·손실 부호인 이 화면의 규칙을 깨고(손실이 나도 검정) 잔액에 붉은색을 써 "올랐다"로 읽힐 수 있어 반대. 대신 색이 아닌 **크기·배경**으로 위계를 만드는 안을 제안하고 채택됨.
- 작업 내용: 계좌 평가금액 카드 값 24px(`size="lg"`) + 강조색 주황(`tone="accent"`, 손익 부호에 쓰지 않는 색) + 옅은 주황 배경·테두리. 총 손익·평가손익·실현손익은 부호색(붉/파) 그대로, 크기 19px 유지. 괄호 수익률(+84.9%)은 옅은 색(`text-muted`)으로 낮춰 금액이 먼저 읽히게.
- Git commit: change: journal cards — emphasize account value by size and accent, keep P&L sign colors (#125)

## [2026-09-06] change | 카드 위계 규칙 — 네 페이지(대시보드·실전매매·시뮬레이터·매매일지) 공통 (사용자 검토 요청 → 제안 채택)

- 검토: 매매일지만 배경색·주황 숫자를 써 대시보드와 분위기가 달랐고, 실전매매는 카드 셋이 모두 24px 라 핵심이 없었으며, 시뮬레이터는 총수익률이 다른 지표와 같은 크기였다.
- 규칙: ① 페이지마다 **핵심 카드 하나**(총자산·총자산·총수익률·계좌 평가금액) — 값 24px 굵게, 첫 자리. ② 핵심 표시는 숫자 색이 아니라 **카드 장식**(`.card-hero`: 왼쪽 3px 주황 선 + 주황 5% 배경 + 옅은 주황 테두리). 숫자는 검정 또는 손익 부호색만 — '숫자 색 = 손익 부호' 규칙을 네 페이지에서 동일하게. ③ 나머지 카드 19px(대시보드 한국·미국 20→19, 실전매매 순손익·수익률 24→19). ④ 라벨 13px·보조 12.5px·안내 11px 통일.
- 구현: `Stat` 에 `hero` 옵션(+ label ReactNode) 추가, 대시보드 상단 카드 3개를 수제 마크업에서 공용 Stat 으로 교체(같은 타이포·간격), 실전매매·시뮬레이터·매매일지에 hero 지정. 매매일지의 임시 `!bg-accent-dim`·주황 숫자는 제거.
- Git commit: change: unify card hierarchy — one hero card per page, sign colors only on P&L (#126)

## [2026-09-06] docs | KRX 애프터마켓 개장(9/14) 영향 검토 — 매매 공식 수정 없음 (사용자 지시)

- 작업 내용: docs/krx-aftermarket-20260914-review.md. 보도 확인: 시간외 단일가 폐지 → KRX 애프터마켓 16:00~20:00 실시간 체결(전일 종가 ±30%), 시간외 종가매매(15:40~16:00) 유지, 프리마켓 2027년 말로 연기, **ETF·ETN 제외**, NXT ETF 는 11월부터. 결론: RAVG 의 신호 앵커(KRX 정규장 15:30 종가, 시장코드 J)·실행(다음 날 09:00 예약주문)·갭 취소 전제가 그대로라 공식 수정 없음. 운영 보완 후보: 20:15 체결 동기화 추가(주식 애프터마켓 체결), 9/14 첫 주 예약주문 창·일봉 종가 확인, 11월 NXT ETF 개시 후 갭 빈도 관찰. ASSUMPTIONS 에 신호 앵커 명시.
- Git commit: docs: review KRX after-market launch (2026-09-14) — no formula change, ops follow-ups (#127) · 후속 §5 ETF 밤갭 질문 답변(#128)

## [2026-09-06] feat | 통제된 무인 실행 — 승인된 지정가를 09:01 시가 확인 후 자동 발주 (사용자 지시 · ADR-008)

- 배경: 갭 취소 규칙이 실전에서는 예약주문 때문에 지켜지지 않았다(시가는 09:00 에야 확정, 예약 취소 창은 07:30 마감). 사용자 판단 "철저히 통제된 무인 매수/매도는 문제 없다" + 지시 "매도도 포함, 설정에서 매수·매도 허용을 각각 켜야 동작".
- 작업 내용: docs/auto-execution-20260906.md. `app/autoexec.py` — 설정 `GET/PUT /settings/auto-exec`({buy, sell}), 승인 `POST /portfolio/{pid}/orders/approve`(설정 꺼진 방향·시장가·미국·09:00 이후·계획 불일치 거절, 중복 차단), 09:01 실행 `run_auto_execution`(락·설정 재확인·시가 조회·갭 취소 `gap_cancel_exact`·예수금/잔고 한도·매도 우선 발주·연속 실패 2회 정지·요약 기록), 장 마감 확정 `sync_auto_orders`·대조 경고 정지 `pause_if_reconcile_warns`, `resume`. KIS `place_order`(TTTC0012U/0011U, 모의 VTTC0802U/0801U)·`cancel_order`(TTTC0013U/VTTC0803U). 마이그레이션 0021(`user_settings.auto_exec`, `broker_orders.mode`). 워커 `auto-exec-open` 09:01(재시도 없음·휴장일 스킵). 취소 엔드포인트가 승인 철회/정규 주문 취소 처리. 웹: 설정 › 무인 실행 탭(매수·매도 토글, 켤 때 확인창), 주문표 "🤖 무인 실행 승인" 버튼·확인창·줄 상태·마지막 실행 요약·정지 배너(다시 켜기).
- 테스트: `tests/test_autoexec.py` 4건(설정·승인 규칙·중복/불일치·철회 / 갭 취소·매도 우선·재실행 차단·체결 확정 / 예수금·잔고 한도·연속 실패 정지·해제 / 미국 거절·대조 경고 정지). 전체 205 passed, tsc 클린, 헤드리스(설정 탭·토글·승인 버튼 표시, 원상복구).
- Git commit: feat: controlled auto-execution — approved limit orders placed after the 09:01 open check (ADR-008) (#129)

## [2026-09-06] fix | 무인 실행 2차 검증 — 논리·절차 오류 3건 수정 (사용자 지시)

- 검토 방법: `autoexec.py`·`broker.py` 훅·워커를 흐름 순서(승인 → 09:01 → 15:45)로 다시 읽고 백테스트 순서와 대조.
- ① **이중 발주 구멍**: 예약주문 중복 검사가 `reserved` 만 봐서, 무인 승인된 줄을 예약주문으로도 접수할 수 있었다(09:00 동시호가 + 09:01 무인 = 2건). 승인 쪽은 예약을 막았지만 반대 방향이 비어 있었다 → 예약 접수도 `approved/submitted/partial` 을 중복으로 본다.
- ② **상시 정지 오류**: 장 마감 대조의 모든 `warn` 을 정지 사유로 썼는데, 지정가 부분체결(계획 8주 ≠ 등록 5주)도 `warn` 이라 그리드 운용에서는 거의 매일 멈췄을 것. `reconcile_plan` 항목에 `kind`(missing/unplanned/short/excess)를 붙이고(level·text 는 불변 — 수동 배너 호환) 정지는 **unplanned·excess** 만.
- ③ **예수금 한도의 전부 생략**: 매수 합계 > 예수금이면 그리드를 전부 생략했다. 백테스트는 grid1 부터 순차 체결하므로 얕은 그리드(높은 지정가)부터 누적액이 예수금 이하인 줄만 발주하고 넘치는 줄만 생략하도록 바꿈. 정지 사유 문구는 마지막 실패 메시지를 쓰도록 정리.
- 검토했지만 유지한 것: 매도 대금은 T+2 라 당일 매수 한도에 넣지 않음(보수적, 백테스트와의 불가피한 차이) · 실패 연속 카운터는 날을 넘겨 누적(성공 시 초기화) · 09:00~09:01 1분 공백 · 실행 도중 예외 시 롤백돼도 락·`plan_date` 조건으로 같은 날 재발주는 없음(원장은 15:45 체결 가져오기로 맞음).
- 테스트: `tests/test_autoexec_review.py` 3건(kind·정지 기준 / 예약↔무인 중복 양방향 / 예수금 한도 부분 발주).
- Git commit: fix: auto-execution review — block reserve/auto double submission, pause only on dangerous reconcile, partial buys by shallow grid first; bump version to 0.5.0 (#130, tag v0.5.0)

## [2026-09-06] feat | 무인 실행 09:01 사전 확인 2건 — 원장 vs 계좌 대조, 계획 재대조 (사용자 질문 → 구현 지시)

- 질문 "최종 주문 전 자동 매매 조건을 한 번 더 확인하는 건 의미 없나?" → 전략 조건 재계산은 입력(전날 종가)이 같아 무의미, 두 가지만 의미 있음: ① 앱 원장과 계좌 잔고의 보유 대조(HTS 수동 매매·가져오기 누락으로 어긋난 보유 기준의 주문표를 그대로 내는 것을 방지 — 종전엔 15:45 사후 대조만), ② 승인 행과 그날 계획 스냅샷 재대조(승인 때만 확인했던 것).
- 작업 내용: `_execute_portfolio` 에 ② 계획 재대조(줄 키·수량 불일치 줄만 생략) → ③ 시가 → ④ 잔고 조회 뒤 원장 대조(200 ETF·레버리지 수량 불일치면 전부 생략 + 정지, 사유에 종목·수량) 순으로 삽입. `_ledger_holdings` 헬퍼. ADR-008 표 11·12, 운영 문서 §5-1 갱신.
- 테스트: `test_precheck_ledger_vs_account_mismatch_skips_and_pauses`, `test_precheck_plan_revalidation_at_execution`; 갭 테스트는 원장 10주를 미리 등록해 사전 대조를 통과하도록 갱신.
- Git commit: feat: auto-execution pre-checks — ledger vs account holdings and plan re-validation right before placing orders (#131, tag v0.5.1)

## [2026-09-06] feat | 무인 매수 — 발주 직전 KIS 매수가능조회로 판정 (사용자 검토 요청 → 승인)

- 질문 "계좌 잔금을 먼저 검사하고 매수해야 하지 않나?" → 검사는 있었으나 기준이 예수금 총액(`dnca_tot_amt`)이라 근사: 다른 주문 증거금이 빠지지 않아 거절→실패→정지로 이어질 수 있고, 당일 매도대금 재사용은 반영되지 않았으며, 09:01 조회 한 번이라 앞 주문이 묶은 금액이 다음 판정에 안 들어갔다.
- 작업 내용: `KisTradingClient.buyable(code, price)` — 매수가능조회(`inquire-psbl-order`, 실전 TTTC8908R·모의 VTTC8908R 자동 치환) → 주문가능현금·미수 없는 매수가능수량. 실행기 ④: 매수 줄마다 발주 직전 조회 → 가능 수량 ≥ 계획 수량이면 발주, 아니면 그 줄만 생략(수량 축소 없음). 조회 실패 시 예수금 총액 누적 규칙으로 폴백("예수금 한도(폴백)"). 그리드 전량 자본 요건은 설계 그대로(주문표가 원장 현금 안에서 단을 만들고, 지정가는 증권사에서 금액이 묶임 — 실데이터 8.4년 grid1/2/3 체결률 25%/5%/1%, 전체 사다리 = 평가액 중앙 6.8%).
- 테스트: `test_buyable_check_before_each_buy`(예수금 총액 0 이어도 KIS 가능 600,000 → grid1 발주 후 잔여 105,000 으로 grid2 "가능 1주 < 계획 3주" 생략, 얕은 단부터 발주 직전마다 조회), 기존 폴백 테스트 문구 갱신. 전체 스위트 결과는 커밋 메시지 참조.
- Git commit: feat: auto-execution — check KIS buyable quantity right before each buy, deposit rule as fallback

## [2026-09-06] feat | 계좌 예수금 연동 — 시작 시 잔고 불러오기 + 장 마감 예수금 대조(경고·원클릭 보정)

- 지시: "kis 에서 예수금 조회도 가능한가? 실전매매 등록할 때 증권계좌 연동하면 자동으로 가져올 수 있는지 검토해줘" → 검토(잔고 조회 output2 에 예수금총액·D+1·D+2 가 이미 옴, 원장 현금과 같은 정의는 **D+2 가수도정산금액**; 계좌 공유 가능성 때문에 자동 확정은 위험) → "1. 구현하세요. 2. 테스트 완료 후 버전 올리고 tag 생성하세요."
- 작업 내용: (1) `fetch_balance` 가 `deposit_d1`(익일정산)·`deposit_d2`(가수도정산, 없으면 총액) 추가 반환. (2) 신규 `app/cashcheck.py` — `GET /broker/accounts/{aid}/balance`(시작 패널 잔고 요약: D+2 예수금·전략 종목 표시), `GET /portfolio/{pid}/cash-check[?refresh]`, `POST …/cash-check/align`(차액을 입금/출금 한 건으로, 오늘 대조 결과이고 대조 이후 원장이 안 바뀐 경우만; tags `cash_check`). 허용 오차 max(1만원, 총자산 0.1%) — 수수료·분배금 범위는 경고 없음. (3) `POST /portfolios` 에 `credential_id`(시작과 함께 계좌 연결), `GET /portfolio/{pid}/broker` 응답에 `cash_check`. (4) `run_post_close_sync` ④ — 체결 가져오기 뒤 국내 포트 예수금 대조를 `params.cash_check` 에 저장(경고·기록만, 원장 불변·무인 실행 정지 없음). (5) 웹 시작 패널: 증권사 계좌 선택 + "계좌에서 불러오기"(D+2 예수금 → 현금 칸, 전략 종목 보유 → 보유분 행, 주력 200 ETF 자동 선택, 전략 외 종목은 제외 목록) + 확인 안내; 주문표 위 예수금 대조 경고 배너("차액을 입출금으로 등록"·"지금 다시 대조"); 증권사 연동 카드에 마지막 대조 요약·"지금 대조"·소액 차이 "차액 등록". 계좌 목록은 포트가 없어도 로드. (6) 문서: ADR-008 ⑪(현금은 09:01 대조 대상 아님 → 15:45 예수금 대조), 운영 문서 §6, user-guide §4, feature-portfolio §5·§8.
- 테스트: `tests/test_cashcheck.py` 6건 — 잔고 요약 파싱(D+1/D+2, 구형 응답 폴백), 잔고 요약 API(전략 종목 정렬·격리 404·502 문구), 시작 시 계좌 연결(타인 계좌 404·포트 미생성), 대조(허용 오차 안 warn=False → 초과 warn → 출금 보정 → 원장 3,900,000·거래 memo/tags → 차이 0 재등록 없음 → 대조 후 원장 변경 시 409 → 입금 보정), 가드(결과 없음·미연결·미국 409), 15:45 동기화 기록(원장 불변·정지 없음). 전체 `pytest -q tests/` → **217 passed**. `tsc --noEmit` 통과. Playwright: 시작 패널 계좌 선택·불러오기, 증권사 연동 카드 "지금 대조" 확인(스크린샷).
- Git commit: feat: account cash link — prefill start panel from KIS balance, post-close cash reconciliation with one-click alignment
- 특이사항: 보정 거래는 TWR·XIRR 에 외부 현금흐름으로 잡혀 수수료 차액이 손실 대신 출금으로 계산된다(소액, feature-portfolio §5 기록). 미국 포트는 국내 잔고 TR 이라 연결만 되고 불러오기·대조는 대상 외.

## [2026-09-06] feat | 장 시작 전 예상 시가 갭 취소(취소만 무인) + 매매 로그 페이지

- 지시: "무인 매수/매도 기능 자체는 그대로 두고 … 08:50~09:00 사이에 시가를 예상할 수 있다고 했는데 이 가격을 근거로 등록된 지정가를 취소하는 방안은 어떤가? 무인 매수/매도는 수동으로 직접 지정한다(초기 기획안대로). 장 시작 전 예상 시가를 계산해서 특정 가격 이하시 그리드 매수를 전량 취소한다(취소는 무인으로)." + "로깅 기능 — 사용자의 매매 기록을 로그 페이지로 출력, 거래에 실패한 로그도 확인" + "구현 완료 후 테스트, 문제 없으면 tag".
- 작업 내용 (1) **사전 갭 취소** `app/preopen.py`: 08:57 워커(`preopen-gap-cancel`)가 오늘 계획이 있는 국내 포트마다 200 ETF 예상체결가(`KisClient.fetch_expected`, 호가/예상체결 TR FHKST01010200 `antc_cnpr`, 3회 재시도)를 읽고 갭 취소 기준(`gap_cancel_exact`) 이하면 KIS 정정취소가능(미체결) 주문(`list_open_orders`, TTTC0084R 실전 전용) 중 **200 ETF 매수·오늘 그리드 지정가와 같은 것**만 취소(TTTC0013U) — 앱 예약주문은 `BrokerOrder.status=gap_cancelled`, HTS 직접 주문은 '앱 밖 주문'으로 로그, 미체결 목록에 없는 예약주문은 '취소 불가' 기록. 무인 승인 줄(auto)은 09:01 실제 시가 판정이라 대상 외. 설정 `auto_exec.preopen_cancel` 기본 켜짐(설정 › 무인 실행 세 번째 스위치). 하루 1회, `params.preopen_cancel.last_run` → 주문표 위 "🕗 장 시작 전 갭 확인" 한 줄. (2) **매매 로그**: 모델 `ActivityLog`(0022), `app/activity.py` `log_event` + `GET /logs`(거래 원장·BrokerOrder·ActivityLog 병합, 최신순·KST, days/type/level/portfolio/q 필터, 수준별 건수). 기록 지점: 무인 실행 요약·정지·승인, 예약주문 접수·취소, 사전 갭 확인·취소·실패·취소 불가, 장 마감 동기화 결과·오류·예약주문 상태 조회 실패, 예수금 대조 경고·보정, 거래 삭제. 웹 `/logs` 페이지(기간·유형·수준(경고 이상/오류만)·실전매매·검색, 수준별 행 색·점) + 내비 "매매 로그"(아이콘). `STATUS_KO.gap_cancelled`, 주문표 상태 표시. (3) 문서: ADR-008 통제 조건 13·8 갱신(대안 항목 병행 채택 주석), 운영 문서 §1 08:57 단계·§2 코드 지도·§3 상태·§6 한계, user-guide §3·§4-3, feature-portfolio §8.
- 테스트: `tests/test_preopen.py` 4건(예상체결가·미체결 파싱·모의 거절 / 비갭 유지 / 갭 → 앱 예약 2건+HTS 1건 취소·매도·타종목·다른 가격 제외·미매칭 1건·하루 1회 / 취소 실패·설정 꺼짐·그리드 없음), `tests/test_activity.py` 3건(병합·필터·격리·거래 삭제 이벤트 / 무인 승인·실행 이벤트 / 예약 접수·취소 이벤트), 기존 무인 설정 응답 2곳 갱신. 전체 `pytest -q tests/` → **224 passed**. `tsc --noEmit` 통과. Playwright: 내비 → /logs 표·필터(경고 이상만 → 빈 상태, 거래만 2건), 설정 › 무인 실행 스위치 3개 표시(누르지 않음), 콘솔 오류 없음. dev DB `alembic upgrade head` → 0022.
- Git commit: feat: pre-open expected-price gap cancel (cancel-only, unattended) and unified trade/order/event log page
- 특이사항: 예상체결가는 근사값 — 예상이 기준 아래인데 실제 시가가 위로 열리면 그날 그리드를 놓친다(손실 아님). KIS 예약주문의 정규 전환 시각이 08:57 보다 늦으면 '취소 불가'로 남는다 — 실계좌 첫 주에 확인 필요(운영 문서 §6). 미체결 조회 TR 은 실전 전용.

## [2026-09-07] feat | 완전 무인 운영 — 16:45 주문표 자동 승인 + 살아 있는 주문 전량 취소(긴급 정지)

- 지시: "실전매매 주문표에서 실행할 줄을 체크하고 무인 실행 승인을 누르는 단계 없이 완전 무인으로 운영할 수 있나?" → 검토(가능, ADR-008 통제 조건 2 의 정책 변경 · 시장가 줄은 예약주문 자동 접수 · 하루 매수 상한 권장) → "1. 완전 무인운영을 구현하세요. 2. 완전 무인운영 중 이미 주문이 완료된 내역을 취소 기능을 제공하세요. 3. 테스트 완료 후 tag."
- 작업 내용: (1) 신규 `app/autoapprove.py` — 포트별 설정 `params.auto_exec.auto_approve = {enabled, market_reserve, daily_buy_cap}` (`PUT /portfolio/{pid}/auto-exec/auto-approve`; 국내 포트·연결 계좌·설정의 매수 또는 매도 허용이 있어야 켬, 기본 꺼짐). 16:45 배치 `run_auto_approve`(워커 `auto-approve-plan`, 휴장일 스킵, 재시도 없음): 정지·설정 모두 꺼짐 건너뜀 → `_portfolio_orders` 로 다음 실행일 주문표 계산·스냅샷 → 실행일 ≤ 오늘(일봉 미적재) 건너뜀 → 하루 매수 상한 초과면 승인 없이 정지 → 허용 방향 지정가 줄 `approved`(자동 승인), 시장가 줄은 옵션이면 예약주문 접수(실전·접수 창 안), 아니면 '수동 필요' → `auto_approve_last` + 로그. 살아 있는 줄은 건너뜀(멱등). (2) 전량 취소 `POST /portfolio/{pid}/orders/cancel-all {stop, since}` — 승인 철회·예약주문 취소 TR·발주된 정규 주문 취소 TR, 체결 줄은 대상 외(`filled_untouched`), `stop` 이면 정지 + 자동 승인 끔. (3) 웹 실전매매: "🤖 완전 무인 운영" 패널(상태·설정: 자동 승인/시장가 예약/하루 상한·마지막 자동 승인 요약·[전량 취소]·[⛔ 무인 중지 + 전량 취소]), 표의 승인·발주 줄에 개별 취소 버튼(기존에는 ✗ 로만 표시되던 상태). `auto_exec_view` 에 `auto_approve`·`auto_approve_last`. (4) 문서: ADR-008 조건 2 갱신·조건 14 신설, 운영 문서 §1 16:45 단계·§2·§5-6, user-guide §3, feature-portfolio §8. (5) 테스트 시계: 컨테이너는 UTC 라 00:00~09:00 KST 에 `date.today()` 가 어제가 되어 '오늘 실행일' 테스트 8건이 새벽에 실패(06:41 실측) → 무인·로그·대조 테스트 6개 파일을 `datetime.now(KST).date()` 로 통일. 로그 시각은 초 단위로(같은 분 순서 보존).
- 테스트: `tests/test_autoapprove.py` 4건(설정 가드·상태 / 배치: 매수만 허용 시 그리드 2줄 승인·익절 '수동 필요'·레버리지 예약 접수, 재실행 멱등, 매도 켜면 익절 승인 / 시장가 옵션 꺼짐·실행일 지남·모의 계좌·상한 초과 정지·정지 상태·꺼짐 / 전량 취소: 승인 3·예약 1·발주 1 취소, 체결 1 대상 외, 정지+자동 승인 끔, 오류 로그). 전체 `pytest -q tests/` → **228 passed**. `tsc --noEmit` 통과. Playwright: 실전매매 페이지에 "완전 무인 운영 꺼짐 · 설정" 패널 렌더(연결 계좌 있는 포트), 콘솔 오류 없음.
- Git commit: feat: fully unattended operation — 16:45 auto-approval of the plan (limit lines approved, market lines reserved) with daily buy cap, cancel-all / emergency stop
- 특이사항: 시장가 줄 예약 접수는 실전 계좌·접수 창(15:40~) 안에서만 — 모의 계좌는 '수동 필요'. 하루 매수 상한은 시장가 줄을 최근 종가로 근사한다. 사람이 보지 않는 운영이라 "정지된 채 모르는 상태"가 가장 큰 위험 — 알림 채널이 없어 매매 로그·배너를 하루 한 번 확인하도록 운영 절차에 적었다(알림은 후속).

## [2026-09-07] feat | 완전 무인 — 하루 매수 상한을 총자산 대비 %(기본 20%) 옵션으로, 레버리지 시장가 줄 예약 접수 확정

- 지시: "첫째, 레버리지 시장가 줄도 예약주문으로 무인 접수할지 ← 구현하세요. 둘째, 하루 매수 총액 상한을 둘지와 그 값 ← 옵션 설정에서 지정할 수 있도록 설정하세요(기본: 20%)."
- 작업 내용: (1) 시장가 줄 예약 접수는 v0.8.0 의 `market_reserve` 옵션(기본 켬)이 이미 그 동작 — 유지, 문서에 사용자 결정으로 기록. (2) 상한을 원 단위 선택값에서 **총자산 대비 %**(`daily_buy_cap_pct`, 기본 20, 0 = 없음, 0~100 검증)로 변경. 분모는 주문표 계산이 준 신호 기준일 총자산(`account.equity`), 없으면 원장 현금 + 보유 로트×최근 종가. 계획 매수 합계(지정가×수량 + 시장가는 최근 종가×수량) > 총자산×상한이면 승인 없이 정지, 사유에 %·금액·총자산 표기. (3) 웹 설정 패널 입력을 %로(기본 20), 상태 문구 "하루 매수 상한 총자산의 N%". (4) 문서: ADR-008 조건 14, 운영 문서 §1·§2, user-guide, feature-portfolio.
- 테스트: `test_autoapprove.py` 갱신 — 기본값(예약 접수 켬·20%)·범위 검증(150% → 422)·상한 10% 초과 시 정지 사유에 "하루 상한 10%"·"총자산 5,000,000원". 전체 `pytest -q tests/` → **228 passed**. `tsc --noEmit` 통과.
- Git commit: feat: auto-approve daily buy cap as percent of equity (default 20%), market lines reserved unattended
- 특이사항: 레짐 전환일의 레버리지 진입(총자산의 30~40%)은 기본 20% 를 넘어 그날 자동 승인이 멈춘다 — 의도된 확인 지점이며, 무인으로 통과시키려면 상한을 40% 이상으로 올리거나 0 으로 둔다.

## [2026-09-07] feat | 텔레그램 알림 + 챗봇 운영 지식·상태 도구 + 문서 전수 점검

- 지시: "외부 메신저로 매매결과, 현황 등 메시지를 전송 — 텔레그램 연동, 봇 토큰은 설정에서 입력, 설정에 전송할 메시지 항목을 나열하고 사용자가 체크하면 발송. 지금까지 변경된 내용을 문서에 모두 기록했는지 검토하고 누락은 기록. 테스트 후 tag." + "챗봇에서 관련 내용을 답변 가능한 상태인지 검토하고 보완."
- 작업 내용: (1) **텔레그램** 신규 `app/notify.py` + 0023(`user_settings.telegram_bot_token` 🔒·`telegram_chat_id`·`notify`). 설정 › 알림 탭: 봇 토큰(암호화 저장·마스킹 표시, 형식 검증), 채팅 ID('연결 확인'이 getUpdates 로 자동 확인 + 테스트 메시지), 알림 켬/끔, 보낼 항목 9종 체크(무인 실행 결과·자동 승인·사전 갭 취소·정지/긴급 정지·장 마감 동기화·예수금 대조 기본 켬 / 주문 접수·취소·설정, 체결 등록 기본 꺼짐 / 일일 현황 켬). 발송 지점 3곳 — 활동 로그 저장 훅(`log_event` → 이벤트 종류→카테고리 매핑, 줄 단위 이벤트는 제외), 거래 등록(`register_transaction`), 16:40 스냅샷 뒤 일일 현황(총자산·전일 대비·구성·포트별 평가액). 실패는 `notify.failed` 로그만(본 작업 계속). (2) **챗봇** — 검토 결과 프롬프트가 "HTS 직접 발주"만 알고 9/5~9/7 기능을 몰랐음 → `OPERATIONS_KNOWLEDGE`(증권사 연동·예약주문·무인 실행·사전 갭 취소·완전 무인·예수금 대조·매매 로그·알림의 화면 위치·동작·시각·정지/해제), 전략 지식의 발주 문장 3경로로 수정, 코어 계약 화면 안내 확장, 도구 `auto_exec_status`(설정 스위치·포트별 정지/사유·마지막 무인 실행/자동 승인/사전 갭·완전 무인 설정·예수금 대조·살아 있는 주문·알림 여부, 채팅 ID/토큰 비노출)·`recent_logs`(매매 로그 병합), '오늘'을 KST 로. (3) **문서 점검** — 누락 보완: docs/README(user-guide·auto-execution 설명 갱신, 미등록 문서 4건 색인), 루트 README 주요 기능(증권사 연동·무인 실행·로그/알림·챗봇), features/README(챗봇·US 공식·무인 운영 행), feature-chatbot §10, feature-portfolio §8(알림 API), ADR-008 조건 8(알림), 운영 문서 §2(알림 행), operator-guide(배치 시각·외부 호스트·비밀값·점검), user-guide §4-4, ASSUMPTIONS 6건(예상체결가 근사·예약주문 전환 시각·D+2 예수금·상한 20%·알림 동기 발송·테스트 KST), TODO 3건(예상체결가 오차 기록·예약주문 전환 시각 실측·알림 확장), PROGRESS 전면 갱신(2026-08 상태로 멈춰 있었음).
- 테스트: `tests/test_notify.py` 5건(설정 왕복·검증·마스킹·격리 / 이벤트 카테고리 필터·수준 머리말·꺼짐·실패 로그 / 거래 등록·삭제 알림 / 연결 확인 chat_id 자동·409·502 / 일일 현황 문구·전송), `tests/test_chat_ops.py` 2건(지식 키워드·도구 등록 / 상태·로그 도구 응답·사용자 스코프). 전체 `pytest -q tests/` → **235 passed**. `tsc --noEmit` 통과. Playwright: 설정 › 알림 탭 렌더(토큰·채팅 ID·연결 확인·항목 9개), 콘솔 오류 없음. dev DB 0023.
- Git commit: feat: telegram notifications (bot token in settings, per-category opt-in), chatbot operations knowledge and status/log tools, docs sweep
- 특이사항: 알림은 활동 로그 저장 직후(commit 전) 동기 발송 — 롤백 시 기록 없는 메시지가 갈 수 있음(드묾, ASSUMPTIONS). 실제 텔레그램 발송은 사용자가 봇을 만들어 연결한 뒤 확인 필요(테스트는 HTTP 경계를 대체).


## [2026-09-07] fix | 매매일지 다종목 평가 누락 — 시세 조회 범위 확대 + 커버리지 표기 (사용자 보고 → 1+3안 승인)

- 작업 내용: 사용자 보고("여러 종목 입력 시 보유수익률에서 일부 종목 제외")를 로컬 재현 — 3종목 일지에서 1종목만 평가되고 **전체 원가 220만 중 120만(55%)이 수익률 분모에서 누락**. 원인은 `enrich_valuation` 의 가격 경로가 ①증권사 잔고 ②DB 종가 둘뿐이고, `ohlcv_daily` 에는 전략 대상 6종만 시딩돼 그 밖의 종목은 `price=None` → 평가 루프에서 `continue` 로 조용히 제외되던 것(코드 미입력 행은 조회 시도조차 못 함). **①(근본)**: 수익률 차트가 쓰던 `_ensure_daily_bars`(신규 종목 자동 등록 + KIS 일봉 보충) 경로를 평가에 연결(`_backfill_closes`, 실패 코드 10분 재시도 억제), 코드 미입력 행은 `instruments` 이름 정규화 매칭(`_codes_by_name`, 동명 다수면 미연결). **③(표기)**: `summary` 에 `holdings_count·cost_total·cost_priced·unpriced·price_notes` 노출, 웹 평가손익 카드가 분모를 `cost_priced` 로 표시하고 제외 종목·원가를 명시 — 종전에는 전체 원가(220만)와 부분 평가액(103만)을 나란히 두어 +3.86% 수익률과 어긋나 보였다.
- 테스트 결과: api **234 passed / 2 failed** — 2건은 변경 전 코드(stash)로 재현 확인한 **사전 존재 실패**(`test_from_backtest_conversion_seeds_state`, `test_same_day_fill_does_not_change_order_sheet`). 신규 회귀 테스트 `test_valuation_price_coverage_and_backfill`(4종목: DB적재·KIS보충·이름매칭·불명 → priced 3/4, 분모=cost_priced, unpriced 노출) 통과. 기존 `test_journal_close_reopen_and_dashboard_assets` 는 이름 매칭 도입으로 '원가→평가' 값이 바뀌어 **불변식(합계 = 집계 대상 일지 값의 합)** 검증으로 갱신. web tsc 무오류.
- 문서: feature-dashboard §5(평가 경로·커버리지 표기 의무)·§12(회귀 케이스), NOTES(시딩 6종 한계·기존 경로 재사용 교훈).
- Git commit: fix: mjournal valuation price coverage and honest reporting

## [2026-09-07] fix | 매매일지 수익률 차트에서 코드 미입력 종목 누락 — 평가와 같은 이름 매칭 적용 (사용자 보고)

- 작업 내용: 사용자 보고("그래프에 안 나온다") 확인 — 도넛·평가 카드에는 삼성전자가 +21.2%로 뜨는데 수익률 차트만 "종목 코드가 없어 시세를 붙일 수 없는 종목: 삼성전자"로 제외. 원인은 직전 수정(#144)이 `enrich_valuation` 에만 이름 매칭을 넣고, **형제 경로인 `journal_return_series` 는 여전히 `e.code` 만으로 코드를 해석**하던 것 — 같은 도메인 규칙이 두 곳에 따로 구현돼 한쪽만 고쳐진 상태였다. 차트 경로도 `_codes_by_name` 을 공유하도록 연결하고, 그래도 못 찾는 종목의 안내 문구에 조치법("기록에서 종목코드를 입력하면 라인이 그려집니다")을 추가.
- 테스트 결과: api **234 passed / 3 failed** — 3건 모두 사전 존재(2건은 변경 전 코드로 재현 확인, ws 1건은 라이브 Redis 공유 플레이크로 재실행 시 통과/실패가 번갈아 나옴 — NOTES 기록 항목). 신규 회귀 테스트 `test_return_series_resolves_code_by_name`(코드 없이 이름만 입력 → code 해석·구간 생성·priced=True·안내 없음) 통과. 매매일지 스위트 15/15 green.
- 문서: feature-dashboard §12(차트 코드 해석 회귀 케이스), NOTES(두 경로의 규칙 중복 구현이 화면 간 불일치를 만든 사례).
- Git commit: fix: resolve symbol codes by name in journal return-series too

## [2026-09-07] change | 매매일지 '총 자본금' — 계좌 연동 여부와 무관하게 항상 표시, 기준만 전환 (사용자 지시)

- 작업 내용: 사용자 지적("같은 매매일지인데 화면이 다르다 / 연동 여부와 무관하게 동일한 데이터가 나와야 한다") 검토 — 일지의 본질 데이터(총손익·평가손익·실현손익·보유 패널·수익률 차트)는 이미 연동과 무관했고, **계좌 평가금액 카드 하나만 커버리지 검사 실패 시 통째로 사라져** 총 손익이 `sm:col-span-2` 로 재배치되며 전혀 다른 화면처럼 보였다. 사용자 정의("계좌 평가금액 = 총 자본금, 연동되면 증권계좌 기준, 아니면 등록 종목 수량으로 총액 계산")에 따라 **기준 이원화**로 재설계: 연동 O = 계좌 기준(잔고 주식 평가액 + 예수금 — **잔고만으로 구성**해 일지 평가액과 섞지 않음), 연동 X = 일지 기준(등록 보유 수량 × 현재가). 종전 커버리지 검사는 원래 '일지 평가액에 계좌 예수금을 더해도 되는가'를 판정하던 장치였는데, 기준을 통일하니 섞일 일이 없어져 **카드 표시 여부가 아니라 수익률 범위 판정**으로 역할이 축소됐다 — 계좌 총액에는 일지 밖 종목이 섞일 수 있어 불일치 시 수익률만 미표시하고 `account_mismatch`(종목·일지 수량·잔고 수량)로 사유를 화면에 노출. `account_total` 은 기존 소비처 하위호환으로 유지.
- 테스트 결과: api **236 passed / 3 failed** — 3건 모두 사전 존재(2건 변경 전 코드로 재현 확인, ws 1건은 라이브 Redis 공유 플레이크). 신규 회귀 2종: 미연동 → 일지 기준·예수금 None·수익률 성립, 연동 → 계좌 기준·커버리지 일치 시 수익률 표시 / 불일치 시 **카드 유지 + 수익률 None + mismatch 사유**. 매매일지 스위트 17/17 green. web tsc 무오류.
- 문서: feature-dashboard §5(기준 이원화·커버리지 역할 축소)·§12(회귀 케이스).
- Git commit: change: journal capital card always shown with dual basis

## [2026-09-07] fix | 일봉 확정봉 가드 + 주문표 갱신 대기 안내 (사용자 보고 → 지시)

- 작업 내용: 사용자 질문("15:39 인데 09/08 주문표가 벌써 나온 게 정상인가") 조사 중 **국내 미완성 봉 오염을 발견**(2026-08-31 미국 사고의 재발). 운영 DB 의 2026-09-07 행 8건이 `ingested_at` 기준 **08:40·09:26 KST**(장 시작 20분 전·개장 26분 후)에 적재됐고 거래량이 102110=0·122630=0·069500=3·005930=1 인 스텁 봉이었다. 가짜 종가 105,880 으로 역산한 그리드 101,640/97,405/93,170·익절 110,120·갭 97,308 이 화면값과 **완전히 일치** — 09/08 주문표 전체가 유령 봉 위에 만들어졌음을 확인. `on_conflict_do_nothing`(원본 불변, ADR-002) 때문에 마감 후 진짜 종가가 영구히 막히는 구조라 심각. **(1) 확정봉 가드**: `services/ingest.bar_is_final()` 신설 — 미래 봉 거부, 오늘 봉은 시장 정규장 마감 후에만 허용(KR 15:30 KST / US 16:00 ET, `zoneinfo`). `upsert_daily_bars` 진입점에 두어 네 적재 경로(daily_ingest·매매일지 일봉 보충·seed·ingest_us_daily)를 한 곳에서 차단. **(2) 주문표 갱신 대기 안내**(사용자 지시): 주문표 기준일이 'DB 의 마지막 일봉'이라 16:05 수집 전에는 exec_day 가 '이미 지나간 오늘'로 잡혀 낡은 주문표를 오늘 것으로 오해하게 된다 — `_plan_pending()` 으로 판정해 RAVG·TF·LTM 세 경로에 `pending`·`pending_note` 를 싣고, 화면에 "아직 다음 거래일 주문표가 작성되지 않았습니다 — 16:05 시세 수집 → 16:45 주문표 갱신 후 표시됩니다" 배너와 제목 변경("주문표 갱신 대기")을 추가.
- 테스트 결과: api **240 passed / 2 failed** — 2건 모두 사전 존재(변경 전 코드로 재현 확인). 신규 3종 통과: 확정봉 시장별 경계(08:40·09:26·15:29 거부 / 15:30 허용 / 과거 허용 / 미래 거부), 장중 적재 차단 후 마감 후 진짜 종가가 자리를 차지하는지, 주문표 pending 4상태. web tsc 무오류.
- **미조치(사용자 실행 필요)**: 기존 오염 행 삭제 — 2026-09-07 국내 8종·2026-08-31 QQQ/QLD. 가드는 신규 유입만 막으므로 `DELETE FROM ohlcv_daily WHERE trade_date='2026-09-07'` 후 마감 후 재적재가 필요하고, 그 전까지 09/08 주문표를 발주하면 안 된다. TODO 갱신.
- 문서: NOTES(사고 사실·역산 검증), feature-market-data §5(확정봉 가드 명세), TODO(가드 완료·정리 대기).
- Git commit: fix: guard against unfinished daily bars and flag pending order sheet

## [2026-09-07] change | 완전 무인 기본 켬·즉시/보완 승인 + 무인 실행 허용을 증권사 계좌별로 + 승인·발주 전 계좌 스위치 검사

- 지시: "무인 실행 승인은 표에서 체크하지 않아도 자동으로 발주해야 해" / "무인 실행을 등록된 증권사 계좌별로 설정할 수 있도록" / "현재 무인 매수 허용만 되어 있으므로 매도는 허용하지 않고 매수는 자동으로 매수 주문을 발행해야" / "승인할 때 설정된 계좌에서 해당 옵션이 허용되었는지 먼저 검사하고 실행".
- 검토 결과: 자동 승인이 포트별 옵트인이고 16:45 배치만 돌아, 밤에 켜면 다음 아침 발주가 없었다(스크린샷: 완전 무인 꺼짐·승인 0건). 허용 스위치는 사용자 단위라 계좌를 구분하지 못했다.
- 작업 내용: (1) **자동 승인 기본 켬**(`auto_approve_cfg` enabled 기본 True) — 끄는 것만 포트별 선택. 켜는 즉시 1회 실행(`PUT …/auto-approve` 응답 `run_now`), `POST …/auto-approve/run-now`('지금 승인 실행' 버튼), **08:40 보완 실행** `auto_approve_catchup`(새로 한 일이 없으면 기록·알림 없음). 실행일이 오늘이어도 09:00 전이면 승인(종전에는 '실행일 지남'으로 건너뜀). (2) **계좌별 스위치** `broker_credentials.auto_exec`(0024, 기존 사용자값을 모든 계좌에 복사) — `account_auto_exec(cred)` 가 승인 전(`approve`)·발주 직전(`_execute_portfolio` ①)·자동 승인(`_auto_approve_portfolio`)·사전 갭 취소(`run_preopen_cancel`)·설정 켜기 가드(`put_auto_approve`)의 판정 함수. `GET /settings/auto-exec` → 기본값 + 계좌 목록, `PUT /settings/auto-exec` = 기본값 + 일괄, `PUT /settings/auto-exec/accounts/{aid}` = 계좌별(로그 `autoexec.account_setting`). 새 계좌는 사용자 기본값 상속, `_acct_out`·`auto_exec_view` 에 스위치·계좌 라벨. 거절·생략 문구에 계좌 라벨("계좌 위탁 의 무인 매수 허용이 꺼져 있습니다", "계좌 설정에서 … 생략"). (3) 매수만 허용 → 자동 승인은 매수 줄만, 매도 줄은 '수동 필요'(기존 동작 확인·테스트 고정). (4) 웹: 설정 › 무인 실행을 계좌별 카드(라벨·계좌·모의·연결 포트·스위치 3개)로, 계좌 2개 이상이면 일괄 적용 버튼; 실전매매 패널 '켜짐/켜짐 · 대기/꺼짐' 실효 상태·계좌 라벨·'지금 승인 실행'·저장 시 즉시 승인 결과 표시; 설정 확인창 문구. 챗봇 운영 지식·상태 도구(settings.accounts, portfolio.account/allowed) 갱신. (5) 문서: ADR-008 조건 1·2·14, 운영 문서 §1·§2, operator-guide 배치 시각, user-guide §3, feature-portfolio §8.
- 테스트: `tests/test_account_autoexec.py` 3건(기본값·일괄·계좌별·상속·격리·로그 / 승인 전 연결 계좌 검사·다른 계좌만 켜면 거절·발주 직전 재검사 생략 / 매수만 허용 시 자동 승인 매수만·사전 갭 취소 계좌 스위치), `test_autoapprove.py` 갱신(기본 켬·즉시 실행·run-now·08:40 보완 승인·변경 없음 조용함), 기존 설정 응답 2곳. 전체 `pytest -q tests/` → **245 passed**. `tsc --noEmit` 통과. Playwright: 설정 › 무인 실행 계좌별 카드, 실전매매 패널 상태, 콘솔 오류 없음. dev DB 0024.
- 테스트 위생(같은 작업에서 고침): ① `test_valuation_price_coverage_and_backfill`(#144)이 공유 CI DB 에 069500·102110 의 2026-09-04 가짜 일봉을 남겨 `test_same_day_fill…`·`test_from_backtest_conversion…` 2건이 깨지던 것(HISTORY 의 '사전 존재 실패' 표기는 오진) → 정리 fixture `_clean_bars`. ② `test_return_series_resolves_code_by_name` 의 봉이 high < close 로 검증기에 전부 거부되어 앞 테스트 잔여에 기대고 있던 것 → high 수정. ③ 자동 승인 기본 켬 뒤 테스트 배치가 DB 의 옛 테스트 포트 수백 개를 승인해 무인 실행 테스트 6건이 깨짐 → `run_auto_approve(only_credential_ids=…)` 필터 + 무인 실행 테스트가 자기 포트의 결과만 보고 자기 계좌에만 가짜 클라이언트를 주도록 수정. CI DB 잔여 승인 행 157건은 cancelled 로 정리.
- Git commit: change: auto-approve on by default with run-now and 08:40 catch-up; auto-exec switches per broker account, checked before approval and placement
- 특이사항: 배포 직후 기존 사용자 설정값이 계좌에 복사되므로 동작은 바뀌지 않지만, 자동 승인 기본 켬 때문에 **무인 매수 허용이 켜진 계좌에 연결된 국내 포트는 다음 16:45(또는 08:40)부터 승인 없이 발주**된다 — 원치 않는 포트는 '완전 무인 운영 › 설정'에서 끈다. 내일 아침 발주를 원하면 배포 후 포트 패널의 '지금 승인 실행'(또는 설정 저장)으로 오늘 밤 승인해 둔다.

## [2026-09-07] ui | 설정 › 무인 실행 — 옵션 설명은 상단 한 번, 계좌는 한 줄씩 스위치 표

- 지시: "이 설정에서 매번 같은 내용을 반복 출력할 필요 없습니다. 상단에만 어떤 옵션인지 설명하고 아래 줄에 계좌 한 줄씩 설정."
- 작업 내용: `settings/page.tsx` `AutoExecSettings` — 옵션 3종(무인 매수·무인 매도·사전 갭 취소) 설명 카드를 상단에 한 번만, 그 아래 계좌 표(계좌·연결 포트·스위치 3열, 허용/꺼짐 배지). 계좌 2개 이상이면 일괄 적용 줄, 하단에 통제 규칙 한 단락. 동작·API 변경 없음.
- 테스트 결과: `tsc --noEmit` 통과. Playwright: 계좌 2행·스위치 6개·설명 블록 3개 렌더, 콘솔 오류 없음. API 테스트 변경 없음(직전 245 passed).
- Git commit: ui: compact per-account auto-exec settings — describe options once, one row per account

## [2026-09-08] change | 무인 매매 단일 실행 (ADR-009) — 승인 단계·주문표 버튼 제거, 계좌 플래그만 참조, 09:00 시각 동결, 취소 버튼, 09:15 감시·하트비트

- 지시(2026-09-08): "1. 매수/매도 수량은 실시간 반영(자본금 입출금) 2. 무인매수 대기 상태 vs 아닌 상태를 명확히 표기 3. 설정의 on/off 플래그만 참조, 주문표의 버튼은 모두 제거, 해제하면 즉시 동작 4. 무인을 취소하고 수동 입력할 수 있는 취소 버튼 5. 분석·검토". 배경: 09-08 아침 16:45 승인 3줄이 09:01 에 발주되지 않았고(배치 미실행, 원인 미확정) 화면은 '무인 승인'만 보여 장중에야 인지.
- 검토·결정(사용자 4건): 시장가 줄(레버리지) **09:01 시장가 무인 포함** / 설정 해제 = **살아 있는 무인 주문 즉시 취소** / 플래그는 **계좌별** / 현금 원천 **원장 + 매수가능조회**. 분석에서 요청 1 이 B안(당일 등록 불변)과 충돌함을 짚고, 동결 기준을 '날짜 → 실행일 09:00 시각'으로 바꾸면 두 요구가 함께 성립함을 확인. ADR-009 작성, ADR-008 부분 대체.
- 작업 내용: (1) `signals.py` — `freeze_at`(09:00 KST)·`_state_before(cutoff: date|datetime)`·`_portfolio_orders(force_freeze, now)`: 09:00 전 실시간 갱신, 실행기가 `frozen_at` 을 찍으면 화면은 스냅샷(`frozen`), 미동결 실행일은 09:00 이후 불변·재계산 표시(2026-09-02 보존 규칙 유지). `_next_exec_day` 캘린더 휴장 스킵, `gap_cancel_exact` 노출. (2) `autoexec.py` 재작성 — 계좌 플래그 `{buy, sell, daily_buy_cap_pct}`(끄면 `_cancel_for_turned_off`), `run_auto_execution` 이 09:01 에 모든 국내 포트를 계산·동결하고 플래그 켠 포트만 시가 → 갭 → 원장 대조 → **상한·주문가능 수량 축소** → 지정가/시장가 발주(`place_order(price=None)` 시장가), 상태 모델 `auto_exec_state`(off/paused/skipped_user/waiting/running/ran/missed + 사유), `skip`/`unskip`(이번 실행일 무인 취소 — 09:00 전 건너뜀, 09:01 후 주문 취소), `run_watchdog`(09:15 지연 실행), `touch_heartbeat`. (3) 삭제: `autoapprove.py`·`preopen.py`·테스트 2종, 배치 16:45/08:40/08:57, 승인·전량 취소·완전 무인 엔드포인트. 추가 배치 `auto-exec-watchdog` 09:15·`pipeline-heartbeat` 60초, worker·scheduler 헬스체크 = 하트비트 키. (4) 웹 — 주문표: 예약/승인/전체 선택/새로고침/완전 무인 패널/줄별 취소 제거, 상태 배너 + [이번 실행일 무인 취소 → 수동]·[되돌리기]·[다시 켜기]만, '무인' 열 읽기 전용, 제목에 동결/실시간 표기. 설정: 플래그 2개 + 하루 상한 입력, 켬·끔 확인창(끄면 즉시 취소 안내). (5) 챗봇 지식·상태 도구, 알림 카테고리 7종, 로그 라벨('(구)'), README·가이드·운영 문서·feature §5/§8/§12·TODO·ASSUMPTIONS·NOTES 갱신. VERSION 0.11.0(마이너 — 새 동작).
- 테스트 결과: api `pytest -q tests/` → **238 passed / 0 failed**(격리 DB stocklab_ci). 재작성·신규: `test_autoexec.py` 6건(플래그·상태·404 / 갭·시장가·매도 우선·재실행·감시 중복 없음·15:45 확정 / 상한·주문가능 축소·수동 처리·연속 실패 정지 / 수동 모드·기준일 불일치·사용자 취소 전후·설정 해제 취소 / 감시 지연 실행 / 미국·대조 정지), `test_account_autoexec.py` 2건, `test_autoexec_review.py` 4건(폴백 축소·원장 대조·매수가능 0 생략), `test_signals.py` 동결 규칙·캘린더 2건 + 기존 `test_same_day_fill…`(기준일 헬퍼를 두 종목 공통 마지막 봉으로 고쳐 사전 존재 실패 해소). `test_activity`·`test_chat_ops`·`test_notify` 갱신. web `tsc --noEmit` 무오류(컨테이너). 화면 실행 확인은 **불가** — 로컬 web 컨테이너가 7일 전부터 `lightningcss.linux-arm64-musl.node` 누락으로 모든 페이지 500(사전 존재 환경 결함, 이번 변경과 무관) → 배포 후 실제 화면 확인 필요.
- 배포 주의: ① 기존 `approved` 행은 실행기가 읽지 않으므로 `UPDATE broker_orders SET status='cancelled', message='ADR-009 전환 정리' WHERE status='approved'` ② `docker compose up -d worker scheduler`(헬스체크 정의 변경은 restart 로 반영되지 않음) ③ 거래일 캘린더 갱신(2026-09-01 까지만 — 휴장 미등록이면 다음 거래일 '기준일 불일치'로 발주 안 함, TODO 상) ④ 09-08 미실행 원인(beat 미발송 vs 큐 미소비)은 사용자 로그 확인 대기 — 하트비트로 재발은 드러난다.
- Git commit: change: unattended trading as a single 09:01 execution — account flags only, 09:00 freeze, cancel button, watchdog and heartbeat (ADR-009)

## [2026-09-08] feat | 배포 후 훅(scripts/post-deploy.d) + alembic 0025 전환 정리 + KIS 거래일 캘린더 갱신 (사용자 지시 "배포에 필요한 명령을 deploy.sh 에 포함, 외부 스크립트 호출 방식 검토")

- 검토: 후보 ① deploy.sh 인라인(버전별 절차가 쌓이고 옛 절차가 새 배포에서도 돎) ② 서버 전용·원격 다운로드 스크립트(재현·검토 불가, 태그와 어긋남) ③ 저장소 안 `scripts/post-deploy.d/*.sh` 를 태그와 함께 배포하고 deploy.sh 가 헬스 검증 뒤 서브셸에서 `source`(채택 — deploy.sh 는 옛 복사본으로 돌지만 훅은 새 태그의 것) ④ 한 번만 일어나는 데이터 전환은 alembic 데이터 마이그레이션(채택 — 정확히 한 번·트랜잭션·추적). 규칙은 `scripts/post-deploy.d/README.md`, 검토 요약은 operator-guide.
- 작업 내용: (1) `deploy.sh` 5단계 "배포 후 훅" — 번호순 source, 훅 실패 = 배포 검증 ✗(다음 훅 계속), `--skip-hooks`. (2) 훅 3종: 10 ADR-009 전환 확인(approved 잔존 0건) · 20 거래일 캘린더 갱신 · 30 하트비트 확인(150초). (3) alembic `0025_adr009_cancel_stale_approved` — 옛 `approved` 행 → cancelled(데이터만). (4) **거래일 캘린더 갱신** `services/calendar.py` + `kis_client.fetch_holidays`(KIS 국내휴장일조회 CTCA0903R, 페이지 추적) + CLI `python -m app.services.calendar --days 120` + 워커 `refresh-trading-calendar` 일요일 06:00 — pykrx 는 미래 거래일을 주지 않아(실측) KIS 로. 캘린더는 2026-09-01 에서 멈춰 있었고 ADR-009 의 실행일 계산이 캘린더에 의존하므로 선결 조건이었다.
- 테스트 결과: `tests/test_calendar.py` 2건(TR 파싱·페이지 추적 / upsert 멱등·변경 기록·`_next_exec_day` 연동) 통과. 로컬 dev 스택에서 실제 실행: alembic 0025 적용(head 0025) → 훅 3종 모두 ✓ — 캘린더 CLI 가 KIS 에서 121일을 받아 추가하고 2026-09-24·25(추석)·10-05·10-09·12-25·12-31·2027-01-01 휴장을 등록, 재실행은 변경 0(멱등), 하트비트 확인. `bash -n` 구문 검사 통과. 전체 스위트 결과는 아래 항목.
- 문서: ADR-009 영향(배포), auto-execution §2·§5-6, operator-guide(훅·검토), README 배포 블록, TODO(캘린더 완료), NOTES(KIS 휴장일 TR 실측·pykrx 한계).
- Git commit: feat: post-deploy hooks, alembic 0025 transition cleanup, KIS trading-calendar refresh

## [2026-09-08] fix | deploy.sh 자기 갱신 — 배포되는 버전의 스크립트로 다시 실행 (사용자 지적 "변경된 deploy.sh 는 기존 스크립트로 적용 안 되지 않나")

- 지적이 맞다: deploy.sh 는 시작 시 자기 임시 복사본으로 재실행되므로 서버의 옛 스크립트(v0.11.0 이하)로 `deploy.sh v0.11.0` 을 돌리면 체크아웃으로 새 스크립트가 내려와도 끝까지 옛 절차가 돈다 — 재빌드·alembic 0025·헬스 검증·컨테이너 재생성은 되지만 5단계 배포 후 훅(전환 확인·캘린더·하트비트)은 실행되지 않는다.
- 작업 내용: 체크아웃 직후 1-0 단계 — 배포되는 버전의 `scripts/deploy.sh` 가 실행 중인 복사본과 다르면(`cmp`) `DEPLOY_SH_UPGRADED=1` 로 그 스크립트를 원본 인자 그대로 `exec`(한 번만). 재실행된 스크립트의 fetch·checkout 은 같은 대상이라 멱등. 첫 전환용 우회(README·operator-guide): `git show <태그>:scripts/deploy.sh > /tmp/deploy.sh && bash /tmp/deploy.sh <태그>`. VERSION 0.11.1(패치 — 스크립트 보강).
- 테스트 결과: `bash -n` 통과. 샌드박스 git 저장소(bare origin + 태그의 deploy.sh 를 스텁으로 교체)에서 실제 실행 — ① 새 스크립트로 `v0.12.0` 배포 시 체크아웃 뒤 "배포되는 버전의 스크립트로 다시 실행합니다" 로그 후 스텁이 원본 인자(`v0.12.0 --skip-hooks --port 19999`)와 `DEPLOY_SH_UPGRADED=1` 을 받아 실행됨 ② `DEPLOY_SH_UPGRADED=1` 이 이미 있으면 재실행하지 않고 다음 단계로 진행. API 테스트 변경 없음(직전 240 passed).
- Git commit: fix: deploy.sh re-executes itself with the deployed version's script

## [2026-09-08] ui | 대조 경고 → ⓘ 참고/⚠️ 경고 분리·문구 축약, 포트 선택 칩 강조, 연결 계좌 잠금 (사용자 지시 3건)

- 지시: "⚠️ 계획과 등록된 거래가 다릅니다가 왜 자꾸 나오나" → 원인: 지정가 미체결(`missing`, 서버 level=info)을 화면이 warn 과 같은 배너로 띄워 그리드 전략에서 거의 매일 노출. "참고용은 이모티콘 + 롤오버 설명" / "문장이 너무 어려워요, 핵심만" / "선택된 계좌가 잘 안 보임 — 완전히 구분" / "연동된 계좌는 수정 못 하게 비활성화 검토·포함".
- 작업 내용: (1) `reconcile_plan` 문구 한 줄 축약(`200 ETF 매수 111주 — 체결 등록 없음` / `계획에 없던 거래: 레버리지 매수 3주` / `200 ETF 매수: 계획 8주 → 등록 11주 (+3)`) + `label/plan/filled` 필드. 화면은 warn 만 ⚠️ 배너(각주 한 줄), info 만 있으면 주문표 제목 옆 ⓘ 툴팁("09-08 미체결 — 200 ETF 매수 111주 · 200 ETF 매도 666주 / 지정가 미도달이면 정상"). (2) 무인 상태 줄 설명·제목 부제·수동 모드 각주를 핵심만으로 축약. (3) 포트 칩: 선택 = `bg-ink` 흰 굵은 글씨·✓·`ring-2 ring-accent`, 비선택 = 20% 틴트·회색·opacity-80. (4) 증권사 연동: 연결되면 select 비활성(🔒 연결됨, 툴팁), 확인창 있는 '연결 해제' 버튼으로만 변경(체결 가져오기·무인 발주가 계좌에 묶여 실수 변경 시 원장이 섞임). 카드 문구의 '예약주문 접수' 잔재 제거. VERSION 0.11.2(패치).
- 테스트 결과: api `test_broker`·`test_autoexec_review`·`test_autoexec`·`test_account_autoexec` 통과(문구 변경에 맞춰 assertion 2곳 갱신: `(-3)`·`계획 8주 → 등록 11주`, `체결 등록 없음`). web `tsc --noEmit` 무오류. 화면 실행 확인은 로컬 web 컨테이너 결함으로 불가 — 배포 후 확인.
- Git commit: ui: split reconcile info/warn (ⓘ tooltip vs banner), terse texts, distinct selected chip, lock linked broker account

## [2026-09-08] feat | 소량 진입 부트스트랩 (ADR-010) — 콜드 스타트 첫 10거래일, 목표 미달분 15% 종가 지정가 · 하루 매수 상한 기본 0

- 지시: "처음 시작하는 보유 주식 없는 사용자가 매매가 안 될 것 같다 — 시뮬레이션 검증" → "매매 성공 확률 25%는 너무 작다. 레짐별 소량으로 시작해 RAVG 2.5 로 수렴하는 알고리즘을 개발, 입력 변수를 바꿔 가며 최적을 찾아라" → "검토된 내용대로 구현, 공식 추가이니 단계별로 코드·사이드 이펙트 검증". 상한 20% 는 "참고용, 사용하지 않음".
- 연구: cold-start-entry-study(현행 첫 체결 중앙 10일, 하락장 시작 78일) → fast-entry-study(1단계 72 · 2단계 162 조합, 3단계 시작 시점 +7/+14 표본 견고성, §7 상한 수준). 채택: `boot_days 10 · boot_frac 0.15 · boot_delta 0(종가, 체결 74%) · boot_bear_mult 0.5` — 첫 체결 1~2일(90% ≤3일), 노출 50% 도달 15 → 7일, 250일 수익 차이 중앙 −0.09~0.00%p·평균 ≈ 0, MDD 불변.
- 작업 내용: (1) `Params` 4개(알고리즘 설정 '초기 진입' 그룹, `boot_days` 정수). (2) `planner.plan(..., days_since_start)` — 창 안이고 목표 미달이면 `boot` 지정가 1줄(현금버퍼 안), 그리드 예산 (1−f), 하락장은 boot 만 f×0.5 + 갭 기준 생성; None 이면 종전과 동일. (3) 엔진: 첫 OK 계획일 = 0일째(절단 실행 동일 → ADR-005 유지), `plan_final` 도 전달. (4) `signals._portfolio_orders`: 시작일 = max(포트 생성일, 첫 거래일), 뒤 봉 수 → `days_since_start`; 응답 `boot {day, days}`. (5) 실행기: 갭 필터 대상에 `boot`. (6) 화면: 종류 '초기 진입', 계산 근거 설명, 제목 '초기 진입 n/10일', 하락장 + 매수 줄 없음 안내 한 줄. (7) 상한 기본 0(설정 문구 '참고용·진입 속도 제한'). (8) 문서: ADR-010, feature-strategy-engine §5.5/§12/§14/변경 이력, strategy-guide, user-guide, 챗봇 지식, INDEX·docs README·TODO·ASSUMPTIONS. VERSION 0.12.0(마이너 — 전략 개정).
- 단계별 검증: ① 플래너·엔진 기존 48 테스트 통과(플래너 테스트는 인자 미지정 = 종전) ② **끔 = 도입 전과 비트 동일** — 2017~2026 KODEX 백테스트 KPI(total_return 2.454899·CAGR 16.02%·MDD −21.66%·거래 375)·최종 자산 345,489,906원 일치, boot 줄 0 ③ 기본값 영향: boot 는 첫 OK 계획일(2018-02-08)부터 정확히 10일, 최종 자산 346,063,150원(+0.17%), 거래 379, MDD −21.67% — 모델 포트 잔고가 소폭 달라져 공용 KR 신호의 그리드 수량도 소폭 변함(가격·레짐·E 동일) ④ 신규 단위 6종(`test_bootstrap_entry.py`) ⑤ 실전 경로(`test_signals`): 오늘 시작 포트 boot 줄·`boot {day:1, days:10}`·스냅샷 저장, 시작 40일 전 포트 없음, 모델 신호 없음 ⑥ 실행기(`test_autoexec`): 갭 출발이면 boot 도 생략, 아니면 종가 지정가가 그리드보다 먼저 ⑦ 상한 기본 0 반영으로 기대값 3곳 갱신(상한 검증 테스트는 20% 명시) ⑧ 전체 `pytest -q tests/` **247 passed**, web `tsc --noEmit` 무오류.
- 사이드 이펙트 점검: 새 백테스트 잡은 첫 10 활동일에 boot 포함(저장된 잡 불변) · 포트 동결 변수(`params.algo`)에 boot 키가 없으면 기본값 적용(켬) · 미국 TF/LTM 경로 무영향 · `reconcile_plan` 은 종목·방향 합계라 boot 를 매수로 정상 집계 · 화면 실행 확인은 로컬 web 컨테이너 결함으로 불가(배포 후).
- Git commit: feat: bootstrap small-entry for cold starts — 10 trading days, 15% of shortfall at last close (ADR-010); daily buy cap default 0

## [2026-09-08] ui | 대조 — 부분 체결(short)도 참고(ⓘ)로 (사용자 지적 "이모지·롤오버로 바꾼 것 아닌가")

- 원인: 0.11.2 에서 `missing`(미체결)만 info 로 내리고 `short`(계획 > 등록, 부분 체결)는 warn 배너에 남겨 두어, 익절 425주 중 25주 체결 같은 정상 부분 체결이 "⚠️ 계획과 등록 체결이 다릅니다"로 떴다. 무인 정지 판정은 이미 short 를 정상으로 보고 있었으므로 표시만 어긋난 상태.
- 작업 내용: `reconcile_plan` short → level info(kind 불변). 배너는 unplanned·excess 만. ⓘ 툴팁에 "200 ETF 매도 425주 중 25주 체결" 형식 추가. 테스트 기대값 2곳 갱신. VERSION 0.12.1.
- Git commit: ui: treat partial fills (short) as info in the reconcile tooltip; banner only for unplanned/excess

## [2026-09-08] fix | 초기 진입은 보유 0 으로 시작한 포트만 (사용자 지적 "보유 수량이 있는 계좌인데 초기 진입이 왜 나오나")

- 원인: 0.12.0 의 조건이 "시작 후 10거래일 + 목표 미달"이어서 보유분을 입력해 시작한 포트(400주 보유)에도 boot 줄이 나왔다. 연구(fast-entry-study)는 현금만으로 시작하는 콜드 스타트를 가정했다.
- 작업 내용: `signals._portfolio_orders` — 시작일까지 등록된 매수(보유분 입력·전환 시드)가 있으면 `days_since_start=None`. 엔진 — `initial_lots` 시작이면 None. ADR-010 결정문·feature §5.5·user-guide 갱신. 테스트: 엔진 initial_lots → boot 없음, 실전 보유 시작 포트 → boot 없음.
- Git commit: fix: bootstrap entry only for portfolios that started with zero holdings

## [2026-09-08] ui | 수익률 추이 차트를 지수(100) 대신 시작 대비 % 로 (사용자 지시 "109 가 9% 상승이라는 뜻인지 직관적으로")

- 작업 내용: `portfolio/page.tsx` 차트 값 = index − 100, 가격 축 포맷 `+9.20%`, 마지막 값 라벨·헤더 '시작 대비 %', 0% 기준 점선. 상단 수익률에 + 부호. 후속 지시로 **매매일지 수익률 차트와 같은 스타일**(포맷·격자·글꼴·크로스헤어)로 맞추고 평가액 툴팁은 제외. API·데이터 변경 없음.
- Git commit: ui: show the TWR chart as % vs start instead of an index

## [2026-09-08] ui | 주문표에 무인 매수/매도 플래그 가시화 (사용자 지적 "설정을 바꿔도 주문표에 변화가 없어 어떻게 적용됐는지 알 수 없다")

- 원인: 상태 줄 문구("무인 매수 대기"/"무인 매수·매도 대기")만 바뀌고, 표의 '무인' 열은 방향과 무관하게 "대기"라 어느 줄이 나가고 어느 줄이 수동인지 알 수 없었다.
- 작업 내용: 상태 줄에 `매수 ON`·`매도 OFF` 배지(롤오버: 설정 위치) + "09:01 발주 예정 n줄 · 수동 m줄" 요약. '무인' 열은 실행 전에도 줄별 `🤖 09:01 발주` / `수동`(이유) / `수동(취소)` / `—`. 서버 변경 없음. VERSION 0.12.2.
- Git commit: ui: show buy/sell flags and per-line 발주/수동 on the order sheet before execution

## [2026-09-08] fix | 주문표 '무인' 열 — 실행 전에는 옛 행이 예고를 가리지 않게 (사용자 질문 "왜 취소됨이 나오나")

- 원인: 배포 전 16:45 자동 승인이 만든 09-09 `approved` 행을 alembic 0025 가 `cancelled` 로 정리했는데, '무인' 열이 line_key 로 그 옛 행을 찾아 "취소됨"을 보여 "🤖 09:01 발주" 예고를 가렸다. 실행기는 그 행을 읽지 않으므로 동작은 정상.
- 작업 내용: 상태가 대기·실행 중·이번 실행일 취소이면 행을 보지 않고 예고(발주/수동)만, 실행 후(ran 등)에만 실제 행 상태. VERSION 0.12.3.
- Git commit: fix: order sheet 무인 column ignores stale rows before execution

## [2026-09-09] feat | 장 시작 전 예상 시가 표기 — 08:30~09:10 매 분 관찰 (사용자 지시)

- 지시: "08:30~09:10 분 간 거래를 일정 간격으로 체크해서 마지막 시가를 예측해서 예상 시가를 표기".
- 작업 내용: `app/preopen_watch.py` — 매 분(beat 08:30-59·09:00-10, 휴장 스킵) KODEX·TIGER 200 의 예상체결가(09:00 전, FHKST01010200)/확정 시가(09:00 후, stck_oprc; 0 이면 현재가)를 Redis 에 표본 누적. `/signals/daily` 에 `expected_open {price, at, kind, gap_hit, samples}`(KR). 화면: 갭 경고 아래 "🕗 예상 시가 105,300원 (08:58) — 갭 기준 위, 그리드 유지" / "⤫ … 이하 → 그리드·초기 진입 매수 생략 예정", 롤오버에 최근 표본. **표시 전용** — 발주 판단은 09:01 실행기의 실제 시가(2026-09-06 사전 갭 취소와 달리 취소·발주 없음). VERSION 0.13.0(마이너 — 새 동작).
- 테스트 결과: `tests/test_preopen_watch.py` 2건(09:00 전 예상체결가·후 확정 시가·현재가 대체·0 미기록·표본 누적 / 갭 판정·기준 없음·관찰값 없음) 통과, `tsc --noEmit` 무오류. 화면 확인은 배포 후.
- 같은 PR(사용자 승인 2026-09-09): **무인 상태 붉은 박스 삭제** → 제목 오른쪽 칩(상태·ON/OFF, 정지·기록 없음만 붉게, 상세는 롤오버) + '무인' 열 헤더에 `취소`/`되돌리기` 링크 + ⓘ. `다시 켜기`는 칩 안. 동작 변경 없음.
- Git commit: feat: pre-open expected price watch (08:30–09:10) shown on the order sheet

## [2026-09-09] fix | 09:01 KIS 유량 초과(EGW00201) — 앱키 공용 초당 제한·주문 재시도·폴링 양보 (사용자 보고 "두 계좌가 각기 다른 이유로 실패")

- 분석: 두 실패 모두 EGW00201(초당 거래건수 초과). A 계좌는 잔고 조회가 15초 재시도 뒤에도 실패해 전 줄 생략, B 계좌는 그리드 3차 주문 POST 실패(POST 는 재시도 없음). 원인은 09:01 에 다른 프로세스(실행기·10초 시세 폴링·예상 시가 폴링)가 같은 앱키로 동시 호출하는데 스로틀이 인스턴스 단위였던 것 + 주문 POST 무재시도. 상세 NOTES.
- 작업 내용: `KisClient._shared_throttle` — Redis 초당 카운터 `kis:rl:{env}:{앱키해시}:{초}` (실전 10/초, 모의 1/초, Redis 없으면 인스턴스 스로틀만) · `KisTradingClient._post` EGW00201 한정 1/2/4초 재시도(다른 오류 무재시도 유지) · 실행기 `autoexec:running` 플래그(180초) + 포트 사이 1초 간격 · `poll_quotes`·`poll_expected_open` 은 실행 중이면 건너뜀 · '무인' 열 사유 60/80자(전문 롤오버). VERSION 0.13.1.
- 테스트 결과: `tests/test_kis_rate_limit.py` 2건(앱키·환경별 초당 한도·다음 초 대기·Redis 없음 통과 / 유량 초과 2회 뒤 성공·4회 실패·다른 오류 무재시도) + 전체 `pytest -q tests/` **252 passed, 238 warnings**, `tsc --noEmit` 무오류.
- Git commit: fix: shared per-app-key KIS rate limit, retry orders on EGW00201 only, pollers yield during the 09:01 run

## [2026-09-09] feat | 실시간 현재가 vs 주문선 그래프 (사용자 지시 · 기술 검토 후 승인)

- 지시: "그래프 하나 추가 — 그리드 매수·매도 가격을 점선으로, KIS 로 현재가 수집, 일정 간격, 실시간으로 흐르며, 매수·매도 선과 얼마나 떨어져 있는지가 핵심, 과도한 KIS 호출 금지".
- 검토·결정: 기존 10초 현재가 폴링(7종목 ≈ 0.7건/초)을 재사용해 **KIS 호출 추가 0** — 워커가 Redis 시계열에 누적, 화면은 첫 로드 REST + WS `/ws/quotes` 구독(기존 채널). 5초 주기·KIS 웹소켓(체결가 스트림)은 이득 대비 범위가 커 2단계로 보류.
- 작업 내용: `quotes.py` `append_sample/read_series/backfill_from_minutes` + `GET /quotes/series`(인증, 오늘·빈 시계열이면 1분봉 1회 백필·10분 잠금) · `worker.poll_quotes` 누적 한 줄 · 웹 `components/livechart.tsx`(lightweight-charts 라인 + 점선 가격선: 그리드/초기 진입 빨강·익절 파랑·갭 기준 회색, KST 시각축, 최근 60분 따라가기/하루 전체, 상태 배지 실시간/폴링, 줄별 거리 % 패널·가장 가까운 선 강조, WS 실패 시 15초 폴링) · 실전매매 주문표 아래 카드(국내·주문표 있을 때). 문서: feature-portfolio §5/§8, feature-market-data, user-guide. VERSION 0.14.0(마이너 — 새 화면).
- 테스트 결과: `tests/test_quotes_series.py` 3건(누적·정렬·상한 / 백필 1회 잠금 / 엔드포인트 인증·오늘 조회) 통과, 전체 `pytest -q tests/` **254 passed + 1 flake**(`test_ws_quotes::test_cached_quote_sent_on_subscribe` — 공유 Redis 타이밍, NOTES 기록 항목, 단독 실행 통과), `tsc --noEmit` 무오류. 로컬 워커 재시작 뒤 `quotes:series:102110:2026-09-09` 에 10초 점이 쌓이는 것 확인. 화면 확인은 배포 후(로컬 web 컨테이너 결함).
- Git commit: feat: live price vs order-line chart on the order sheet (Redis series from the 10s poller, WS stream, distance panel)

## [2026-09-09] fix | 실시간 그래프 — 주문선이 축척 밖이라 보이지 않던 것 + 터치 = 체결 표시 (사용자 지적)

- 원인: lightweight-charts 자동 축척이 현재가 범위(±0.3%)만 잡아 점선(익절 +3.4%, 그리드 −4.5% …)이 화면 밖에 있었다.
- 작업 내용: `autoscaleInfoProvider` 로 축 범위에 주문선 포함 — 기본 '가까운 주문선'(현재가 위 첫 매도선·아래 첫 매수선), '모든 주문선', '가격만' 토글. 터치 판정(표본 ≤ 매수선 / ≥ 매도선) → 첫 터치 시각 ● 마커(`createSeriesMarkers`), 선 실선 `✓체결`, 거리 패널 `✓ 터치 — 체결 (시각)`; 09:01 발주 행 상태가 filled/partial 이면 `체결 확인`. 각주에 "10초 표본 사이 체결은 놓칠 수 있어 확정은 15:45". VERSION 0.14.1.
- 테스트 결과: `tsc --noEmit` 무오류(웹만). 화면 확인은 배포 후.
- Git commit: fix: live chart includes order lines in the price scale; touch = fill markers and panel state

## [2026-09-09] fix | 텔레그램 알림 — 실패를 화면에 드러내기 (사용자 보고 "메시지가 안 온다", 토큰 실측)

- 분석: 개발 환경(사내망)에서는 텔레그램 API 가 TLS 단계에서 차단(Connection reset)돼 토큰 검증·전송 테스트가 불가능했다(NOTES). 앱 로직상 조용히 안 보내는 경우 3가지 — ① 설정 미완(켬·토큰·채팅 ID 중 하나 빠짐 = `ready` false) ② 항목 미체크(주문·체결 등록은 기본 꺼짐) ③ 전송 실패(매매 로그 `notify.failed` 로만 남고 설정 화면엔 표시 없음). 운영 서버가 같은 망이면 ③ 이 매일 남는다.
- 운영 확정(사용자 서버 조회): 서버는 텔레그램 접속 정상·토큰 유효, `user_settings.notify` 에 events·채팅 ID·토큰은 있으나 **`enabled` 키가 없음** → `maybe_notify` 가 조용히 False. 즉 ①(설정 미완: 알림 보내기 꺼짐). 조치: 연결 확인 성공 시 `enabled=true` 자동 설정(응답 `enabled_now`), 설정 화면에 "봇은 연결됐지만 알림 보내기가 꺼져 있어 …" 경고 + '지금 켜기' 버튼.
- 작업 내용: `notify.py` — 마지막 전송 성공/실패(시각·사유)를 `user_settings.notify.last` 에 기록(자동 발송·연결 확인 모두), `GET /settings/notify` 의 `last`; `_humanize` 에 연결·TLS·시간 초과 사유("텔레그램 서버에 연결할 수 없습니다 — 서버 네트워크 차단 …"). 설정 › 알림 상단에 "✓ 마지막 전송 성공 시각" / "⚠️ 마지막 전송 실패 시각 — 사유" 줄. VERSION 0.14.2.
- 테스트 결과: `test_notify` 갱신(실패 뒤 `last.error`·`sent_at`, 연결 오류 문구) 통과 — `test_notify`·`test_activity` 8 passed, 18 warnings, `tsc --noEmit` 무오류.
- Git commit: fix: surface the last Telegram send result and humanize network errors

## [2026-09-09] fix | 툴팁(Tip)이 표 안에서 잘려 보이지 않던 문제 (사용자 지적 "무인 롤오버 설명이 안 나온다")

- 원인: `Tip` 툴팁이 `position:absolute` 라 표를 감싼 `overflow-x-auto` 컨테이너(CSS 규칙상 세로 overflow 도 auto 로 바뀜) 안의 '무인' 열 헤더 ⓘ 에서 위로 뜨는 툴팁이 잘려 보이지 않았다. 제목 줄의 ⓘ(컨테이너 밖)는 정상이라 차이가 났다.
- 작업 내용: `Tip` 을 뷰포트 기준 `fixed` 로 — hover/focus 순간 트리거 사각형으로 위치(위/아래·좌우 여백) 계산. 모든 Tip 에 적용. VERSION 0.14.3.
- Git commit: fix: render Tip tooltips as fixed so they are not clipped inside scroll containers

## [2026-09-09] feat | 무인 재시도 — 09:01 실패·생략 줄을 장중에 다시 발주 (사용자 지시 "API 실패 시 취소 대신 재시도")

- 배경: J-한투-ISA 계좌처럼 09:01 발주가 KIS 오류로 실패하면 그날은 HTS 로 직접 넣는 것 외에 방법이 없었고, 실패 뒤에도 '취소' 만 보이는데 취소할 주문이 없어 쓸모가 없었다.
- 작업 내용: `autoexec.py` — 발주 절차(③ 시가·갭 → ④ 잔고 대조·매도 한도 → ⑤ 상한·매수가능 → 발주)를 `_place_lines` 로 분리해 09:01 실행과 재시도가 **같은 코드**를 쓴다(통제 10). `retryable_rows`(실행일 줄별 최신 무인 행이 failed/skipped 이고 방향이 켜진 줄 — skipped_gap 은 그날의 전략 판정이라 제외, 꺼진 방향은 켜면 대상) · `retry_auto_exec` + `POST /portfolio/{pid}/auto-exec/retry`(장중 09:00~15:20 만 · 정지/오늘 취소/기록 없음/대상 없음/실행일 불일치/중복 클릭(Redis 60초 잠금) 409 · 축소 전 계획 수량으로 새 행 `response.retry_of` · 매도 먼저 · `last_run.retry` 요약 · 로그·알림 `autoexec.retry` · 연속 실패 정지 규칙 동일 · 실행 중 플래그로 폴러 양보) · view `retryable` · 상태 detail "재시도 HH:MM — 발주 n건 …" · 주문 행 `retry_of`. 웹 `portfolio/page.tsx`: '무인' 열 헤더 — 실행 뒤 `취소`는 살아 있는 무인 주문이 있을 때만, `재시도`는 완료·대상 줄·장중일 때(확인창 → 결과 한 줄), ⓘ 설명 조건부, 칩 "· 재시도 발주 n", 발주됨 옆 "· 재시도". 문서: ADR-009 §4·영향, feature-portfolio §5/§8/§9/§12, user-guide, auto-execution 운영 5-1. VERSION 0.14.4.
- 테스트 결과: `tests/test_autoexec.py::test_intraday_retry_replaces_failed_and_skipped_lines` 신규(실패 익절 + 주문가능 부족 생략 → 15:30 409 → 10:30 재시도 2줄 발주·이전 행 유지·retry_of·retryable 0·detail·로그 → 대상 없음 409·엔드포인트 409 / 갭 생략 제외·매도 켜면 대상 1 → 발주 / 정지 409). `test_autoexec`·`test_account_autoexec`·`test_autoexec_review`·`test_notify` **20 passed**(첫 실행에서 `test_deposit_fallback…` 1회 실패 → 단독·재실행 2회 모두 통과, 코드 변경 전에도 단독 통과 — 순서 의존 플레이크로 기록). 전체 `pytest -q tests/` **256 passed + 1 실패**(`test_ltm_api::test_legacy_us_ravg_pairs_rejected_and_ltm_job_runs` — 401≠422, 무인과 무관한 인증 플레이크, 단독 재실행 2 passed). `tsc --noEmit` 무오류. 화면 확인은 배포 후(로컬 web 컨테이너 결함).
- Git commit: feat: intraday retry of failed/skipped unattended lines through the shared placement pipeline

## [2026-09-09] fix | 텔레그램 문구 3건 + 챗봇 매매일지 조회 복구 (사용자 보고 스크린샷·"매매일지 검색이 안 된다")

- 분석(스크린샷): 장 마감 동기화 15:45/17:10·일일 현황 16:40 은 설계된 메시지. 다만 ① 17:10 재시도가 변경 없어도 15:45 와 같은 문장을 다시 보냄 ② "체결 1건 조회"는 2일 조회 창(어제~오늘)의 **어제** 체결이 오늘 것처럼 읽힘(오늘 체결 없음 — 무인 주문 2건은 '주문 상태 2건 갱신'으로 미체결 확정) ③ 일일 현황 '전일 대비'가 입출금을 빼지 않아 대시보드(단순 Dietz)와 다름.
- 챗봇: `trading_journal` 도구가 `app.mjournal.journals_overview` 를 import 하는데 PR #86(2026-09-05, 화면 합산 뷰 제거)이 이 함수를 지워 **ImportError → 매매일지 조회 전부 실패**. 챗봇이 "시스템 오류로 조회 불가"라고 답한 원인.
- 작업 내용: `broker.import_fills_for_portfolio` 반환에 `today/prev`(오늘분·어제분 건수) 추가 → `_log_post_close` 문구 "오늘 체결 n건 · 신규 m건 등록 · 어제분 k건 이미 등록(또는 중 x건 새로 등록)". `run_post_close_sync(retry=, only_portfolio_ids=)` + `post_close_changed()` — 17:10(beat `kwargs retry=True`)은 신규 등록·주문 상태 갱신·무인 정지·예수금 경고·오류가 없으면 "재시도 — … 변경 없음(알림 생략)" 로그만(`activity.log_event(notify=False)` 추가). `notify.daily_status_text` 전일 대비 = `dashboard.user_flows_between` 차감, 입출금 있으면 "입출금 ±n원 제외" 표기. 챗봇: `mjournal.journals_overview(user_id, session, q, days, limit)` 복구(일지별 보유·실현손익 + 전 일지 기록 최신순, q = 종목명·코드·일지 이름, days = 최근 N일) + `filter_journal_rows`, `_run_tool` 상세는 series 제거·rows 제한, 도구 설명·운영 지식에 매매일지(trading_journal) vs 실전매매 일자별 기록(portfolio_journal) 구분. 문서: feature-portfolio §5/§8, feature-chatbot, user-guide 알림, operator-guide 배치. VERSION 0.14.5.
- 테스트 결과: 신규 `test_chat_ops::test_trading_journal_tool_overview_search_detail_and_scope`(요약·검색 q/days·상세 limit·타 사용자 스코프), `test_notify::test_daily_status_change_excludes_cash_flows`(입금 1,000,000 → 전일 대비 +0원 · 입출금 표기), `test_cashcheck::test_post_close_message_counts_today_and_quiet_retry`(어제분 1건 문구 · 재시도 무변경 알림 생략 · 경고 시 발송). 전체 `pytest -q tests/` **260 passed**. 작업 중 발견: 챗봇 상세 조회가 시세 보충으로 실 KIS(.env 앱키)에 나가 공유 CI DB 에 005930 일봉을 적재해 `test_mjournal` 평가 테스트를 깨뜨렸다 → 테스트에서 `_kis_for_bars` 스텁, CI DB 의 005930 일봉 삭제로 복구(테스트는 네트워크에 나가면 안 된다 — NOTES 원칙 재확인).
- Git commit: fix: post-close notice counts today's fills and quiets the 17:10 retry; daily status excludes cash flows; restore chatbot trading-journal tool

## [2026-09-09] chore | 챗봇 기본 모델 → openai/gpt-5.6-luna (사용자 지시 "openrouter 에서 조회해서 gpt-5.6-luna 로 변경")

- 조회: OpenRouter `GET /api/v1/models`(431개) 에서 luna 검색 → `openai/gpt-5.6-luna`(tool calling 지원, ctx 1,050,000, $0.20/$1.20 per M) 확정. `-pro`·`:batch` 변형은 제외.
- 작업 내용: `config.openrouter_model` 기본값 교체(종전 anthropic/claude-sonnet-4.5), `.env.example` 에 `OPENROUTER_MODEL` 예시, feature-chatbot 갱신. 서버 `.env` 에 `OPENROUTER_MODEL` 이 설정돼 있으면 그 값이 우선하므로 배포 시 확인. VERSION 0.14.6.
- 테스트 결과: 로컬에 OPENROUTER_API_KEY 가 없어 실호출은 생략(배포 뒤 챗봇 한 번 질문으로 확인). `test_chat`·`test_chat_ops`(업스트림 모킹) **12 passed**.
- Git commit: chore: switch the chatbot default model to openai/gpt-5.6-luna

## [2026-09-09] fix | 스케줄러 상태 파일 이미지 혼입 사고 대응 + 챗봇 매매일지 평가·전일 대비 (사용자 보고 "재시작마다 메시지", "수량·현재가로 계산 못 함")

- 분석(서버 로그): `beat: Starting` 직후 `daily-ingest`(16:05)·`daily-snapshot`(16:40)이 즉시 전송 → 스냅샷 재계산 + 일일 현황 재발송. 새 컨테이너에 낡은 `celerybeat-schedule` 이 있었다는 뜻이고, 즉시 실행된 두 항목이 정확히 9월 5일 이전부터 있던 항목이라 서버 `apps/api/` 에 남은 옛 파일(초기 바인드 마운트 시절)이 `COPY . .` 로 배포마다 이미지에 들어간 것으로 확정(NOTES "Celery beat 상태 파일 이미지 혼입").
- 작업 내용: `apps/api/.dockerignore` 신설 · Dockerfile `mkdir /var/lib/celery` + compose 스케줄러 `beat -s /var/lib/celery/celerybeat-schedule`(코드 밖) · `beat_cron_starting_deadline=3600` · `autoexec.LATE_RUN_LIMIT` 09:30 — `run_auto_execution(late_limit=)` 이 넘겨 도착한 09:01/09:15 실행을 `late` 로 기록(상태 "발주 안 함 — 09:30 지연 상한 초과", 오류 로그, 발주 없음; None 이면 상한 없음) · `notify.send_daily_status` 하루 1회(`notify.daily_status_sent`) · deploy.sh 빌드 전 `apps/api/celerybeat-schedule*` 삭제.
- 챗봇 매매일지: `journals_overview` 가 `enrich_valuation`(증권사 잔고 현재가 → DB 종가 → KIS 보충)과 새 `add_day_change`(`_prev_close_map` 전일 종가 → holdings[].prev_close/day_change/day_change_pct, summary.prev_eval/day_change/day_change_pct)를 붙이고 일지 합계 `totals` 와 수익률 세 종류(실현·평가·하루 변동) 설명 note 를 돌려준다. 도구 설명·운영 지식에 "실현수익률만 보고 평가는 못 한다고 답하지 말 것" 명시. 종전에는 `_compute`(원가 기준)만 넘겨 챗봇이 "종가 데이터가 추가로 필요"라고 답했다.
- 문서: NOTES 사고 기록, operator-guide 배치·상태 파일, ADR-009 §5 지연 상한, feature-portfolio §5, feature-chatbot, user-guide 챗봇. VERSION 0.15.0(마이너 — 챗봇 평가 조회 신설).
- 테스트 결과: 신규 `test_worker_config`(deadline 3600·.dockerignore·/var/lib/celery·retry kwargs), `test_autoexec::test_late_run_limit_blocks_catch_up_execution`(11:00 도착 → late·발주 없음·오류 로그, already-ran, late_limit=None 이면 발주), `test_notify::test_daily_status_sent_once_per_day`, `test_chat_ops::test_trading_journal_overview_includes_valuation_and_day_change`(종가 11,000/전일 10,000 → 평가 110,000·평가손익 20,000·하루 변동 +10,000/+10%·totals). 관련 8파일 59 passed, 전체 `pytest -q tests/` **264 passed**. 로컬 이미지 재빌드 후 스케줄러 재생성: `db -> /var/lib/celery/celerybeat-schedule`, 기동 직후 크론 즉시 전송 없음, `.dockerignore` 이미지 포함 확인.
- Git commit: fix: keep the beat schedule file out of the image, cap cron catch-up and late 09:01 runs, send daily status once; chatbot journal valuation with day change

## [2026-09-09] fix | 반쪽 스냅샷 — 거래 등록 뒤 flush 없이 당일 스냅샷 재계산 (사용자 보고 "챗봇 계산과 대시보드 수익률이 다르다")

- 분석: 서버 복호화 조회로 09-08 M-신한-ETF 스냅샷이 주식 636주(매도 후)·현금은 21:42 등록한 매도 30주×114,000 대금 3,420,000원 누락(매도 전) 상태임을 확인. 대시보드 전일 대비 5,746,150 = 가격 변동 2,326,150(챗봇과 일치) + 3,420,000. 원인은 `autoflush=False` 세션에서 flush 없이 `compute_user_snapshot` 호출 — 로컬 격리 DB 재현으로 입금(cash 0)·매수(stock 0)·매도(대금 누락) 모두 한 거래씩 늦게 반영됨을 확인(NOTES "autoflush=False 세션").
- 작업 내용: `portfolios.register_transaction`·거래 삭제·`cashcheck` 보정 등록에 재계산 직전 `session.flush()`. `app/services/snapshot_repair.py` — `--date`(필수) `--user` `--apply`(기본 dry-run): `signals._state_before` 로 그 날짜까지 체결분 FIFO 로트·현금 재생, 그 날짜 이하 마지막 종가로 평가해 국내 포트 스냅샷·사용자 총액(journal·other 유지) 갱신. 문서: feature-dashboard §5, operator-guide, NOTES. VERSION 0.15.1.
- 서버 조치(배포 뒤): `python -m app.services.snapshot_repair --date 2026-09-08` 로 M-신한-ETF cash 57,688,404 → 61,108,404, 총액 343,048,473 → 346,468,473 확인 후 `--apply`.
- 별건으로 남김(사용자 결정 대기): 매매일지 등록이 전체·전일 수익률에 수익으로 잡히는 문제, 시세 없는 종목의 취득가 평가.
- 테스트 결과: 신규 `tests/test_snapshot_consistency.py` 2건 — 입금·매수·매도·삭제 직후 당일 스냅샷 = 원장(종전 반쪽값 주석), 보정 도구 dry-run 무저장·apply 후 stock/cash/총액·journal 유지·대시보드 전일 대비에서 매도 대금 제거. 관련 4파일 46 passed, 전체 `pytest -q tests/` **266 passed**. CLI dry-run 스모크(격리 DB) 통과.
- Git commit: fix: flush before same-day snapshot recomputation; add as-of snapshot repair tool

## [2026-09-09] feat | 실전매매 요약 — 누적 평가손익(unrealized_total)과 하루 손익(day_change) 필드 분리 (사용자 결정)

- 배경: 챗봇이 실전매매 누적 평가손익(+17,497,632 / +6,638,000)을 "오늘 평가손익" 열에 적었다. 검증 결과 값은 매수 이후 누적이었고(평단 84,888·95,805 로 역산 일치) 오늘 손익은 +1,106,640 / +696,000. 같은 검증에서 "어제 수익률" −0.1111%/−0.0869% 는 도구값(−0.0547%/−0.1743%)과 달라 모델 자체 계산 오류로 판정 — 도구 표 확장·프롬프트 규칙 제안은 사용자가 기각(특정 질문에서만 동작), 필드 분리만 채택.
- 작업 내용: `portfolios.prev_close_before` · `portfolio_summary` 에 `unrealized_total`(= `unrealized_pnl`, 호환 유지), `day_change`·`day_change_pct`(평가 종가일 하루 손익, Σqty×prev_close+현금 대비)·`day_change_asof`, `positions[].prev_close/day_change/day_change_pct`. 챗봇 `portfolio_summary` 결과에 `fields_note`(누적 vs 하루 뜻). 문서 feature-portfolio §8. VERSION 0.15.2.
- 테스트 결과: 신규 `test_portfolios::test_summary_separates_cumulative_unrealized_from_day_change`(누적 17,497,632 · 하루 1,106,640 · % 분모 · positions 필드 · 챗봇 fields_note). 관련 5파일 53 passed, 전체 `pytest -q tests/` **267 passed**.
- Git commit: feat: split cumulative unrealized from day change in the portfolio summary

## [2026-09-09] docs | 초기 진입가 위치(boot_delta) 10년 스윕 검증 (사용자 지시 "전일 종가 근거 부족 — 변수 반복 계산으로 최적값·결론")

- 방법: 실전 플래너와 같은 엔진(`run_backtest`)으로 콜드 스타트 250일 창을 2018-03~2025-08 시작 365개(5거래일 간격) + 준독립 37개×2위상에서 δ·f·N·bear 스윕. 지표에 진입가 품질(K200 첫 매입가·20일 평균 매입가 vs 시작일 종가)·부트 체결률·부호검정·부트스트랩 CI 추가. 스크립트 `apps/api/scripts/boot_delta_sweep.py`(stage 1~5).
- 결과: δ ∈ [−0.5, 1.0] 어느 값도 250일 수익을 유의하게 바꾸지 않음(CI 모두 0 포함, 표본 위상별 부호 반전). δ 는 체결률(92→25%)·속도·꼬리를 맞바꾸는 변수. 종가(δ0)는 체결률 75%·5일 내 미체결 0·첫 매입가 = 시작일 종가·꼬리 중간. 그리드만 기다리는 기준은 44% 창이 5일 내 미체결이고 20일 평균 매입가가 +0.22% 더 비쌈. 측정 가능한 개선은 N10→5·bear 0.5→0.25(p10 −1.06→−0.50%, 최악 −2.98→−1.98%, 우세 162→172, 세 표본 동일 방향). bear0 은 유일하게 평균 유의(+0.09%)지만 하락장 시작에 매매가 없어 기각.
- 결론: `boot_delta` 0 유지 권고, `boot_days` 5·`boot_bear_mult` 0.25 는 선택적 조정(설정 변수, 코드 변경 없음). 문서 `docs/boot-entry-price-study-20260909.md`. 코드 변경 없음(버전 유지 0.15.2).
- 추록(같은 날, 사용자 제안 "이동평균선 참고 진입 + 아주 소량", "20·60일은 예시 — 다양하게"): 플래너 연구용 옵션 `boot_price_ref`(ma{n}/ma{n}min)·`boot_ma_filter`(n) 추가(기본값 현행 동일, 설정 미노출) → 스윕 stage 6, MA 5·10·20·60·120·200 × 지정가/필터 + 소량 f.10/.05/.02·N20/30 27종. 결과: 모든 MA 변형이 체결률(38~60% vs 75%)·t50(7~10 vs 5일)·첫 매입가(+0.02~+0.33% vs 0.00%)에서 뒤지고 MA120·200 은 Δ 평균 −0.10%p 유의. 소량은 효과·위험이 비례 축소될 뿐(f.02 ≈ 없음). **채택 안 함**, 문서 §5. 전체 267 passed. VERSION 0.15.3(플래너 코드 변경, 기본 동작 불변).
- 추록 2(같은 날, 사용자 지시 "시가로 들어가는 전략 시뮬레이션"): 연구용 `boot_otype="market"`·`boot_market_slippage` — 플래너는 price None 시장가 boot(수량 종가 환산), 엔진(`backtest.py` ②)은 K200 시장가 boot 를 시가(+가산율)로 체결·갭 필터 적용·그리드 회계 로트. stage 7: 체결률 99%·t50 4일(N5 면 3일)로 가장 빠르나 Δ 중앙 −0.10%(현행 −0.05%)·평균 +0.07%(현행 +0.03%)·최악 −3.45%(현행 −2.98%), 0.1% 가산 시 평균 우위 소멸·최악 −3.65%. 시가+N5+bear0.25 는 종가 N5 bear0.25 와 꼬리 비슷·진입 2일 빠름·중앙/우세/매입가는 종가가 우위. **기본값 변경 근거 없음**, 문서 §7. `test_bootstrap_entry` 시장가 변형 테스트 추가.
- Git commit: docs: boot entry-price position study — 10-year cold-start sweep on the real planner · research: MA-reference boot options + stage 6 · research: market-open boot option + stage 7
## [2026-09-09] fix | 실시간 그래프가 비어 보이는 문제 (사용자 지적 "이건 왜 자꾸 안 나오는거야")

- 원인: `LiveChart` 가 주문선(lines JSON)·터치 상태가 바뀔 때마다 차트를 지우고 다시 만들었는데, 데이터를 넣는 효과는 `pts` 변화에만 반응했다. 페이지 로드 직후 주문 상태(bo)가 늦게 도착해 lines 의 `status` 가 바뀌면 차트가 재생성되고 다음 10초 틱이 올 때까지 빈 차트(축 눈금도 없음)로 남는다. 장 마감 뒤에는 틱이 없어 계속 빈 화면 — 스크린샷(15:59 마지막 표본, 거리 패널은 정상)과 일치.
- 작업 내용: 차트 생성은 code 당 한 번(`[code, name]`), 생성 직후 이미 받은 데이터를 즉시 반영. 주문선은 별도 효과에서 `removePriceLine` → `createPriceLine` 으로 제자리 갱신(터치 시 실선·✓ 라벨 포함). 마커·축척 provider 는 그대로. 문서 feature-portfolio §5. VERSION 0.15.4.
- 테스트 결과: `tsc --noEmit` 무오류(웹만 변경). 화면 확인은 배포 후 — 주문표 아래 카드에 축 눈금과 선이 바로 보여야 정상.
- Git commit: fix: keep the live chart instance across order-line updates so it never renders empty

## [2026-09-10] test | 오늘 봉을 심는 테스트 4건이 오전(15:30 전)에 확정봉 가드에 걸려 실패 — 심는 순간만 장 마감 상태로

- 증상: 08:00 KST 전체 스위트 264 passed / 4 failed — `test_chat_ops::test_trading_journal_overview…`, `test_portfolios::test_summary_separates…`, `test_snapshot_consistency` 2건. 모두 `upsert_daily_bars` 로 **오늘 날짜** 봉을 심고 그 종가로 평가를 검증하는데, 0.9.4 의 확정봉 가드(`bar_is_final` — 오늘 봉은 KR 15:30 뒤에만 허용)가 오전에는 그 봉을 거부해 전일 종가로 평가됐다. 09-09 저녁(15:30 뒤) 실행에서는 통과해 발견되지 않았다.
- 작업 내용: 네 곳의 봉 심기를 `unittest.mock.patch("app.services.ingest.market_session_state", return_value=(today, True))` 로 감싸 심는 순간만 장 마감으로 본다(가드 자체 테스트는 그대로). 코드 변경 없음.
- 테스트 결과: 4건 통과 후 전체 `pytest -q tests/` → **268 passed** (08:00 KST, 오전 실행).
- Git commit: test: seed today's bars under a closed-market patch so valuation tests pass before 15:30 KST


## [2026-09-10] feat | 실시간 시세(10초) 반영 + 주문표 줄별 취소 버튼 (사용자 지시)

- 배경: 종목 카드의 "현재가 112,400원"이 실은 전일 종가였다(오늘 봉은 15:30 뒤에만 적재 — 확정봉 가드). 검토 결과 10초 실시간은 주문표 아래 그래프뿐이었고, 실전매매 요약·대시보드 총자산은 일봉 종가, 매매일지는 증권사 잔고(120초 캐시)라 **한 화면 안에서 기준 시각이 섞여** 있었다. 사용자 지시로 전부 10초 실시간으로 통일.
- 작업 내용: `quotes.live_quotes(codes)` — 워커 10초 폴링이 남긴 `quotes:last:{code}`(TTL 300)를 MGET 으로 읽는다(KIS 호출 없음, Redis 장애·장외면 빈 dict). `portfolio_summary` 가 실시간 우선·종가 폴백으로 평가하고 `price_source`·`live_at`·`live_count`·`delayed` 를 돌려준다. 하루 변동 기준일을 '평가에 쓴 종가일' → **오늘**로 고정해 장중 실시간과 장 마감 종가가 같은 뜻이 되게 했다. `mjournal.enrich_valuation` 은 실시간 → 증권사 잔고 → 종가 순, `summary.live_at` 추가. 대시보드는 `live_kr_stock()` 로 **표시 전용** 덮어쓰기(적재 스냅샷은 종가 유지 — 2026-09-09 반쪽 스냅샷 사고 재발 방지)하고, 10초 주기 호출용 `GET /dashboard/live`(방문 기록·적재 없음)를 신설. 웹 3화면(실전매매·대시보드·매매일지)이 `live_at` 이 있을 때만 10초 타이머를 돌리고 탭이 숨겨지면 건너뛴다 + `● 실시간 HH:MM` 배지. 주문표 '무인' 열의 `발주됨`·`일부 체결` 줄에 **취소** 링크(기존 취소 엔드포인트 재사용, 확인창, 거부 시 사유 표시 후 상태 갱신). 테스트 오염 방지로 conftest 에 `_no_live_quotes` autouse 픽스처. VERSION 0.16.0(마이너 — 새 동작·새 엔드포인트).
- 테스트 결과: 신규 `tests/test_live_prices.py` 4건 — 캐시 파싱(없음·0원·깨진 JSON·중복 코드) / 요약 실시간 우선·종가 폴백·하루 변동·`live_count` / `/dashboard/live` 덮어쓰기와 **적재 스냅샷은 종가 유지** / 매매일지 실시간 평가. 전체 `pytest -q tests/` **272 passed**, `tsc --noEmit` 무오류. 작업 중 발견해 함께 고침: ① `live_quotes` 가 호출마다 Redis 연결을 새로 열어 소켓이 새던 것 → 모듈 단위 풀 재사용(10초 주기 호출이라 치명적) ② `test_post_close_sync_records_cash_check` 가 공유 CI DB 의 전 포트를 돌아 파일 디스크립터가 고갈되던 것 → `only_portfolio_ids` 로 자기 포트만.
- Git commit: feat: real-time (10s) prices across portfolio, journal and dashboard; per-line order cancel

## [2026-09-10] fix+feat | 체결된 주문의 취소 차단 · 줄별 재등록 · 새 실전매매 화면의 연동 카드 (사용자 지적 3건)

- 원인(취소): 주문 상태는 15:45 동기화가 확정하므로 장중에 체결돼도 화면은 계속 '발주됨' 이었고, 그래서 **이미 체결된 줄에 취소 버튼이 남았다**(스크린샷: 초기 진입 17주가 체결돼 원장에 들어왔는데 주문표는 발주됨). 서버는 KIS 가 거부해 사고로 이어지지 않았지만 화면이 사실과 달랐다.
- 작업 내용: ① 취소 직전 `sync_auto_orders` 로 당일 체결조회를 돌려 상태를 맞추고, 전량 체결이면 증권사에 요청을 보내지 않고 409 + `order.cancel` 경고 로그. 부분 체결은 잔량 취소하고 "체결 n주는 유지" 표기. 화면은 장중에 주문표를 `refresh=1` 로 받고(로드 시 + 살아 있는 무인 주문이 있으면 30초마다, 탭 숨김 시 건너뜀) 상태를 최신으로 유지. ② 줄별 **재등록** — `POST /portfolio/{pid}/orders/{oid}/reorder`, `retryable_rows`·`retry_auto_exec` 에 `statuses`·`only_lines` 를 더해 재사용(`REORDERABLE = failed·skipped·cancelled`). 취소로 풀린 현금은 매수가능조회로 자동 반영. ③ 새 실전매매 패널이 열려 있으면 기존 포트의 증권사 연동·이름·색 카드를 감추고, 패널 제목에 "증권사 계좌는 아래에서 고릅니다" 안내 — 잠긴 '연결됨' 이 새 포트의 계좌 선택처럼 보여 "새 계좌를 연결할 수 없다"는 오해를 낳았다. VERSION 0.17.0(마이너 — 새 엔드포인트·새 버튼).
- 테스트 결과: 신규 `test_autoexec::test_cancel_refuses_filled_order_and_reorder_replaces_cancelled_line` — 전량 체결 취소 409·증권사 요청 없음·상태 자동 교정·경고 로그 / 미체결 취소 성공 / 체결된 줄 재등록 409 / 취소된 줄만 재등록(새 행·`retry_of`·다른 줄 불변·로그). 전체 `pytest -q tests/` **273 passed**, `tsc --noEmit` 무오류.
- Git commit: fix: never cancel a filled order; add per-line reorder and hide stale broker card on new-portfolio panel

## [2026-09-10] feat | 실전매매 직접 주문 (ADR-011) — PR 1/2 (사용자 지시 "api 로 주문 발행·취소, 무인과 다른 아이콘")

- 배경: ADR-009 가 발주 경로를 HTS 직접·09:01 무인 둘로 못박아 주문 버튼을 모두 없앴지만, KIS 클라이언트에는 정규 주문·취소·매수가능조회가 이미 있었다(09:01 실행이 사용). 무인이 실패·생략한 줄, 주문표에 없는 종목, 급한 청산은 HTS 로 가야 했고 그 주문은 앱이 알지 못해 미체결 확인·취소가 불가능했다. ADR-011 로 발주 경로에 '앱의 직접 주문' 을 더한다.
- 작업 내용: `POST /portfolio/{pid}/orders/manual` — `BrokerOrder(mode="manual")` 로 기록해 취소·체결 확정·표시가 무인과 같은 경로(취소 분기·`sync_auto_orders` 를 manual 까지 확장, 예약주문 동기화 대상에서는 제외). 통제 6가지: 연결 계좌만 · 장중 09:00~15:20 · 매수는 매수가능조회·매도는 잔고로 사전 검증 후 **초과면 거부**(무인의 '축소' 와 달리 사용자가 낸 수량을 바꾸지 않는다) · 같은 주문 5초 Redis 잠금 · `order.manual` 로그·알림(실패도 행으로 남김). 웹: 주문표 아래 '직접 주문 · 오늘 낸 주문' 카드(주문 폼 + 확인창에 계좌·실전/모의·시장가 취소 불가 경고, 그날 주문 전체를 경로 배지 🤖 무인 / 👤 직접 / 예약 로 나열하고 줄마다 취소), 주문표 '무인' 열은 직접 주문을 `👤 직접 발주됨` 으로 구분. VERSION 0.18.0.
- 테스트 결과: 신규 `test_autoexec::test_manual_order_places_cancels_and_shows_with_auto` — 장 마감 뒤 409 / 매수가능 초과 거부(주문 미발송) / 잔고 초과 매도 거부 / 정상 접수(mode=manual·주문번호·로그) / 목록 표시 / 전량 체결 시 취소 409·미체결 취소 성공. 전체 `pytest -q tests/` **274 passed**, `tsc --noEmit` 무오류.
- 남은 것: 매매일지 직접 주문 — `broker_orders` 에 `journal_id` 추가·`portfolio_id` nullable 마이그레이션(사용자 결정 A안)으로 **PR 2** 에서.
- Git commit: feat: place KIS orders directly from the app (ADR-011) — portfolio side

## [2026-09-10] feat | 매매일지 직접 주문 (ADR-011) — PR 2/2, 같은 PR 에 이어 커밋 (사용자 결정 A안)

- 배경: 매매일지의 **계좌 연동·체결 가져오기·잔고 기초 보유 등록은 이미 있었고**(0018), 없던 것은 주문 발행뿐이었다. 실전매매와 같은 코드로 묶기 위해 `broker_orders` 를 두 주인이 공유하게 한다(사용자 결정 A안).
- 작업 내용: 마이그레이션 **0026** — `broker_orders.journal_id` 추가(FK CASCADE), `portfolio_id` nullable, 소유자 CHECK(`portfolio_id IS NOT NULL OR journal_id IS NOT NULL`), `(journal_id, plan_date)` 인덱스. 주문 본체를 `place_manual_kis_order()`, 취소를 `cancel_regular_order()` 로 공용화해 실전 포트·매매일지가 같은 안전장치를 쓴다(장중·매수가능/잔고 사전 검증·중복 잠금·취소 전 체결 재확인). 매매일지 API 3종(`GET /mjournals/{jid}/orders`, `POST …/orders/manual`, `POST …/orders/{oid}/cancel`). 웹: 매매일지 '직접 주문' 카드(주문 폼 + 그날 주문 목록 + 취소, 살아 있는 주문이 있으면 장중 30초 상태 갱신).
- 테스트 결과: 신규 `test_mjournal::test_journal_manual_order_place_list_and_cancel` — 계좌 미연결 거부 / 장 마감 뒤 409 / 잔고 초과 매도 거부(주문 미발송) / 정상 접수(journal_id 기록·portfolio_id None) / 목록·연결 계좌 / 전량 체결 취소 409·미체결 취소 성공 / 다른 일지와 격리. 전체 `pytest -q tests/` **275 passed**, `tsc --noEmit` 무오류.
- Git commit: feat: journal-side manual orders sharing broker_orders (migration 0026)

## [2026-09-10] refactor | 거래 입력을 한 섹션으로 — 주문유형 탭 (사용자 지시 "체결 수동등록과 증권사 주문을 나누지 말고 한곳에서")

- 배경: 같은 화면에 '체결·입출금 등록'(장부 기록)과 '직접 주문'(실제 발주)이 따로 있어 어디에 무엇을 넣는지 헷갈렸다. 두 폼은 방향·종목·수량이 같고 가격의 뜻(단가 vs 지정가)과 일자·이유 칸만 달랐다.
- 작업 내용: **실전매매** — 주문표 안에 접혀 있던 `<details id="fill-entry">` 를 걷어내고 주문표 아래에 '거래 입력' 카드 신설, 탭 3개(사용자 결정): `✍️ 체결 기록`(매수/매도 토글 + 종목·수량·단가·메모) · `💰 입출금`(입금/출금 토글 + 금액) · `👤 증권사 주문`(종목코드·방향·수량·지정가, 경고색 테두리와 붉은 버튼, 계좌 미연결·국내 아님이면 비활성). 탭 전환이 `form.kind` 를 맞춘다. 주문표 줄의 '체결 등록' 은 체결 탭을 열고 값을 채운다(`entryOpen` 상태 제거). '오늘 낸 주문' 목록을 같은 카드 안으로. **매매일지** — '오늘 입력' 카드를 `JournalTradeCard` 로 바꿔 탭 2개(기록만 · 증권사 주문), 기록 폼은 `recordForm` 프롭으로 그대로 넘겨 재사용, 낸 주문 목록도 같은 카드에. 서버 변경 없음. VERSION 0.19.0.
- 함께 고침: 실전매매 체결 폼의 단가 라벨이 `wontouch` 로 깨져 있던 것(d60ecd9 의 일괄 치환 흔적)을 `단가({unit})` 로 복구.
- 테스트 결과: 서버 변경이 없어 기존 스위트 그대로 — `pytest -q tests/` **275 passed**(`test_ws_quotes` 1건은 알려진 간헐 실패, 단독 재실행 2 passed), `tsc --noEmit` 무오류. 화면 확인은 배포 후.
- Git commit: refactor: one trade-entry section with order-type tabs (portfolio 3 tabs, journal 2)

## [2026-09-10] fix | 입력 누락을 칸에 붉게 — 모든 폼 공통 (사용자 지적 "주문 넣기가 안 먹는데 뭘 빠뜨렸는지 모른다")

- 원인 2가지: ① 직접 주문의 검증 문구를 `boMsg` 로 넣었는데 그 자리가 주문표 위쪽이라 거래 입력 카드를 보는 사용자 눈에 들어오지 않았다. ② 여러 폼이 **버튼을 조용히 비활성**으로 두어(매매일지 기록·새 일지, 설정 계좌 등록·조회) 왜 안 눌리는지 알 수 없었다. 실전매매 체결·입출금 폼은 검증 자체가 없어 서버 422 문구만 떴다.
- 작업 내용: 공통 훅 `apps/web/lib/form.tsx` `useFieldErrors()` 신설 — `validate({칸: 사유|""})` 로 표시를 켜고 사유 요약을 돌려주며 `cls`(붉은 테두리)·`msg`(칸 아래 사유)·`clear`(입력 시 해제)·`has`. 규칙: **비활성 대신 눌러 보면 알려 준다**. 적용: 실전매매 거래 입력 3탭(체결·입출금·증권사 주문, 메시지도 카드 안으로) · 매매일지 기록 폼·주문 폼·새 일지 · 설정 증권사 계좌 등록/조회(`CredentialInput` 에 `invalid` 지원)·알림 봇 토큰 · 시뮬레이터 기간·자본금 · 로그인 아이디·비밀번호. 서버 변경 없음. VERSION 0.19.1.
- 테스트 결과: 서버 변경 없음 — `pytest -q tests/` **275 passed**, `tsc --noEmit` 무오류. 화면 확인은 배포 후.
- Git commit: fix: show which field is missing on every form (shared useFieldErrors)

## [2026-09-10] fix | 모바일 잘림 3건 (사용자 지시 "메뉴별로 모바일 출력 분석")

- 점검 방법: 로컬 web 컨테이너 결함으로 화면을 볼 수 없어 반응형 클래스·레이아웃 구조를 코드로 훑었다. 기반은 정상 — 사이드바는 `lg` 미만에서 서랍으로, 차트는 자동 크기, 표는 대부분 가로 스크롤 래퍼 안. 문제는 **본문 컨테이너가 넘침을 잘라내는데**(`shell.tsx` `overflow-x-clip`, 툴팁이 페이지를 밀지 않게 한 조치) 스크롤 없는 요소가 넘치면 보이지도 눌리지도 않는다는 점.
- 고친 것 ①: `CardTitle` 의 오른쪽 액션이 `shrink-0 whitespace-nowrap` 이라 360px 에서 잘렸다(매매일지 청산·연동·삭제 3버튼 ≈280px, 실전매매 무인 칩 ≈285px) → 제목 줄을 `flex-wrap` 으로 바꿔 좁으면 아랫줄로 내려가고, 오른쪽 안에서도 `whitespace-normal sm:whitespace-nowrap` 으로 줄바꿈. 매매일지 헤더 버튼 묶음에도 `flex-wrap`.
- 고친 것 ②: 2026-09-10 에 넣은 거래 입력 탭이 라벨+설명이라 3개 합계 ≈430px 로 넘쳤다 → 설명은 `hidden sm:inline`, 탭 묶음에 `flex-wrap`. 매매일지 2탭도 동일.
- 고친 것 ③: 시뮬레이터 주문 표(`simulator/page.tsx`)만 스크롤 래퍼가 없어 칸이 눌렸다 → `overflow-x-auto` 래퍼.
- 남긴 것(경미): 반응형 접두 없는 `grid-cols-2` 4곳(설정 상품코드·환경, 새 일지 요율, 시뮬레이터 옵션, 거리 패널) — 입력은 되고 답답한 정도. 2버튼 토글·마켓 스위치는 좁아도 들어간다.
- 테스트 결과: 서버 변경 없음 — `pytest -q tests/` **275 passed**, `tsc --noEmit` 무오류. 재점검 스크립트로 스크롤 래퍼 없는 표 0건 확인(남은 3건은 래퍼가 4~5줄 위인 오탐). 실제 화면 확인은 배포 후.
- Git commit: fix: stop mobile clipping — wrap card-title actions, collapse tab hints, scroll the simulator table

## [2026-09-10] fix | 주문 취소의 체결 재확인이 다른 시계를 쓰던 것 — 15:30 이후 테스트 3건 실패

- 증상: 15:43 KST 전체 스위트에서 `test_autoexec::test_cancel_refuses_filled_order_and_reorder_replaces_cancelled_line`·`test_manual_order_places_cancels_and_shows_with_auto`·`test_mjournal::test_journal_manual_order_place_list_and_cancel` 3건이 취소 200 을 기대한 자리에서 409 를 받았다. 같은 코드가 오전(13~14시) 실행에서는 통과했다.
- 원인: `cancel_regular_order` 가 취소 직전 `sync_auto_orders` 로 체결을 재확인하는데 그 함수가 인자 없이 실제 시각을 읽는다. 15:30 이 지나면 체결 없는 주문을 `unfilled` 로 확정하므로 취소가 "취소할 수 없는 상태" 409 가 된다. **운영 동작은 옳다**(장 마감 후에는 취소할 것이 없다). 문제는 접수 창(`MANUAL_WINDOW`, `app.broker.datetime`)과 체결 재확인이 서로 다른 시계를 봐서, 테스트가 접수만 고정하고 취소는 실제 시각으로 돌던 것.
- 작업 내용: `cancel_regular_order` 가 `sync_auto_orders(..., now=datetime.now(KST))` 로 **이 모듈의 시계 하나**를 넘긴다(동작 불변, 출처만 통일). 테스트 3곳에 취소용 헬퍼(`cancel(oid, at=(10, 0))`)를 두어 접수와 같은 방식으로 장중 시각을 고정.
- 테스트 결과: 3건 통과 후 전체 `pytest -q tests/` → **275 passed** (15:45 KST, 장 마감 후 실행). `tsc --noEmit` 무오류(웹 변경 없음).
- 특이사항: 같은 유형(시계 의존 테스트)이 오늘 아침에도 있었다 — 확정봉 가드로 오늘 봉 평가 테스트 4건이 15:30 전에 실패(#178). 시각에 걸리는 규칙이 늘어난 만큼 새 테스트는 시계를 고정하는 것을 기본으로 한다.
- Git commit: fix: cancel path re-checks fills on one clock so tests are not wall-clock dependent

## [2026-09-10] feat | KRX 애프터마켓(9/14) 대응 — 저녁 배치 3종: 주식 일봉 20:10 · 동기화 20:15 · 스냅샷 20:20(조건부 알림)

- 검토(사용자 질문 "9/14 시장 시간 변경에 대응돼 있나"): 기사·기존 문서 재확인 — 정규장 09:00~15:30 불변, 애프터마켓 16:00~20:00 신설(주식·DR 만, **ETF·ETN 제외**), 전일 종가 ±30%, T+2 유지. 따라서 **매매 공식·09:01 무인 실행은 영향 없음**(우리 한국 전략은 전부 ETF, 신호 앵커는 KRX 정규장 종가). 남는 구멍은 매매일지에 담은 **주식** 쪽 3가지 — ① 16:00~20:00 체결이 그날 동기화에 안 잡힘 ② 16:05 에 받은 주식 일봉이 저녁 거래를 놓친 채 굳음(ON CONFLICT DO NOTHING) ③ 16:40 스냅샷이 애프터마켓 한복판 값.
- 사용자 결정: "주문표 생성을 21:00 으로 옮기는 것"은 입력(ETF 종가)이 같아 이득 없음을 확인하고 기각. 대신 "장 마감 후 1회 보내고, 애프터마켓이 끝나면 **추가 거래가 있을 경우에만** 한 번 더". 가격만 움직인 경우는 보내지 않는다(임계값 알림은 후속 후보).
- 작업 내용: (1) `ingest.market_session_state(market, type_)`·`bar_is_final(..., type_)` — 마감 시각을 종목 종류로 분기(국내 ETF·ETN 15:30 / 국내 주식 `KR_STOCK_CLOSE` 20:00 / 미국 16:00 ET). `upsert_daily_bars` 가 `Instrument.type` 을 넘긴다. 종류 미지정 호출(주문표 `_plan_pending` 등)은 정규장 기준 그대로. (2) `worker.ingest_targets(session, scope)` + `daily_ingest(scope=)` — 16:05 `regular`(국내 ETF·미국), **20:10 `stocks`**(국내 주식). (3) **20:15** `broker_post_close_sync(retry=True)` — 저녁 체결 가져오기, 변경 없으면 알림 없음(17:10 규칙 재사용). (4) **20:20** 신규 `evening_asset_snapshot` — 전 사용자 스냅샷 재계산(같은 키 덮어쓰기 → 그날 최종값)하고 `users_with_evening_fills`(실전 원장 `TradeTransaction.created_at ≥ 16:45` + 매매일지 항목)에 해당하는 사용자에게만 일일 현황 재발송. (5) `notify.send_daily_status(phase=)` — 단계별 잠금(`daily_status_sent`/`daily_status_after`), 저녁 제목 "🌙 애프터마켓 마감 반영".
- 테스트 결과: 신규 `tests/test_after_market.py` 4건 — 종류별 확정 시각(15:29/15:30/16:05/19:59/20:00/20:10 × ETF·주식·미지정, 과거·미래) / 장중 주식 봉 거부 후 20:00 뒤 값이 자리 차지 / `ingest_targets` 3범위 분리 / 저녁 스냅샷·조건부 발송(입금은 대상 아님·체결 등록 후 대상·최종값 반영·단계별 하루 1회). 기존 `test_validators` 스텁 시그니처 갱신. 전체 `pytest -q tests/` → **279 passed**. 로컬 워커·스케줄러 재기동 후 beat 4항목(16:05 regular·20:10 stocks·20:15 evening·20:20 snapshot) 확인.
- 문서: 9/14 검토 문서 §3-5(조치 표), operator-guide 저녁 블록, user-guide 알림 절, feature-market-data 확정봉 절, ASSUMPTIONS 2건.
- 남은 것: 9/14 첫 주에 ① 주식 일봉이 저녁에 실제로 갱신되는지(16:05 vs 20:10 값 비교) ② 예약주문 접수 창 변경 여부 ③ KIS 가 애프터마켓 주문 TR 을 지원하는지(직접 주문 창 09:00~15:20 확장 여부) 확인.
- Git commit: feat: evening batches for the KRX after-market — stock bars 20:10, sync 20:15, snapshot 20:20 with conditional daily status

## [2026-09-10] feat | 계좌별 현황·매매일지 자산에 '오늘 손익' 열 — 누적과 분모를 나눠 함께 표기 (사용자 지시)

- 지시: "계좌와 매매일지 현황에 누적 수익율/금액과 오늘 손익율/금액을 표기하도록 검토" → 검토(두 표 모두 누적만 있었고, 필요한 계산은 9/9 `portfolio_summary.day_change`·9/9 `add_day_change` 에 이미 있었음 — 표만 연결하면 됨) → "제안 사항을 모두 구현".
- 작업 내용: (1) `dashboard.dashboard()` 계좌 행 — `_live_price_overrides`(10초 폴링 캐시 → {instrument_id: 현재가})와 `_prev_close_map_by_id`(오늘 이전 마지막 종가)를 더해 `day_change`·`day_change_pct`·`day_missing`·`price_source` 를 돌려주고, 평가액·누적도 같은 현재가로 계산해 총자산 카드와 기준을 맞췄다(적재 스냅샷은 종가 유지 — 2026-09-09 반쪽 스냅샷 사고 규칙 불변). 셀 보유가 없으면 `day_change=null`(화면 '—'). (2) `mjournal.journal_assets` — `add_day_change` 를 붙이고 **총자산에 넣는 종목만** 합산해 `day_change`·`day_change_pct`·`day_missing` 추가. (3) 웹 대시보드 두 표에 '오늘 손익' 열(≥sm)과 좁은 화면용 보조 줄, 열 제목 ⓘ 에 분모 설명(누적=원가, 오늘=전일 종가 평가액)과 제외 종목 안내, 공용 `dayCell`·`dayTone`.
- 테스트 결과: 신규 `tests/test_day_change_rows.py` 3건 — 계좌 행(누적 원가 대비·오늘 전일 종가 대비·오늘 산 종목은 `day_missing` 으로 제외) / 실시간 캐시가 있으면 그 값으로 평가·오늘 손익(`price_source=live`) / 매매일지 행(포함 종목만·전일 종가 없는 종목 제외). 전체 `pytest -q tests/` → **282 passed**, `tsc --noEmit` 무오류. 화면 확인: 계좌 행은 현금만이라 '—', 일지 행은 한투-삼성 누적 +1,438,800(94.6%)·오늘 −5,500(−0.19%), 연금저축 누적 −15,450(−1.0%)·오늘 −2,065(−0.14%).
- 특이사항: 재기동 직후 잔고 가격 캐시가 비어 있던 순간에 일지 평가액이 한 번 낮게 찍혔다(스크린샷). 캐시가 채워진 뒤 정상값으로 돌아왔고 API 값도 정합 — 계산 경로 문제는 아니다.
- 문서: feature-dashboard §5.
- Git commit: feat: show cumulative and today's P&L side by side on the dashboard account and journal tables

## [2026-09-10] ui | 대시보드 상단 카드 — 총자산은 오늘·누적 두 줄로, 시장 카드에 오늘 손익 추가 (사용자 지시)

- 지시: "이 부분도 오늘, 누적이 필요할 것 같다" → 검토(총자산 카드는 오늘만 있고 누적은 %만, 시장 카드는 누적만) → "총자산 카드에서 '실전매매 …·매매일지 …' 줄은 빼고 오늘과 누적 비율·금액을 넣는 방안을 검토" → "제안 사항 모두 구현".
- 작업 내용: (1) `dashboard()` 가 `since_inception_amount`(최초 스냅샷 대비 증가액, 입출금 차감 — 종전엔 % 만) 를 함께 준다. (2) `_market_breakdown` 이 `day_change`·`day_change_pct` 추가 — 계좌 표와 같은 규칙(현재가는 10초 캐시 우선·없으면 종가, 기준은 오늘 이전 마지막 종가, 전일 종가 없는 종목 제외). (3) 웹: 총자산 히어로 카드 보조를 "▲/▼ 오늘 금액(%)" + "▲/▼ 누적 금액(%)" 두 줄로 바꾸고 실전매매·매매일지 구성 줄 제거(같은 정보가 자산 구성 카드·계좌별 표에 있음), 한국·미국 주식 카드에 "누적 …" 아래 "오늘 …" 줄 추가, 세 카드 라벨에 ⓘ — **두 '누적'의 기준 차이**(총자산=최초 스냅샷 대비, 시장=보유 원가 대비)를 명시. 기록이 하루뿐이면 누적은 "기록 이틀째부터".
- 테스트 결과: `test_day_change_rows` 에 상단 카드 검증 1건 추가(시장 카드 누적·오늘 각각의 분모, `since_inception_amount` = 총자산 − 최초 스냅샷). 전체 `pytest -q tests/` → **283 passed**, `tsc --noEmit` 무오류. 화면 확인: 히어로 카드가 "▼ 오늘 7,565원 (-0.02%) / ▼ 누적 88,414,115원 (-70.25%)" 두 줄, 구성 줄 사라짐.
- 문서: feature-dashboard §5.
- Git commit: ui: total-assets card shows today and cumulative; market cards gain today's P&L

## [2026-09-10] ui | 한국·미국 주식 카드도 오늘이 위, 누적이 아래 (사용자 지시)

- 지시: "한국 주식도 오늘이 위에 누적이 아래에 위치하도록". 총자산 카드는 이미 오늘 → 누적 순서인데 시장 카드만 반대였다.
- 작업 내용: `dashboard/page.tsx` 두 카드의 보조 줄 순서를 바꿨다(값·색·툴팁 불변, 표시 순서만). 웹만 변경.
- 테스트 결과: `tsc --noEmit` 무오류, 서버 변경 없음(직전 283 passed 유지). 로컬 dev 계정은 국내 주식 보유가 0 이라 카드 보조 줄이 렌더되지 않아 눈으로는 서버에서 확인한다.
- 버전: **올리지 않음** — 같은 화면 작업의 후속 표시 변경이라 v0.22.0 에 포함해 배포한다 (2026-09-10 사용자 지적 "버전이 이렇게 막 올라갈 일인가" 이후 규칙: 버전은 배포 단위로만, 테스트·문서·후속 표시 수정은 올리지 않는다).
- Git commit: ui: market cards list today above cumulative, matching the total card

## [2026-09-10] feat | 자산 추이 — 계열 선택 표기와 매매일지 선 (사용자 지시)

- 지시: (1) 그래프에서 선택한 항목(총자산·J-한투-ISA 등)을 그래프에 표기, (2) 자산 추이에 매매일지 자산 추가.
- 검토: 매매일지는 **일지 단위 스냅샷이 없다**. `asset_snapshots.journal`(사용자 합계)만 남아 있어 실전매매처럼 일지별 다선은 지금 그릴 수 없고, 합계 한 줄이 가능한 최대다. 일지별 선을 원하면 포트 스냅샷과 같은 테이블·배치가 필요하고 과거는 소급 복원되지 않는다(후속 과제).
- 작업 내용
  - 서버 `GET /portfolio/trend`: 포트 계열에 `kind="portfolio"` 를 붙이고, 매매일지 합계 계열(`kind="journal"`, KRW)을 추가했다. `journal IS NULL`(0020 이전, 집계가 없던 날)은 0 으로 찍으면 없던 급락이 생기므로 점을 만들지 않는다. 일지가 없어 매일 0 인 사용자에게는 계열 자체를 넣지 않는다. ALL 구간은 포트 계열과 같은 주 단위 샘플을 적용한다.
  - 웹: 매매일지는 회청색(#475569) **파선** 한 줄. 범례를 버튼으로 바꿔 누르면 그 계열만 진하게(선 굵기 3, 이름·가격선·마지막 값 표기) 남기고 나머지는 알파 0.2 로 흐리게 한다. 다시 누르면 전체 보기. 계열 이름표(title)를 상시 켜 두면 가격축에 4개가 겹쳐 붙어, 고른 계열에만 붙인다.
  - 웹: 크로스헤어 읽기 추가 — 커서 날짜의 계열별 금액을 금액 내림차순으로 한 상자에 띄운다. 차트 오른쪽 절반에서는 왼쪽으로 뒤집고, 긴 이름은 말줄임해 금액 줄이 접히지 않게 했다.
- 테스트 결과: `tests/test_trend_journal.py` 3건 신규(매매일지 계열·NULL 날짜 제외·일지 없으면 계열 없음·포트 kind), **286 passed**. `tsc --noEmit` 무오류. 헤드리스 확인 — 범례 4개(총자산·일지 계좌·한투-자금운영1·매매일지), 매매일지 선택 시 `매매일지 428만` 이름표만 표기, 호버 상자에 4개 계열 금액(2026-09-07 기준 37,462,580 / 30,000,000 / 4,462,680 / 2,999,900원), 콘솔 오류 없음.
- 확인한 데이터: 개발 DB 사용자 스냅샷의 journal 은 09-04 이전 NULL → 선이 09-05 부터 시작한다(정상). 09-10 값은 배치 재계산으로 4,446,635 → 4,279,175 로 바뀌었고 화면 표기(445만 → 428만)가 이를 따라간다.
- 버전: 올리지 않음 — 대시보드 표시 개선 묶음으로 다음 배포에 함께 낸다.
- Git commit: feat(dashboard): journal line and series highlight on the asset trend chart

## [2026-09-10] ui | 브랜드 마크 A안(열린 문) 적용 (사용자 지시)

- 지시: 마크 개정안 세 안 중 "A시안으로 변경작업을 진행하세요".
- 작업 내용: 짙은 사각형 + 흰 마름모를 **문틀 세 면 + 빠져나가는 상승 화살**로 바꿨다. 마크를 `components/brand.tsx` 로 뽑아 사이드바(32)·모바일 상단 바(28)·로딩(20)·로그인(32) 네 곳이 같은 것을 쓴다. 로고타입은 `Me` 만 브랜드색으로 끊었다. 브랜드색 `--color-brand: #0d9488`(청록)을 새로 뒀다 — 빨강·파랑·초록·주황은 이미 수치의 뜻이 차 있어 로고가 쓸 수 있는 빈 자리가 청록뿐이다.
- 파비콘: 지금까지 없었다. `app/icon.svg`(획 3.6, 어두운 UI 에서 문틀만 밝게)와 `app/apple-icon.png`(180×180 흰 바탕)을 추가했고 Next.js App Router 가 자동으로 걸어 `layout.tsx` 는 손대지 않았다.
- 테스트 결과: `tsc --noEmit` 무오류. 헤드리스 확인 — 로그인 카드·사이드바·모바일 상단 바 모두 새 마크와 `Exit`+청록 `Me`, 파비콘 링크 2개가 200 으로 응답, 콘솔 오류 없음. 파비콘 획은 3.1/3.6/4.0/4.6 을 16px 실제 래스터로 찍어 비교해 3.6 으로 정했다(4.0 부터 화살촉이 뭉친다). 서버 코드 변경 없음.
- 고친 것: 로고타입을 조각(fragment)으로 두니 모바일 상단 바의 `flex gap-2` 가 `Exit` 과 `Me` 사이를 벌려 "Exit Me" 로 보였다 — 한 덩이로 감쌌다.
- 하지 않은 것(개정안에 있었으나 지시 밖): accent 토큰 정리(#c2410c vs #f97316 → #ea580c 계열), 부제 `AUTO TRADING` 문구 교체.
- 문서: `THROUGHLINE/docs/brand-mark-20260910.md` 신규 — 채택 이유·경로·반영 위치·보류 항목.
- Git commit: ui: adopt the open-door brand mark

## [2026-09-12] strategy | 그리드 간격 상한 4.0% → 2.5% (사용자 지시)

- 경위: "거래가 너무 안 되는 것 아닌가" 질문에서 출발해 거래 빈도를 올리는 변경 6종(그리드 폭, 단계 수, 같은 범위 촘촘히, 1차만 얕게, 5단 가중 4종, 상한)을 모두 측정. 상한 인하만 MDD를 건드리지 않아 채택 지시를 받았다.
- 변경: `Params.grid_max` 0.04 → **0.025**. 정본 §10 튜닝표 `Grid 계수 / clip`(○) 범위 내 값 조정이라 SOURCES 는 수정하지 않고 feature-strategy-engine.md §5.5 가 확정값을 가진다(§15 이력에도 기록).
- 근거 (`docs/grid-cap-study-20260912.md`): 상한은 "고변동 + 그리드 가동" 국면에서만 물린다 — 실측 바인딩 8.6년 중 49일, **전부 2026년**(2020년 급등 구간은 하락장이라 그리드 정지로 무관). 시작일 89개 짝 비교에서 달라진 창 5개, 최악 +0.00%p, 최선 +12.01%p, **MDD 악화 0건**, 전 구간 MDD −21.0% 불변. 예산 규칙과 갭 필터가 총량을 묶어 진입 위치만 위로 옮기기 때문이다. 1.0% 는 MDD 악화 27/89 로 기각, 2.0% 는 평균 +0.08%p 앞서지만 증거가 창 6개뿐이라 우열 판정 불가 — 절벽에서 한 칸 더 먼 2.5% 를 택했다.
- 한계(문서에 명시): 이득의 근거가 사실상 2026년 국면 하나다. 표본 1.
- 함께 고친 곳: 챗봇 전략 요약(`0.8~4%` → `0.8~2.5%`)과 그 유출 방지 테스트 문자열, 사용자 가이드 RAVG 설명, feature 문서 골든 시나리오 주석.
- 적용 범위: 사용자 설정은 기본값과 다른 값만 저장하므로 `grid_max` 를 직접 지정한 사용자가 없으면 전원 즉시 2.5% 로 바뀐다(개발 DB 확인 — 지정한 사용자 0명). 주문표는 요청 시점에 계산돼 배치를 기다리지 않는다. **다만 백테스트에서 전환한 포트는 `params.algo` 에 공식이 동결돼 있어(2026-09-05 결정) 그 포트만 종전 4.0% 를 계속 쓴다** — 서버에 그런 포트가 있으면 별도 처리가 필요하다(개발 DB 에는 없음).
- 테스트 결과: **288 passed**(신규 2건 — 상한 2.5% 클립·하한 0.8% 불변, 고변동일 사다리가 68,250/66,500/64,750 로 올라옴), `tsc --noEmit` 무오류. 최신 봉(2026-09-10, 종가 112,150) 기준 계산값 3.85% → Grid 2.50% 로 클립되는 것을 실제 DB 로 확인.
- Git commit: strategy: cap the grid step at 2.5% instead of 4.0%

## [2026-09-12] fix | 실전 로트 전략 태그 — 재구성이 백테스트와 같은 규칙을 따른다 (감사 A1·A2, 사용자 지시 "권고 모두 진행")

- 문제(감사 재현): 실전 주문표는 매일 저녁 원장에서 로트를 다시 만들며 전략 정보를 버렸다. 레버리지 로트는 전부 `lev_strat` 로(A1) — 실전 규칙을 백테스트에 넣으면 전술 진입 35→81건, 전술 이탈 18→0건. K200 익절가는 매일 최근 종가×(1+오늘 Grid) 로 이동(A2) — grid 체결 220건 중 22% 가 다음날 원가 아래 익절가. 명세 §5.6 "체결 시점 스냅샷 고정, 매일 재계산 금지" 위반. 근본 원인은 하나: 원장에 로트 종류·익절가를 저장하지 않음.
- 작업 내용
  - 마이그레이션 0027: `trade_transactions.lot_kind·tp_price(🔒)`, `broker_orders.plan_grid·plan_regime`. 기존 행은 NULL → 종전 근사 유지.
  - `app/lots.py` 신설: `lot_tag`(백테스트 체결 블록과 같은 규칙 — 상승장 계획이면 core, 아니면 grid + 체결가×(1+계획일 Grid) 올림, 레버리지는 주문 종류가 로트 종류), `sell_tag`(익절은 지정가로 로트 귀속), `consume_sell`(익절 → 그 익절가 로트, 전략·전술 매도 → 그 종류 로트 먼저, 나머지 FIFO — `ledger.sell(lot=/kinds=)` 동일), `rebuild_lots`(태그에서 출발해 체결일 이후 레짐 전환만 재생 — `apply_regime_conversion` 동일).
  - 태그 원천: 무인(`autoexec`)·재등록·예약(`broker.reserve`)·직접 주문(`manual`) 발주 행에 계획 Grid·레짐 저장(주문표 스냅샷 payload 에 `grid` 추가). 체결 가져오기(`import_fills`, 마감 후 배치 포함)가 주문번호로 `broker_orders` 를 찾아 태그. 화면 '체결 등록'은 줄의 종류·Grid·레짐을 기억해 종목·방향이 그대로일 때만 `strategy_kind·plan_grid·plan_regime·plan_price` 를 동봉 → `POST /positions` 가 태그 생성(국내 포트 buy/sell 만).
  - 재구성(`signals._state_before`·`_portfolio_orders`)이 태그를 쓰고 매도를 귀속대로 소진. `final_lots` 에 kind·tp_price 추가(검증용).
- 테스트 결과: **297 passed**(신규 9 — 태그 규칙 4, 재구성·전환 재생 2, **동일성 1**: 합성 700봉 백테스트의 체결을 태그 원장으로 넣어 재구성 → 최종 로트(종류·익절가·수량·단가)와 다음 주문표가 백테스트와 비트 동일, 체결 등록 API 태그 저장 1, 체결 가져오기 주문번호 태그 1). `tsc --noEmit` 무오류. 헤드리스: '체결 등록' → 등록 요청을 가로채 확인 — `strategy_kind=boot, plan_grid=0.025, plan_regime=NEUTRAL` 동봉(실제 등록은 하지 않음). 개발 DB `alembic upgrade head` → 0027.
- 한계: 태그 없는 로트(시딩·HTS 직접 주문·구형 행)는 종전 근사 그대로 — 태그 로트가 쌓일수록 수렴(ASSUMPTIONS 2026-09-12). 실전 체결가(정수)와 백테스트 체결가(실수)의 익절가는 호가 반올림에서 최대 1틱 다를 수 있다.
- 문서: feature-strategy-engine §5.6, feature-portfolio(API·화면·가져오기), ASSUMPTIONS(2026-08-28 근사 범위 축소 + 2026-09-12 항목).
- Git commit: fix: persist strategy lot tags so the live order sheet rebuilds the backtest's state

## [2026-09-12] fix | 레버리지 매수도 공통 현금버퍼를 지킨다 (감사 A8, 사용자 지시 "권고 모두 진행")

- 문제(감사 재현): 플래너의 가용 현금 예약(`cash_left` = 현금 + 예정 매도대금 − 총자산×0.5%)이 K200 그리드에만 적용되고 뒤에 오는 레버리지 매수(전략·전술)는 현금과 무관하게 수량이 나왔다. 현금 1억·E 1.30·전술 1·2차 동시 조건에서 두 종목 매수 명목 99,910,400원 > 가용 99,500,000원(+410,400원). 백테스트는 체결 시 현금 부족 주문을 통째로 건너뛰고, 실전 실행기는 매수가능조회로 수량을 줄여 경로가 달랐다.
- 작업 내용: 레버리지 전략·전술 매수 수량을 `min(예산, cash_left)` 로 계산하고 `cash_left` 를 차감한다. 그리드가 먼저 계산되므로 줄어드는 쪽은 레버리지 매수. 레버리지 매도 대금은 매수 재원으로 세지 않는다(보수적).
- 백테스트 영향(1억, 2017~2026): 누적 +262.49% → **+261.68%**, CAGR 16.68% → 16.65%, MDD −21.67% → −21.86%, 청산 392 → 408 — 통째로 생략되던 매수가 가용액만큼 부분 진입으로 바뀐 결과.
- 테스트 결과: **298 passed, 294 warnings in 78.07s (0:01:18)** (신규 1 — A8 반례가 버퍼 안으로 들어오고 그리드 가격 불변, 현금이 얇으면 레버리지 매수가 가용액 안으로 줄어듦).
- 문서: feature-strategy-engine §5.5 현금 버퍼 문장 확장.
- Git commit: fix: leverage buys share the K200 cash reservation

