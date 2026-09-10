# 운영자 가이드

## 설치·기동

1. `.env.example`을 복사해 `.env` 작성 — `KIS_APP_KEY`, `KIS_APP_SECRET`, `DATABASE_URL`, `REDIS_URL`, `ENCRYPTION_KEY`, `JWT_SECRET`. 값은 절대 커밋하지 않습니다.
2. `docker compose up -d` — nginx/web/api/worker/scheduler/db/redis 7서비스가 healthcheck 순서대로 기동합니다.
3. 초기 시세 시딩: `docker compose run --rm api python -m scripts.seed --years 10`
   - 종목×기간 체크포인트가 있어 중단 후 재실행하면 이어받습니다.

## 일일 배치

- Celery beat이 장 마감 후 자동 실행: 시세 수집 → 검증 → 적재 → RAVG v2.5 시그널 배치 → 주문표 발행 (목표: 30분 내).
- 상태는 `batch_runs` 테이블과 구조화 로그(`docker compose logs scheduler worker`)로 확인합니다.

## 장애 대응

| 증상 | 확인 | 조치 |
|---|---|---|
| 주문표 미생성 | `GET /signals/daily`의 status (`MISSING`/`FAILED`/`INSUFFICIENT_HISTORY`) | `batch_runs` 실패 사유 확인 후 배치 재실행. 전일 주문표를 임의 재사용하지 않음 |
| KIS 장애·한도 초과 | 로그의 폴백 기록 | pykrx 폴백 자동 — 화면 "시세 지연" 표기 확인 |
| 백테스트 잡 적체 | `backtest` 큐 길이 | worker 재시작. `ingest` 큐와 분리되어 수집은 영향 없음 |
| 수정주가 이벤트 | `corporate_actions` 등록 | `adj_factor` 재계산(adj_version 증가) → 기존 백테스트에 stale 배지 자동 표시 |

## 무인 운영 배치와 외부 연동 (2026-09-07)

- 평일 배치 시각(KST, ADR-009 2026-09-08): 08:30~09:10 예상 시가 관찰(매 분, 표시 전용) → 09:01 무인 실행(주문표 계산·동결 → 시가 → 갭 → 잔고 → 상한 → 발주) → 09:15 감시(미실행 포트 지연 실행) → 15:45/17:10 장 마감 동기화(체결·주문 상태·예수금 대조; 17:10 은 beat kwargs `retry=True` 로 변경 없으면 알림 생략) → 16:05 일봉 → 16:40 스냅샷·일일 현황 알림. 60초마다 하트비트(beat→큐→워커) — worker·scheduler 컨테이너 헬스체크가 Redis 키 `autoexec:pipeline:heartbeat` 를 보므로 `docker compose ps` 에서 unhealthy 면 배치가 돌지 않는 상태다(2026-09-08 사고: 승인만 남고 09:01 미실행, 종전 헬스체크는 import 만 확인). 배포 시 옛 `approved` 행을 cancelled 로 정리하고 `trading_calendar` 를 최신으로 유지한다. 스케줄러 상태 파일은 컨테이너 `/var/lib/celery/celerybeat-schedule`(코드 밖, `.dockerignore`)이며, 멈춘 사이 지나간 크론은 1시간 안이면 기동 즉시 따라잡고 그 이상은 건너뛴다(`beat_cron_starting_deadline`). 09:01/09:15 무인 실행은 09:30 을 넘기면 발주하지 않고 "지연 상한 초과"로만 기록한다(2026-09-09 사고, NOTES). 배포 직후 `docker compose logs scheduler | grep "Sending due task"` 에 daily-ingest·daily-snapshot 이 붙어 있으면 낡은 상태 파일이 이미지에 들어간 것이다. 과거 날짜 스냅샷이 틀렸을 때(전일 대비가 가격 변동과 크게 다를 때)는 `docker compose exec -T api python -m app.services.snapshot_repair --date YYYY-MM-DD` 로 as-of 원장 재계산 결과를 보고 `--apply` 로 저장한다(2026-09-09).
- **저녁 블록(2026-09-14 KRX 애프터마켓 대응)**: 20:10 국내 주식 일봉(애프터마켓 종료 후에야 확정) · 20:15 체결 동기화(변경 없으면 알림 없음) · 20:20 스냅샷 재계산 + 그날 저녁 체결이 있은 사용자에게만 일일 현황 한 번 더. 국내 ETF·미국은 16:05 그대로.
- 외부 통신: KIS Open API(openapi.koreainvestment.com), 텔레그램 Bot API(api.telegram.org, 사용자가 설정에 넣은 봇 토큰으로 서버가 발송), OpenRouter(챗봇). 방화벽이 있으면 이 세 호스트의 HTTPS 아웃바운드를 허용한다.
- 비밀값: KIS 앱키·시크릿·계좌번호, 텔레그램 봇 토큰은 DB 에 AES-GCM 암호화로 저장되고 API 는 마스킹만 돌려준다. `.env` 의 암호화 키를 잃으면 복호화할 수 없으니 백업에 포함한다.
- **배포 후 훅 (2026-09-08)**: `scripts/deploy.sh <태그>` 는 헬스 검증 뒤 체크아웃된 태그의 [`scripts/post-deploy.d/*.sh`](../../scripts/post-deploy.d/README.md) 를 번호순으로 서브셸에서 `source` 한다 — 10 ADR-009 전환 확인(approved 잔존 0건; 정리 자체는 alembic 0025) · 20 거래일 캘린더 갱신(KIS 국내휴장일조회 CTCA0903R, 오늘부터 120일 — 워커도 일요일 06:00) · 30 하트비트 확인(150초). 훅 실패는 배포 결과 ✗ 로 기록되고 다음 훅은 계속 돈다. `--skip-hooks` 로 생략. **검토(외부 스크립트 호출 방식)**: 후보는 ① deploy.sh 안에 인라인(버전별 절차가 쌓여 비대해지고 옛 절차가 새 배포에서도 돌아감) ② 서버에만 두는 스크립트나 원격에서 내려받는 스크립트(재현·검토 불가, 태그와 어긋남) ③ 저장소 안 `post-deploy.d` 훅을 태그와 함께 배포하고 deploy.sh 가 source(채택 — deploy.sh 자체는 옛 복사본으로 돌지만 훅은 새 태그의 것) ④ 한 번만 일어나는 데이터 전환은 alembic 데이터 마이그레이션(채택 — 정확히 한 번·트랜잭션·`alembic_version` 추적). 훅은 멱등이어야 하고 실행 순서는 파일명 접두 번호. **자기 갱신**(2026-09-08 사용자 지적 "변경된 deploy.sh 는 기존 스크립트로 적용 안 되지 않나" — 맞다: 실행 중인 것은 배포 전 버전의 임시 복사본이라 v0.11.0 을 옛 스크립트로 올리면 훅 단계가 없다): 체크아웃 직후 배포되는 버전의 `scripts/deploy.sh` 가 실행 중인 것과 다르면 그것으로 처음부터 다시 실행한다(`DEPLOY_SH_UPGRADED` 로 한 번만, fetch·checkout 은 멱등). 자기 갱신이 없는 옛 스크립트로 처음 올릴 때만 `git show <태그>:scripts/deploy.sh > /tmp/deploy.sh && bash /tmp/deploy.sh <태그>`.
- 점검: 매매 로그(메뉴)에서 "경고 이상만" 필터로 실패·정지를 확인한다. 정지된 포트는 실전매매 주문표 배너에서 사유를 보고 "다시 켜기". 상세 절차는 [auto-execution-20260906.md §5](auto-execution-20260906.md).

## 백업

- DB 볼륨 `pgdata`를 일 단위 스냅샷. 복구 후 `batch_runs` 최신 성공 시점 이후 배치를 재실행합니다.

## 보안 점검

- 로그에 KIS 키·토큰이 노출되지 않는지 주기 점검(`grep`으로 키 값 검색 — 검출 0건이어야 함).
- `.env` 권한·백업 암호화 확인. 상세 계약은 [ARCHITECTURE.md §6](../ARCHITECTURE.md).
