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

- 평일 배치 시각(KST): 08:40 자동 승인 보완 → 08:57 사전 갭 취소 → 09:01 무인 실행 → 15:45/17:10 장 마감 동기화(체결·주문 상태·예수금 대조) → 16:05 일봉 → 16:40 스냅샷·일일 현황 알림 → 16:45 자동 승인. 모두 worker/scheduler 컨테이너가 돌리므로 배포 뒤 두 컨테이너가 떠 있는지 확인한다(`docker compose ps`).
- 외부 통신: KIS Open API(openapi.koreainvestment.com), 텔레그램 Bot API(api.telegram.org, 사용자가 설정에 넣은 봇 토큰으로 서버가 발송), OpenRouter(챗봇). 방화벽이 있으면 이 세 호스트의 HTTPS 아웃바운드를 허용한다.
- 비밀값: KIS 앱키·시크릿·계좌번호, 텔레그램 봇 토큰은 DB 에 AES-GCM 암호화로 저장되고 API 는 마스킹만 돌려준다. `.env` 의 암호화 키를 잃으면 복호화할 수 없으니 백업에 포함한다.
- 점검: 매매 로그(메뉴)에서 "경고 이상만" 필터로 실패·정지를 확인한다. 정지된 포트는 실전매매 주문표 배너에서 사유를 보고 "다시 켜기". 상세 절차는 [auto-execution-20260906.md §5](auto-execution-20260906.md).

## 백업

- DB 볼륨 `pgdata`를 일 단위 스냅샷. 복구 후 `batch_runs` 최신 성공 시점 이후 배치를 재실행합니다.

## 보안 점검

- 로그에 KIS 키·토큰이 노출되지 않는지 주기 점검(`grep`으로 키 값 검색 — 검출 0건이어야 함).
- `.env` 권한·백업 암호화 확인. 상세 계약은 [ARCHITECTURE.md §6](../ARCHITECTURE.md).
