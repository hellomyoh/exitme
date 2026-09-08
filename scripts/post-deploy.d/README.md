# 배포 후 훅 (scripts/post-deploy.d)

`scripts/deploy.sh <태그>` 가 재빌드 → alembic → 헬스 검증을 마친 뒤, 이 디렉터리의 `*.sh` 를 **파일명 순으로** 실행한다 (2026-09-08 지시 "배포에 필요한 명령을 스크립트에 포함").

## 규칙

- **저장소 안에, 배포되는 태그와 함께 버전 관리** — deploy.sh 는 옛 복사본으로 돌지만 훅은 방금 체크아웃한 태그의 것을 읽는다. 그 버전이 필요로 하는 절차가 그 버전과 같이 간다. 원격에서 내려받는 스크립트·서버에만 있는 스크립트는 쓰지 않는다(재현 불가·검토 불가).
- **서브셸에서 `source`** — deploy.sh 의 `"${COMPOSE[@]}"`(compose 명령 배열)·`log`·`fail`·`pick`·`PORT`·`EXITME_DIR` 를 그대로 쓴다. `fail "…"` 또는 0 이 아닌 종료는 그 훅만 실패로 기록하고(배포 결과 ✗) 다음 훅은 계속 실행한다. `exit 0` 은 훅만 끝낸다.
- **멱등** — 매 배포마다 다시 실행된다. 한 번만 일어나야 하는 데이터 전환은 훅이 아니라 **alembic 데이터 마이그레이션**(예: `0025_adr009_cancel_stale_approved.py`)에 둔다 — 정확히 한 번, 트랜잭션, `alembic_version` 으로 추적된다. 훅은 그 결과를 검증하거나(10), 외부 소스로 상태를 맞추거나(20), 기동 뒤에만 확인할 수 있는 것을 본다(30).
- 생략: `scripts/deploy.sh v0.11.0 --skip-hooks`. 특정 훅만 손으로: `cd /path/to/exitme && COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml) && source scripts/post-deploy.d/20-trading-calendar.sh` (bash).

## 현재 훅

| 파일 | 하는 일 | 실패 조건 |
|---|---|---|
| `10-verify-adr009-transition.sh` | `broker_orders.status='approved'` 잔존 0건 확인 (정리는 alembic 0025) | 잔존 > 0 |
| `20-trading-calendar.sh` | `python -m app.services.calendar --days 120` — KIS 국내휴장일조회로 거래일 캘린더 갱신 (휴장 미등록이면 09:01 이 발주하지 않음, ADR-009 §2-3) | KIS 키 없음·조회 실패 |
| `30-pipeline-heartbeat.sh` | beat → 큐 → 워커 하트비트(Redis `autoexec:pipeline:heartbeat`)가 150초 안에 찍히는지 | 하트비트 없음 |
