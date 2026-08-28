# PLAN.md — 개발 Phase 계획

> 회전 규칙: 완료 Phase가 4개를 초과하면 오래된 것을 PLAN-archive.md로 이관한다.

## Phase 0 — 인프라 골격

- **목적**: 개발·CI가 도는 최소 환경
- **산출물**: docker-compose 3종, 7서비스 healthcheck, FastAPI/Next.js 스캐폴드, Alembic 마이그레이션 골격, GitHub Actions(테스트+compose e2e), `.env.example`
- **완료 조건**: `docker compose up -d` 한 줄 기동, 스모크 테스트(각 서비스 health) 실제 실행·통과
- **QA 완료 조건**: CI green 1회
- **의존**: 없음 | **상태**: **완료** (2026-08-28 — compose 7서비스 healthy, e2e 스모크 통과, CI 정의)
- **관련**: [ARCHITECTURE §2·§8](ARCHITECTURE.md), [ADR-001](adr/001-compose-monolith.md)

## Phase 1 — 시세 데이터 파이프라인

- **목적**: 모든 기능의 데이터 토대
- **산출물**: 스키마(ohlcv_daily/intraday·instruments·status_history·corporate_actions·calendar·batch_runs), 10년 시딩, 일일 배치, KIS/pykrx 클라이언트, `GET /ohlcv`·종목 검색·`WS /ws/quotes`
- **완료 조건**: [feature-market-data.md §13](features/feature-market-data.md) — 자동 테스트 실제 통과 + 시딩 완주 + 배치 3거래일 연속 성공
- **QA 완료 조건**: 회귀 체크리스트 "데이터 파이프라인" 절 통과
- **의존**: Phase 0 | **상태**: **진행 중** (스키마·KIS 클라이언트·시딩·/ohlcv 완료. 남은 것: KIS 키 기입 후 실 시딩, 일일 배치 crontab 확정·3거래일 검증, WS /ws/quotes, 종목 검색 확장)
- **관련**: [feature-market-data.md](features/feature-market-data.md), [ADR-002](adr/002-timescaledb.md), [ADR-004](adr/004-market-data-source.md)

## Phase 2 — 주식 차트

- **목적**: 분석 화면 v1 (M1~M2 마일스톤 대응)
- **산출물**: 차트 화면(4-툴바), 지표 TS 구현 + Web Worker, 드로잉 5종·레이아웃 저장, 인증(JWT) 기반 사용자 스캐폴드
- **완료 조건**: [feature-chart.md §13](features/feature-chart.md) — 지표 교차 검증 포함 자동 테스트 실제 통과
- **QA 완료 조건**: 수동 60fps 계측 기록
- **의존**: Phase 1 | **상태**: 대기
- **관련**: [feature-chart.md](features/feature-chart.md), [ADR-003](adr/003-auth-jwt.md), [ADR-005](adr/005-strategy-single-source.md)

## Phase 3 — 백테스트 엔진 + 3스텝 위저드

- **목적**: 검증 루프의 핵심
- **산출물**: 전략 모듈 `strategy/`(순수 함수), 백테스트 워커(벡터화)·체결 시뮬레이션·비용 모델·KPI, 위저드 UI, 취소·진행률 WS, data_fingerprint·stale 배지
- **완료 조건**: [feature-backtest.md §13](features/feature-backtest.md) — look-ahead·체결·KPI·잡 수명주기 테스트 실제 통과, 단일 종목 5년 < 5s 실측
- **QA 완료 조건**: 회귀 "백테스트" 절 통과. **종료 시 유니버스 60s 실측 → 유니버스 정의 변경요청 제출**
- **의존**: Phase 1 (병행 가능: Phase 2) | **상태**: 대기
- **관련**: [feature-backtest.md](features/feature-backtest.md), [ADR-005](adr/005-strategy-single-source.md), [ADR-006](adr/006-ravg-v2-adoption.md)

## Phase 4 — RAVG v2 전략 엔진 (일일 시그널·주문표)

- **목적**: 본 프로젝트 차별점 — 검증된 전략의 일일 운용
- **산출물**: 일일 시그널 배치(Celery beat), 주문표 API·화면, 시그널 append-only 체인, **절제 5종 백테스트 실행 리포트 + 파라미터 확정 변경요청**
- **완료 조건**: [feature-strategy-engine.md §13](features/feature-strategy-engine.md) — 골든·경계·재현성(백테스트=시그널 바이트 동일) 테스트 실제 통과, 실데이터 배치 30분 내 완료
- **QA 완료 조건**: 회귀 "전략 엔진" 절 통과
- **의존**: Phase 3 | **상태**: 대기
- **관련**: [feature-strategy-engine.md](features/feature-strategy-engine.md), [ADR-005](adr/005-strategy-single-source.md), [ADR-006](adr/006-ravg-v2-adoption.md)

## Phase 5 — 실전매매 기록

- **목적**: 실행 루프 완성 (백테스트→실전 전환 포함)
- **산출물**: 거래 등록(FIFO 원장·암호화), 수익률 카드, TWR/XIRR, 매매일지, 전환 버튼, 차트 마커 연동
- **완료 조건**: [feature-portfolio.md §13](features/feature-portfolio.md) — FIFO·XIRR·암호화 테스트 실제 통과, 전환 e2e 1회
- **QA 완료 조건**: 회귀 "인증·권한"·"핵심 흐름" 절 통과
- **의존**: Phase 3 (전환 기능), Phase 2 (차트 마커) | **상태**: 대기
- **관련**: [feature-portfolio.md](features/feature-portfolio.md), [ADR-003](adr/003-auth-jwt.md)

## Phase 6 — 대시보드 + 마감 QA

- **목적**: 관리 루프 완성과 릴리즈 준비 (M4 대응)
- **산출물**: 벤토 대시보드(총자산·도넛·추이·캘린더·레짐 게이지), 자산 스냅샷 배치, 다크/라이트·반응형·접근성 마감, 분석 이벤트 3종
- **완료 조건**: [feature-dashboard.md §13](features/feature-dashboard.md) — 자동 테스트 실제 통과 + 접근성 수동 QA
- **QA 완료 조건**: [release-checklist.md](qa/release-checklist.md) 전 항목
- **의존**: Phase 5 | **상태**: 대기
- **관련**: [feature-dashboard.md](features/feature-dashboard.md)
