# 무인 실행 — 승인된 지정가를 09:01 시가 확인 후 자동 발주 (2026-09-06)

> 정책 근거: [ADR-008](../adr/008-controlled-auto-execution.md). 이 문서는 구현·운영 지도다.
> 지시(2026-09-06): "매도도 무인에 넣으세요. 무인 매수/매도 허용을 옵션으로 만들고, 설정화면에서 이 부분을 허용해야만 동작하도록 설계하세요. 매수/매도 각각 옵션."

## 1. 흐름

```
전날 16:40~     주문표 조회 → 줄 체크 → [🤖 무인 실행 승인] → BrokerOrder(mode=auto, status=approved)
                (설정에서 그 방향이 허용돼 있어야 버튼이 동작. 시장가 줄은 제외 → 예약주문으로)
전날 16:45      [완전 무인, 2026-09-07 지시] 워커 auto_approve_plan (app/autoapprove.py) → 자동 승인이 켜진 국내 포트별:
                  정지 상태·설정 모두 꺼짐이면 건너뜀(로그) → 다음 실행일 주문표 계산·스냅샷 저장(_portfolio_orders)
                  → 실행일 ≤ 오늘(오늘 일봉 미적재)이면 건너뜀 → 하루 매수 상한(총자산 대비 %, 기본 20%) 초과면 승인 없이 정지
                  → 허용 방향의 지정가 줄 approved(자동 승인) · 시장가 줄은 옵션이면 예약주문 접수(실전·접수 창 안), 아니면 '수동 필요' 로그
                  → params.auto_exec.auto_approve_last + 활동 로그. 이미 살아 있는 줄은 건너뜀(멱등)
                긴급 정지: 화면 [⛔ 무인 중지 + 전량 취소] → POST …/orders/cancel-all {stop:true} → 승인 철회·예약 취소·정규 주문 취소 + 정지 + 자동 승인 끔
실행일 08:57    워커 preopen_gap_cancel (app/preopen.py, 2026-09-06 밤 지시 — 취소만 무인) → 오늘 계획이 있는 국내 포트별:
                  설정 auto_exec.preopen_cancel(기본 켜짐) → 200 ETF 예상체결가(FHKST01010200 antc_cnpr, 최대 3회 재시도)
                  → 예상체결가 ≤ gap_cancel_exact ? KIS 정정취소가능(미체결) 주문 중 200 ETF 매수·오늘 그리드 지정가와 같은 것 취소(TTTC0013U)
                    (앱 예약주문 → BrokerOrder gap_cancelled · HTS 직접 주문 → 로그에 '앱 밖 주문' · 미체결 목록에 없는 예약주문 → '취소 불가' 기록)
                  → params.preopen_cancel.last_run + 활동 로그. 무인 승인 줄(auto)은 건드리지 않음 — 09:01 이 실제 시가로 판정
실행일 09:01    워커 auto_execute_open → 포트별:
                  락(하루 1회) → 정지 상태·설정 스위치 재확인 → 당일 시가 조회(현재가 TR stck_oprc, 최대 4회 재시도)
                  → 시가 ≤ gap_cancel_exact ? 그리드 매수 skipped_gap
                  → 잔고 조회: 원장 대조(200 ETF·레버리지 보유 = 계좌 잔고, 아니면 전부 생략+정지), 매도 수량 ≤ 보유
                  → 매도 먼저 place_order → 매수는 얕은 그리드부터, 줄마다 발주 직전 buyable(매수가능조회) 로 가능 수량 ≥ 계획 수량이면
                    place_order(지정가), 부족하면 그 줄 생략(수량 축소 없음) · 조회 실패 시 예수금 총액 누적 규칙으로 폴백
                  → submitted(order_no) / skipped / failed
                  → last_run 요약 저장, 연속 실패 2회면 paused
15:45 / 17:10   run_post_close_sync → sync_auto_orders(당일 체결조회로 filled/partial/unfilled 확정)
                → reconcile_for_portfolio 에 warn 이 있으면 pause_if_reconcile_warns
화면            주문표: 승인 버튼·확인창, 줄별 상태(무인 승인/무인 발주/갭 취소 생략/생략/체결…), 마지막 실행 요약, 정지 배너(다시 켜기)
                설정 › 무인 실행: 매수 허용 / 매도 허용 토글(켤 때 확인창)
```

## 2. 코드 지도

| 층 | 위치 | 내용 |
|---|---|---|
| 정책·실행 | `app/autoexec.py` | 설정 GET/PUT `/settings/auto-exec`, 승인 `POST /portfolio/{pid}/orders/approve`, 상태 `GET /portfolio/{pid}/auto-exec`, 해제 `POST …/auto-exec/resume`, `run_auto_execution`, `sync_auto_orders`, `pause_if_reconcile_warns` |
| KIS | `services/kis_client.py` `KisTradingClient.place_order / cancel_order / buyable` | 실전 TTTC0012U(매수)·TTTC0011U(매도)·TTTC0013U(취소)·TTTC8908R(매수가능조회), 모의 VTTC0802U·VTTC0801U·VTTC0803U·VTTC8908R. 지정가만 |
| 기록 | `models.py` `BrokerOrder.mode`('reserve'/'auto'), `UserSettings.auto_exec`, `TradePortfolio.params.auto_exec` | 마이그레이션 0021 |
| 훅 | `broker.py` | `STATUS_KO` 확장, 주문 목록 응답에 `auto_exec`, 취소 엔드포인트가 무인 줄 처리(승인 철회 / 정규 주문 취소), `run_post_close_sync` 가 확정·정지 |
| 계획 | `signals.py` | `PortfolioPlan.payload.gap_cancel_exact`(정확값) 추가 — 시가 판정용 |
| 워커 | `worker.py` | `auto-exec-open` 09:01 mon–fri, `max_retries=0`, 휴장일 스킵 · `preopen-gap-cancel` 08:57 mon–fri (2026-09-06 밤) |
| 완전 무인 | `app/autoapprove.py` `run_auto_approve`, `PUT /portfolio/{pid}/auto-exec/auto-approve`, `POST /portfolio/{pid}/orders/cancel-all`, `worker.py` `auto-approve-plan` 16:45 | 포트별 자동 승인(기본 꺼짐)·시장가 예약 접수 옵션(기본 켬)·하루 매수 상한 `daily_buy_cap_pct`(총자산 대비 %, 기본 20, 0 = 없음). 전량 취소(승인·예약·발주) + stop 이면 정지·자동 승인 끔. 상태는 `auto_exec_view` 의 `auto_approve`·`auto_approve_last` |
| 사전 갭 취소 | `app/preopen.py` `run_preopen_cancel`, `services/kis_client.py` `fetch_expected`(FHKST01010200)·`list_open_orders`(TTTC0084R 실전 전용) | 예상체결가 ≤ 기준 → 그리드 가격과 같은 200 ETF 매수 미체결 취소. `BrokerOrder.status=gap_cancelled`, `params.preopen_cancel.last_run`. 설정 `auto_exec.preopen_cancel` 기본 켜짐 |
| 알림 | `app/notify.py` `notify_event`(활동 로그 훅)·`notify_trade`·`send_daily_status`, `GET/PUT /settings/notify`, `POST /settings/notify/test`, `user_settings.telegram_bot_token`(🔒)·`telegram_chat_id`·`notify`(0023) | 텔레그램 Bot API sendMessage/getUpdates. 카테고리 9종(기본: 결과·경고 켬, 주문·체결 등록 꺼짐). 실패는 `notify.failed` 로그만 — 본 작업 계속 |
| 로그 | `app/activity.py` `log_event`, `GET /logs`, `models.ActivityLog`(0022) | 거래(원장)·주문(BrokerOrder)·이벤트(ActivityLog) 병합. 기록 지점: 무인 실행 요약·정지·승인, 예약주문 접수·취소, 사전 갭 취소, 장 마감 동기화 결과·오류, 예수금 대조 경고·보정, 거래 삭제 |
| 웹 | `portfolio/page.tsx`, `settings/page.tsx`, `logs/page.tsx` | 승인 버튼·확인창·상태·배너·사전 갭 확인 한 줄 / 무인 실행 탭(매수·매도·사전 갭 취소 스위치) / 매매 로그(기간·유형·수준·포트·검색 필터) |

## 3. 상태 흐름 (BrokerOrder.mode=auto)

`approved` → (09:01) `submitted` → (15:45) `filled` | `partial` | `unfilled`
`approved` → `skipped_gap`(갭 취소) | `skipped`(설정 꺼짐·시장가·시가 미확인·예수금/잔고 부족·정지) | `failed`(KIS 오류)
`approved` → `cancelled`(사용자 승인 철회, KIS 호출 없음) · `submitted` → `cancelled`(정규 주문 취소 TR)
(mode=reserve) `reserved` → (08:57 사전 갭 취소) `gap_cancelled` — 예상체결가 ≤ 기준일 때 미체결 목록에서 찾아 취소한 그리드 매수. 못 찾으면 `reserved` 유지 + 메시지 '취소 불가'

## 4. 테스트 (`tests/test_autoexec.py`)

- 설정 꺼짐 → 승인 409, 매수만 켬 → 매도 줄·시장가 줄 409, 승인 2건, 중복/불일치 판정, 목록의 `auto_exec`, 승인 철회.
- 실행 ①: 갭 발생 → 그리드 2건 `skipped_gap`, 익절 매도 1건 발주(매도 먼저), 같은 날 재실행 차단, 15:45 확정 `filled`.
- 실행 ②: 갭 없음·예수금 부족 → 매수 생략, 보유 0 → 매도 생략; 발주 연속 실패 2회 → `paused`, 승인 거절, `resume` 로 해제.
- 미국 포트 승인 409, 대조 경고로 정지.

## 5. 운영 절차

1. 설정 › 무인 실행에서 매수·매도 허용을 켠다(각각). 확인창의 통제 규칙을 읽는다.
2. **모의투자 계좌로 먼저**: 설정 › 증권사 계좌에 모의(vps) 계좌를 등록해 포트에 연결 → 주문표에서 1주 승인 → 다음 날 09:01 결과를 주문표에서 확인. (모의는 정규 주문 TR 을 지원한다. 예약주문은 미지원)
3. 실계좌 소액 → 정상 운용. 각 단계는 사용자 확인 후.
4. 매일: 장 마감 후 주문표에서 줄을 체크해 승인(승인은 09:00 전까지 철회 가능). 09:01 이후 주문표의 "🤖 무인 실행" 요약과 표의 상태를 본다.
5. 정지 배너가 뜨면 사유(연속 실패/대조 경고)를 확인하고 계좌·기록을 맞춘 뒤 "다시 켜기".
6. **완전 무인(2026-09-07)**: 주문표 위 "🤖 완전 무인 운영 › 설정"에서 자동 승인을 켠다(시장가 줄 예약 접수·하루 매수 상한 선택). 이후 매일 승인 없이 16:45 자동 승인 → 09:01 발주. 매매 로그의 "자동 승인"·"무인 실행" 이벤트와 정지 배너를 하루 한 번은 확인한다(사람이 보지 않는 운영이라 '정지된 채 모르는 상태'가 가장 큰 위험). 문제가 보이면 [⛔ 무인 중지 + 전량 취소]로 살아 있는 주문을 모두 거두고 정지한다 — 체결된 것은 취소되지 않으니 반대 매매로 정리한다. 표의 승인·발주 줄은 개별 "취소"로도 거둘 수 있다.

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

- 09:01 발주라 09:00:00~09:01:00 사이의 체결 기회는 놓친다(1분). 그리드 지정가는 시가 아래에 있어 대부분 영향 없다.
- 시가 조회가 09:01 에도 0 이면(휴장·지연) 4회(30초) 기다린 뒤 그날 발주를 생략하고 사유를 남긴다 — 다음 날 다시 승인해야 한다.
- 매수 한도는 발주 직전 KIS 매수가능조회(주문가능현금·미수 없는 수량)로 판정한다. 조회가 실패한 날만 예수금 총액(`dnca_tot_amt`, D+2 기준) 누적 규칙으로 물러나며 그때는 당일 매도 대금이 반영되지 않아 보수적이다.
- 하루 1회 락은 Redis 기준이며, Redis 가 없으면 `last_run.date` 마커로 대신한다.
- 대조 경고로 정지되면 그날 이미 발주된 주문은 취소하지 않는다(사용자 판단). 배너에서 확인 후 해제.
- 미국 포트·시장가 줄은 대상 외(예약주문/수동).
- **사전 갭 취소의 한계**(2026-09-06 밤): 예상체결가는 동시호가 호가 잔량으로 계산한 근사값이다 — 08:57 예상이 기준 아래여도 09:00 실제 시가가 위로 열리면 그날 그리드 매수를 놓친다(손실 아님). KIS 예약주문이 정규 주문으로 전송되는 시각이 08:57 보다 늦으면 미체결 목록에 없어 취소하지 못하고 '취소 불가'로 기록된다(실계좌 첫 주에 확인할 것). 미체결 조회 TR 은 실전 전용이라 모의 계좌는 대상 외. 대상은 **오늘 계획의 그리드 지정가와 정확히 같은 200 ETF 매수**뿐이라 사용자가 다른 가격으로 넣은 주문은 건드리지 않는다.
- **예수금 대조**(2026-09-06, `app/cashcheck.py`): 15:45/17:10 동기화가 체결을 가져온 뒤 원장 현금과 계좌 **D+2 예수금**(가수도정산금액)을 비교해 `params.cash_check` 에 저장한다. 허용 오차(1만원 또는 총자산 0.1% 중 큰 값 — 수수료·분배금 범위)를 넘으면 주문표 위에 경고 배너. **자동 수정·자동 정지 없음** — "차액을 입출금으로 등록" 버튼 한 번으로 원장을 계좌에 맞춘다(오늘 대조 결과이고 대조 이후 원장이 안 바뀐 경우만). 주문표(그리드 수량)는 원장 현금으로 계산되므로 차이가 크면 매수 수량이 실제와 어긋난다 — 발주 자체는 매수가능조회가 막아 주지만 계획은 틀어진다. 시작 패널의 "계좌에서 불러오기"로 처음부터 계좌 값(D+2 예수금·전략 종목 보유)으로 시작할 수 있다.
