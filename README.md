# StockLab

RAVG v2.5 매매 전략을 내장한 **백테스트 + 실전매매 기록 + 자산 대시보드** 통합 웹.
전략을 **검증(백테스트) → 실행(일일 주문표·실전 기록) → 관리(대시보드)** 하는 하나의 루프로 제공합니다.

> 모의·과거 데이터 기반이며 투자 권유가 아닙니다. 실주문 체결(증권사 연동)은 v1 범위 외입니다.

## 주요 기능

- **주식 차트** — HTS 수준 캔들 차트, 지표·드로잉, 레이아웃 저장
- **백테스트 3스텝 위저드** — 조건 설정 → 실행(진행률) → 결과(KPI·자산곡선·오버레이 비교), RAVG v2.5 프리셋 + 절제(ablation) 플래그
- **RAVG v2.5 전략 엔진** — 장 마감 후 일일 배치로 레짐·노출·다음 거래일 지정가 주문표 자동 생성
- **실전매매 기록** — 매수 시점 기준 수익률, FIFO 원장, TWR/XIRR, 매매일지
- **자산 대시보드** — 총자산·자산 구성·손익 캘린더·레짐 게이지

상세 명세: [THROUGHLINE/features/README.md](THROUGHLINE/features/README.md)

## 기술 스택

Next.js 15 (TS, Tailwind v4, shadcn/ui, Lightweight Charts v5) · FastAPI (Python 3.12, SQLAlchemy, Pydantic v2) ·
Celery + Redis 7 · PostgreSQL 16 + TimescaleDB · Nginx · docker compose · GitHub Actions.
상세: [THROUGHLINE/ARCHITECTURE.md](THROUGHLINE/ARCHITECTURE.md)

## 사전 요구사항

- Docker / docker compose v2
- 한국투자증권 KIS Open API 앱키 (시세 조회용)

## 설치·실행

```bash
cp .env.example .env   # 환경변수 입력 — KIS_APP_KEY/KIS_APP_SECRET 필수
docker compose up -d   # 전체 기동 (7서비스, healthcheck 순서 기동)

# 초기 시세 시딩 (10년 일봉) — KIS 키가 있어야 실행됩니다 (KRX가 pykrx 요청을 차단 중)
docker compose run --rm api python -m scripts.seed --years 10
```

KIS 앱키는 [KIS Developers 포털](https://apiportal.koreainvestment.com)에서 발급합니다.
구현은 [공식 예제 저장소](https://github.com/koreainvestment/open-trading-api)의 인증·시세 패턴을 따릅니다.

## 환경변수

| 이름 | 용도 |
|---|---|
| `KIS_APP_KEY` / `KIS_APP_SECRET` | KIS Open API 인증 (서버 전용) |
| `DATABASE_URL` | PostgreSQL/TimescaleDB 연결 |
| `REDIS_URL` | Redis (캐시·브로커) |
| `JWT_SECRET` | JWT 서명 키 |
| `ENCRYPTION_KEY` | 계좌 데이터 AES-GCM 암호화 키 |

값과 Secret은 커밋하지 않습니다 (`.env.example`만 커밋).

## 테스트

```bash
docker compose run --rm api pytest          # 백엔드 (전략 골든·백테스트 정합성 포함)
docker compose run --rm web npm test        # 프론트 (지표 교차 검증 포함)
```

QA 기준: [THROUGHLINE/qa/README.md](THROUGHLINE/qa/README.md) — "테스트 통과"는 실제 실행 결과가 기록된 경우만 인정.

## 프로젝트 구조

```text
├── README.md / AGENTS.md / CLAUDE.md   # 루트 3파일
└── THROUGHLINE/                        # 프로젝트 문서 (명세·계획·QA·ADR)
    ├── ARCHITECTURE.md  # 횡단 계약
    ├── PLAN.md          # 개발 Phase (0~6)
    ├── features/        # 기능명세서 6종
    ├── docs/            # 사용자·운영자 문서
    ├── qa/              # 회귀·수동·릴리즈 체크리스트
    ├── adr/             # 설계 결정 기록
    └── SOURCES/         # 제출 자료 (전략 정본 포함, 불변)
```

(애플리케이션 코드 구조는 Phase 0 스캐폴드 후 이 절에 추가)

## 주요 문서

- [사용자 가이드](THROUGHLINE/docs/user-guide.md) · [전략 가이드](THROUGHLINE/docs/strategy-guide.md) · [운영자 가이드](THROUGHLINE/docs/operator-guide.md)
- [아키텍처](THROUGHLINE/ARCHITECTURE.md) · [개발 계획](THROUGHLINE/PLAN.md) · [전략 정본 (RAVG v2)](THROUGHLINE/SOURCES/trade_algorithm_final.md)
