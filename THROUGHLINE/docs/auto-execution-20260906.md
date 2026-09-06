# 무인 실행 — 승인된 지정가를 09:01 시가 확인 후 자동 발주 (2026-09-06)

> 정책 근거: [ADR-008](../adr/008-controlled-auto-execution.md). 이 문서는 구현·운영 지도다.
> 지시(2026-09-06): "매도도 무인에 넣으세요. 무인 매수/매도 허용을 옵션으로 만들고, 설정화면에서 이 부분을 허용해야만 동작하도록 설계하세요. 매수/매도 각각 옵션."

## 1. 흐름

```
전날 16:40~     주문표 조회 → 줄 체크 → [🤖 무인 실행 승인] → BrokerOrder(mode=auto, status=approved)
                (설정에서 그 방향이 허용돼 있어야 버튼이 동작. 시장가 줄은 제외 → 예약주문으로)
실행일 09:01    워커 auto_execute_open → 포트별:
                  락(하루 1회) → 정지 상태·설정 스위치 재확인 → 당일 시가 조회(현재가 TR stck_oprc, 최대 4회 재시도)
                  → 시가 ≤ gap_cancel_exact ? 그리드 매수 skipped_gap
                  → 잔고 조회: 매수 합계 ≤ 예수금, 매도 수량 ≤ 보유 (아니면 skipped)
                  → 매도 먼저, 매수 다음으로 place_order(지정가) → submitted(order_no) / failed
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
| KIS | `services/kis_client.py` `KisTradingClient.place_order / cancel_order` | 실전 TTTC0012U(매수)·TTTC0011U(매도)·TTTC0013U(취소), 모의 VTTC0802U·VTTC0801U·VTTC0803U. 지정가만 |
| 기록 | `models.py` `BrokerOrder.mode`('reserve'/'auto'), `UserSettings.auto_exec`, `TradePortfolio.params.auto_exec` | 마이그레이션 0021 |
| 훅 | `broker.py` | `STATUS_KO` 확장, 주문 목록 응답에 `auto_exec`, 취소 엔드포인트가 무인 줄 처리(승인 철회 / 정규 주문 취소), `run_post_close_sync` 가 확정·정지 |
| 계획 | `signals.py` | `PortfolioPlan.payload.gap_cancel_exact`(정확값) 추가 — 시가 판정용 |
| 워커 | `worker.py` | `auto-exec-open` 09:01 mon–fri, `max_retries=0`, 휴장일 스킵 |
| 웹 | `portfolio/page.tsx`, `settings/page.tsx` | 승인 버튼·확인창·상태·배너 / 무인 실행 탭 |

## 3. 상태 흐름 (BrokerOrder.mode=auto)

`approved` → (09:01) `submitted` → (15:45) `filled` | `partial` | `unfilled`
`approved` → `skipped_gap`(갭 취소) | `skipped`(설정 꺼짐·시장가·시가 미확인·예수금/잔고 부족·정지) | `failed`(KIS 오류)
`approved` → `cancelled`(사용자 승인 철회, KIS 호출 없음) · `submitted` → `cancelled`(정규 주문 취소 TR)

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
- 예수금은 D+2 기준(`dnca_tot_amt`) — 당일 매도 대금은 반영되지 않아 보수적으로 막는다.
- 하루 1회 락은 Redis 기준이며, Redis 가 없으면 `last_run.date` 마커로 대신한다.
- 대조 경고로 정지되면 그날 이미 발주된 주문은 취소하지 않는다(사용자 판단). 배너에서 확인 후 해제.
- 미국 포트·시장가 줄은 대상 외(예약주문/수동).
