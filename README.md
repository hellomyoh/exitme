# ExitMe

RAVG v2.5 매매 전략을 내장한 **백테스트 + 실전매매 기록 + 자산 대시보드** 통합 웹.
전략을 **검증(백테스트) → 실행(일일 주문표·실전 기록) → 관리(대시보드)** 하는 하나의 루프로 제공합니다.

> 모의·과거 데이터 기반이며 투자 권유가 아닙니다. 실주문 체결(증권사 연동)은 v1 범위 외입니다.

## 주요 기능

- **주식 차트** — HTS 수준 캔들 차트, 지표·드로잉, 레이아웃 저장
- **백테스트 3스텝 위저드** — 조건 설정 → 실행(진행률) → 결과(KPI·자산곡선·오버레이 비교), RAVG v2.5 프리셋 + 절제(ablation) 플래그
- **RAVG v2.5 전략 엔진** — 장 마감 후 일일 배치로 레짐·노출·다음 거래일 지정가 주문표 자동 생성
- **실전매매 기록** — 매수 시점 기준 수익률, FIFO 원장, TWR/XIRR, 매매일지
- **증권사 연동(KIS)** — 체결 자동 가져오기, 주문표의 예약주문 접수, 장 마감 동기화, 예수금 대조, 새 실전매매 시작 시 잔고 불러오기
- **무인 실행(ADR-008)** — 승인한 지정가를 09:01 시가 확인 후 발주(갭 취소·원장 대조·매수가능조회·자동 정지), 08:57 예상 시가 갭 취소(취소만), 완전 무인(16:45 자동 승인·하루 매수 상한·전량 취소)
- **매매 로그·텔레그램 알림** — 거래·주문·이벤트를 한 표로, 실패 필터; 봇 토큰만 넣으면 결과·현황을 텔레그램으로
- **자산 대시보드** — 총자산·자산 구성·손익 캘린더·레짐 게이지
- **매매 도우미 챗봇** — 계좌·주문표·로그·무인 운영 상태를 도구로 조회해 답하는 어시스턴트(OpenRouter)

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

## 업데이트 (원격 서버에 새 버전 반영)

`docker compose restart` 는 **이미지를 다시 만들지 않습니다.** 운영 구성(`-f docker-compose.prod.yml`)은 소스를 이미지에
넣어 실행하므로 `git pull` 뒤 반드시 `--build` 로 다시 만들고, 그다음 마이그레이션을 적용합니다.

**권장: 배포 스크립트** — 태그(버전)를 인자로 받아 체크아웃 → 재빌드 → 마이그레이션 → 헬스 검증까지 한 번에 합니다.

```bash
scripts/deploy.sh v0.1.1            # 태그 v0.1.1 로 패치 배포
scripts/deploy.sh v0.1.1 --prune    # 배포 후 안 쓰는 이미지 정리
scripts/deploy.sh v0.1.1 --force    # 같은 버전 재배포·하위 버전 롤백을 강제
scripts/deploy.sh main              # main 최신으로 (검증용)
scripts/deploy.sh restart           # 코드 변경 없이 컨테이너만 재시작 (docker compose restart) 후 헬스 확인
scripts/deploy.sh restart api web   # 일부 서비스만 재시작
```

마지막에 `✓ version v0.1.1 · ✓ build_time … · ✓ db 0019` 가 나오면 반영 완료이고, ✗ 가 있으면 사유와 함께 실패로 끝납니다.
추적 파일에 로컬 변경이 있으면 덮어쓰지 않고 중단합니다(`.env` 같은 미추적 파일은 무관).
태그 배포는 실행 중인 버전과 먼저 비교해 **같은 버전이면 재빌드하지 않고 중지**하고, 낮은 버전(롤백)도 중지합니다 — 의도한 것이면 `--force`.
`restart` 는 이미지를 다시 만들지 않으므로 코드 변경은 반영되지 않습니다(설정·메모리 문제로 다시 띄울 때 사용).

수동으로 할 때는 아래 순서입니다.

```bash
git pull origin main
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build   # api/worker/scheduler/web 재빌드
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec api alembic upgrade head   # 새 이미지 안에서 마이그레이션
curl -s http://localhost:12010/api/health
# {"status":"ok","version":"v0.1.0","build_time":"2026-09-05T10:30:12Z","db_revision":"0019"}
```

`version` 은 `apps/api/app/VERSION`(태그와 함께 갱신), `build_time` 은 **이미지를 만든 시각(UTC)**, `db_revision` 은 alembic 리비전입니다.
`build_time` 이 방금 시각으로 바뀌지 않았으면 이미지가 재빌드되지 않은 것입니다(`restart` 만 한 경우). 화면 왼쪽 아래에도
`v0.1.0 · db 0019 · 빌드 09-05 19:30` 으로 같은 정보가 보입니다.

개발 구성(override 기본 적용, 소스 bind mount)에서는 `git pull` 후 `docker compose exec api alembic upgrade head` 와
`docker compose restart api worker scheduler web` 만으로 반영되며, 빌드 시각 대신 "개발(bind mount)" 로 표시됩니다.
버전 규칙은 [AGENTS.md](AGENTS.md) "버저닝과 태그" 참조.

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
