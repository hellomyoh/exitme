# 무인 매매 — 09:01 단일 실행: 계산·시가 확인·발주·동결 (2026-09-06 → 2026-09-08 ADR-009)

> 정책 근거: [ADR-009](../adr/009-unattended-single-execution.md) (2026-09-08 단일 실행) — [ADR-008](../adr/008-controlled-auto-execution.md) 의 승인·보완·사전 갭 취소 구조를 대체. 이 문서는 구현·운영 지도다. §5-1 은 2026-09-06 검증 이력.
> 지시(2026-09-06): "매도도 무인에 넣으세요. 무인 매수/매도 허용을 옵션으로 만들고, 설정화면에서 이 부분을 허용해야만 동작하도록 설계하세요. 매수/매도 각각 옵션."

## 1. 흐름 (ADR-009, 2026-09-08 단일 실행)

```
설정 › 무인 실행   증권사 계좌별 플래그 {무인 매수, 무인 매도, 하루 매수 상한 %} — 유일한 스위치. 끄면 그 방향의 오늘 미체결 무인 주문 즉시 취소
~09:00           주문표 = 그 순간의 원장(입출금·체결)으로 실시간 계산 (조회마다 스냅샷 갱신). 상단 상태: 🤖 무인 매수·매도 대기 — {실행일} 09:01 발주 예정
09:01  워커 auto_execute_open → 국내 포트별 (app/autoexec.py run_auto_execution → _execute_portfolio):
         락(하루 1회) → 주문표 계산·동결 _portfolio_orders(force_freeze) → 실행일 ≠ 오늘이면 '기준일 불일치' 기록·발주 없음
         → 계좌 플래그 모두 꺼짐 = 수동 모드(기록만) · 정지 · 사용자 취소(skip) → 발주 없음
         → 줄마다 BrokerOrder(mode=auto) 생성, 꺼진 방향은 skipped('수동 처리')
         → 시가(현재가 TR stck_oprc, 최대 4회) ≤ gap_cancel_exact ? 그리드 매수 skipped_gap
         → 잔고 조회: 원장 보유(200 ETF·레버리지) ≠ 계좌 → 전부 생략 + 정지 · 매도 수량 ≤ 보유
         → 매도(시장가 → 지정가) → 매수(시장가 레버리지 진입 → 얕은 그리드 → 깊은 그리드):
              하루 매수 상한(총자산 × %)에 맞춰 수량 축소 → 매수가능조회 수량에 맞춰 축소(0 이면 생략; 조회 실패 시 예수금 누적 폴백)
              → place_order(지정가 ORD_DVSN 00 / 시장가 01) → submitted(order_no) | skipped | failed
         → last_run 요약·활동 로그·알림. 연속 실패 2회 → paused
09:15  워커 auto_exec_watchdog → 실행 기록 없는 포트를 지연 실행(락·마커로 중복 없음) + 경고(trigger=watchdog)
매 60초 pipeline_heartbeat → Redis autoexec:pipeline:heartbeat(TTL 180초) — scheduler·worker 헬스체크가 이 키를 본다
15:45/17:10  broker_post_close_sync → 체결 가져오기 → 무인 주문 상태 확정(filled/partial/unfilled) → 계획·체결 대조(unplanned·excess 면 정지) → 예수금 대조
화면  주문표 위 상태 한 줄(수동/대기/실행 중/완료/취소됨/정지/경고 + 사유) · 버튼은 [이번 실행일 무인 취소 → 수동]·[되돌리기]·[다시 켜기]만 · 표의 '무인' 열은 줄별 최종 상태
```

폐지(2026-09-08): 16:45 자동 승인 · 08:40 보완 · 08:57 사전 갭 취소(`app/preopen.py` 삭제) · 승인(`approved`) 단계 · 시장가 예약 접수 · 포트별 완전 무인 토글 · 전량 취소 · 주문표의 예약주문 접수 버튼(API `POST /orders/reserve` 만 잔존).

## 2. 코드 지도

| 영역 | 위치 | 내용 |
|---|---|---|
| 정책·실행 | `app/autoexec.py` | 설정 GET/PUT `/settings/auto-exec`(기본값 + 일괄), 계좌별 `PUT /settings/auto-exec/accounts/{aid}` — `account_auto_exec(cred)` 가 유일한 판정 함수, 끈 방향은 `_cancel_for_turned_off`. 상태 `GET /portfolio/{pid}/auto-exec?date=` (`auto_exec_view` → `state`), 취소 `POST …/auto-exec/skip {date}` · `unskip`, 해제 `resume`. 실행 `run_auto_execution`(09:01) · `run_watchdog`(09:15) · `touch_heartbeat`/`heartbeat_age`, 확정 `sync_auto_orders`, 정지 `pause_if_reconcile_warns` |
| 계획 | `app/signals.py` | `freeze_at(exec_day)`(09:00 KST) · `_state_before(cutoff: date \| datetime)` · `_portfolio_orders(force_freeze, now)` — 동결 전 실시간 갱신, `frozen_at` 뒤 스냅샷 반환(`frozen`), `gap_cancel_exact` 노출 · `_next_exec_day(base_day, session)` 캘린더 휴장 스킵 |
| KIS | `services/kis_client.py` `place_order(code, side, qty, price \| None)` / `cancel_order` / `buyable` | 지정가(ORD_DVSN 00)·시장가(01, 단가 0). 실전 TTTC0012U/0011U/0013U/8908R, 모의 VTTC0802U/0801U/0803U/8908R |
| 기록 | `models.py` `BrokerOrder.mode=auto`(09:01 에 생성, 최종 상태만), `BrokerCredential.auto_exec {buy, sell, daily_buy_cap_pct}`, `TradePortfolio.params.auto_exec {paused…, last_run, skip}` | 스키마 변경 없음(JSONB) |
| 워커 | `worker.py` | `auto-exec-open` 09:01 · `auto-exec-watchdog` 09:15 · `pipeline-heartbeat` 60초 (mon–fri 는 앞 둘만). `max_retries=0`, 휴장일 스킵 |
| 헬스체크 | `docker-compose.yml` worker·scheduler | 하트비트 키 존재 확인(`start_period` 150초) — 종전 `import app.worker` 는 2026-09-08 미실행을 잡지 못했다 |
| 훅 | `broker.py` | 주문 목록 응답 `auto_exec`(실행일 기준 state), `run_post_close_sync` 가 확정·정지 |
| 알림·로그 | `app/notify.py` 카테고리 7종(autoexec·paused·sync·cash·orders·trades·daily), `app/activity.py` 종류 라벨(폐지 종류는 '(구)') | `autoexec.run`(지연 실행은 warn)·`autoexec.skip`·`autoexec.cancel`·`autoexec.account_setting` |
| 웹 | `portfolio/page.tsx`, `settings/page.tsx` | 상태 배너 + 취소/되돌리기/다시 켜기 · '무인' 열(읽기 전용) · 제목의 동결/실시간 표기 / 계좌별 플래그 2개 + 상한 입력, 켬·끔 확인창(끄면 즉시 취소 안내) |
| 챗봇 | `app/chat.py` `auto_exec_status` | 계좌 플래그·포트별 `state`·`skip`·마지막 실행·살아 있는 주문 수 |

## 3. 상태 흐름 (BrokerOrder.mode=auto)

09:01 에 줄마다 생성되어 그 자리에서 최종 상태가 정해진다: `submitted`(order_no) → (15:45) `filled` | `partial` | `unfilled` · `skipped_gap`(갭 취소) · `skipped`(꺼진 방향·시가 미확인·잔고/대조·상한 0·주문가능 0) · `failed`(KIS 오류).
`submitted`/`partial` → `cancelled`: 사용자 취소(skip, 09:01 후) · 설정 해제(`_cancel_for_turned_off`). 축소 발주는 `submitted` 에 메시지("상한으로 5→4주", "주문가능 수량에 맞춰 3→1주")와 `response.plan_qty`.
`approved`·`reserved`·`gap_cancelled` 는 2026-09-08 이전 행에만 남는다(화면 '(구)').

## 4. 테스트 (`tests/test_autoexec.py` · `test_account_autoexec.py` · `test_autoexec_review.py` · `test_signals.py`)

- 플래그 기본값·일괄·계좌별·상한(0~100)·상속·타 사용자 404 · 상태 off → waiting · 승인/전량 취소/완전 무인 엔드포인트 404.
- 단일 실행: 갭 발생 → 그리드 2건 `skipped_gap`, 익절 매도(지정가) + 레버리지 진입(시장가 `price=None`) 발주(매도 먼저), 상태 `ran`(건수·시가), 09:03 재실행·09:15 감시 모두 already-ran, 15:45 확정 filled/unfilled.
- 축소: 총자산 200만·상한 20% → grid1 5→4주, grid2 생략, 매도 꺼짐 → '수동 처리' · 주문가능현금 60만 → grid1 5주, grid2 3→1주 · 예수금 폴백 60만/20만 · 연속 실패 2회 → paused → resume.
- 수동 모드(기록 없음) · 기준일 불일치(발주 없음, error 로그) · 사용자 취소(09:00 전 건너뜀·되돌리기·지난 날 409 / 09:01 후 살아 있는 주문 취소·되돌리기 409) · 설정 해제 → 그 방향만 즉시 취소 + 로그.
- 감시: 09:01 기록 없음 → 지연 실행, `last_run.trigger=watchdog`, warn 로그 "지연 실행".
- 계좌 플래그: 다른 계좌를 켜도 수동, 연결 계좌 매수만 켬 → 매수 줄만 발주. 원장 vs 계좌 불일치 → 전부 생략 + 정지. 대조 경고는 unplanned·excess 만 정지.
- 동결 규칙(`test_signals.py`): 실행일 08:30 입금 즉시 반영·09:30 입금 미반영·force_freeze 뒤 스냅샷 고정·캘린더 휴장 스킵.

## 5. 운영 절차

1. 설정 › 무인 실행에서 계좌의 **무인 매수·매도** 플래그를 켠다(확인창). 하루 매수 상한(기본 20%)을 정한다. 이것이 유일한 스위치다.
2. **모의투자 계좌로 먼저**: 모의(vps) 계좌를 포트에 연결하고 플래그를 켠 뒤 다음 거래일 09:01 결과(주문표 상태 한 줄·표의 '무인' 열·매매 로그)를 본다. 실계좌 소액 → 정상 운용.
3. 매일: 09:00 전에 입출금·체결이 원장에 있는지 확인(그날 수량에 바로 반영). 09:01 이후 주문표 상단 "✅ 무인 … 완료 — 발주 n건 …" 을 본다. "⚠️ 09:01 실행 기록 없음"이면 09:15 감시를 기다리고, 그 뒤에도 없으면 `docker compose ps`(하트비트 헬스체크)·워커 로그를 본다.
4. 오늘만 손으로 하려면 주문표의 **[이번 실행일 무인 취소 → 수동]** — 09:00 전이면 발주를 건너뛰고(되돌리기 가능), 09:01 후면 살아 있는 무인 주문을 취소한다. 다음 실행일에 자동 복귀.
5. 정지 배너(연속 실패·대조 불일치)는 사유를 확인하고 계좌·기록을 맞춘 뒤 "다시 켜기".
6. 배포 시: 2026-09-08 이전의 `approved` 행은 실행기가 읽지 않는다 — `UPDATE broker_orders SET status='cancelled', message='ADR-009 전환 정리' WHERE status='approved'` 로 정리한다. 거래일 캘린더(`trading_calendar`)를 최신으로 유지한다(휴장 미등록이면 그날 09:01 이 '시가 확인 실패', 다음 날 '기준일 불일치'로 발주하지 않는다).

## 5-1. 2차 검증 (2026-09-06, 사용자 지시 "논리·절차 오류 검토") — 고친 3건

| # | 발견 | 수정 |
|---|---|---|
| A | 예약주문 중복 검사가 `reserved` 만 봐서 무인 승인 줄을 예약으로도 접수 가능 → 09:00 + 09:01 이중 발주 | 예약 접수도 `approved/submitted/partial` 을 중복으로 판정(승인 쪽은 이미 예약을 막고 있었음) |
| B | 매수 합계 > 예수금이면 그리드 전부 생략 — 백테스트는 grid1 부터 순차 체결 | 높은 지정가(얕은 그리드)부터 누적액 ≤ 예수금인 줄만 발주, 넘치는 줄만 생략 |
| C | 장 마감 대조의 모든 `warn` 이 정지 사유 — 지정가 부분체결도 `warn` 이라 거의 매일 정지 | `reconcile_plan` 에 `kind` 추가(level·text 불변), 정지는 `unplanned`·`excess` 만 |

후속(사용자 질문 "최종 주문 전 조건 재확인은 의미 없나?" → 두 가지만 의미 있음, 구현):

| # | 추가한 사전 확인 (09:01, 발주 직전) | 불일치 시 |
|---|---|---|
| 1 | **원장 vs 계좌 대조** — 앱 원장의 200 ETF·레버리지 보유 수량과 계좌 잔고 비교 | 그날 전부 생략 + 포트 정지(사유에 종목·수량). 원장을 계좌에 맞춘 뒤 다시 켜기 |
| 2 | **계획 스냅샷 재대조** — 승인 행의 줄 키·수량이 그날 계획과 같은지 | 그 줄만 생략("실행 시점 재대조 실패") |

전략 조건(레짐·노출·그리드 가격) 재계산은 입력이 전날 종가라 결과가 같으므로 하지 않는다 — 밤새 바뀐 변수는 시가 하나이고 갭 취소가 그것을 본다.

검토했지만 유지한 것: 매도 대금은 T+2 라 당일 매수 한도에 넣지 않음(보수적) · 연속 실패 카운터는 날을 넘겨 누적, 성공 시 초기화 · 실행 도중 예외로 롤백돼도 Redis 락과 `plan_date=오늘` 조건 때문에 같은 날 재발주는 없고 원장은 15:45 체결 가져오기로 맞음(BrokerOrder 행만 `approved` 로 남을 수 있음 — 다음 실행일에는 선택되지 않음).

## 6. 알려진 한계·주의

- 09:01 발주라 09:00:00~09:01:00 사이의 체결 기회는 놓친다(1분). 그리드 지정가는 시가 아래에 있어 대부분 영향 없다. 09:15 지연 실행이면 14분.
- 시가 조회가 09:01 에도 0 이면(휴장·지연) 4회(30초) 기다린 뒤 그날 발주를 생략하고 사유를 남긴다. 캘린더에 없는 휴장이면 다음 거래일 09:01 은 '기준일 불일치'(계산된 실행일이 어제)로 발주하지 않는다 — 캘린더 갱신이 선결.
- 매수 한도는 발주 직전 KIS 매수가능조회(주문가능현금·미수 없는 수량)로 판정한다. 조회가 실패한 날만 예수금 총액(`dnca_tot_amt`, D+2 기준) 누적 규칙으로 물러나며 그때는 당일 매도 대금이 반영되지 않아 보수적이다.
- 하루 1회 락은 Redis 기준이며, Redis 가 없으면 `last_run.date` 마커로 대신한다.
- 대조 경고로 정지되면 그날 이미 발주된 주문은 취소하지 않는다(사용자 판단). 배너에서 확인 후 해제.
- 미국 포트는 대상 외(수동). 시장가 줄(레버리지 진입·청산)은 2026-09-08 부터 09:01 시장가 무인 발주 — 시가 급변 시 체결가 통제 불가(사용자 결정).
- **사전 갭 취소의 한계**(2026-09-06 밤): 예상체결가는 동시호가 호가 잔량으로 계산한 근사값이다 — 08:57 예상이 기준 아래여도 09:00 실제 시가가 위로 열리면 그날 그리드 매수를 놓친다(손실 아님). KIS 예약주문이 정규 주문으로 전송되는 시각이 08:57 보다 늦으면 미체결 목록에 없어 취소하지 못하고 '취소 불가'로 기록된다(실계좌 첫 주에 확인할 것). 미체결 조회 TR 은 실전 전용이라 모의 계좌는 대상 외. 대상은 **오늘 계획의 그리드 지정가와 정확히 같은 200 ETF 매수**뿐이라 사용자가 다른 가격으로 넣은 주문은 건드리지 않는다.
- **예수금 대조**(2026-09-06, `app/cashcheck.py`): 15:45/17:10 동기화가 체결을 가져온 뒤 원장 현금과 계좌 **D+2 예수금**(가수도정산금액)을 비교해 `params.cash_check` 에 저장한다. 허용 오차(1만원 또는 총자산 0.1% 중 큰 값 — 수수료·분배금 범위)를 넘으면 주문표 위에 경고 배너. **자동 수정·자동 정지 없음** — "차액을 입출금으로 등록" 버튼 한 번으로 원장을 계좌에 맞춘다(오늘 대조 결과이고 대조 이후 원장이 안 바뀐 경우만). 주문표(그리드 수량)는 원장 현금으로 계산되므로 차이가 크면 매수 수량이 실제와 어긋난다 — 발주 자체는 매수가능조회가 막아 주지만 계획은 틀어진다. 시작 패널의 "계좌에서 불러오기"로 처음부터 계좌 값(D+2 예수금·전략 종목 보유)으로 시작할 수 있다.
