# Feature: 실전매매 기록

## 1. 목적

수동 매수 등록 기반으로 매수 시점 기준 수익률을 추적하고, 백테스트에서 전환된 실전 포트의 성과를 관리한다.

## 2. 범위

### 포함 범위

- 매수/매도 등록(종목·수량·단가·일시, 차트 클릭 입력), 로트 FIFO 원장 + 평단 병기, 현금 입출금 등록
- 수익률 카드(비용 포함/제외 토글, 보유일수·연환산·최고최저 도달·목표/손절 진행 바)
- 계좌 수준 실현/미실현 손익·TWR·XIRR, 매매일지 메모·태그
- 백테스트→전환 포트(`backtest_id` 링크, 기대 성과 대비)

### 제외 범위

- 증권사 계좌 연동·자동 체결 반영(v1 수동 입력), 목표·손절 도달 푸시 알림(후순위)

## 3. 사용자 시나리오

주문표대로 HTS에서 체결 → 체결 내역을 매수 등록(또는 차트에서 클릭) → 수익률 카드에서 평단·수익률·목표 진행 확인 →
매매일지에 메모 → 대시보드에서 백테스트 기대 성과와 대비.

## 4. 최종 합의안

회계를 로트 FIFO로 통일(전략 엔진·백테스트와 동일)하고, XIRR의 전제인 입출금 등록을 포함 범위로 확정했다.

### 검토 요약 (감사용)

- 참여 Agent: [Backend/Quant Engineer](../personas/backend-engineer.md), [Security](../personas/security.md), [Product Manager](../personas/product-manager.md)
- 핵심 쟁점과 결론: 평단법 vs FIFO — 백테스트와 실전이 같은 매매에 다른 손익을 보이면 안 되므로 FIFO 통일, 화면은 평단 병기.
  XIRR은 입출금 없이는 계산 불가 → 입출금 등록을 범위에 추가. 암호화 대상은 수량·단가·금액(종목 코드는 평문).
- 남은 쟁점: 없음
- 검토 로그: [discussion/review-portfolio-20260828.md](../discussion/review-portfolio-20260828.md)

## 5. 기능 요구사항

- 등록: 매수·매도·입금·출금 4종 거래. 매도는 FIFO 자동 매칭 → 실현손익 산출. 평단은 표시용으로 병기.
- 수익률: 종목 `= (현재가 − 평단) / 평단`, 비용 포함 토글 시 수수료·세금 반영. 연환산은 보유 30일 미만 미표시(극단값 방지).
- 계좌: TWR(일별 체인), XIRR(입출금 현금흐름 기반). 목표가·손절가는 포지션별 설정, 진행 바 표시.
- **손익 비율 이중 기준 (2026-09-02 사용자 결정)**: 순손익% = 순손익 ÷ **납입원금(입금 − 출금)**, 평가손익% = 평가손익 ÷ **보유원가**. 분모 ≤ 0(원금 초과 출금·전량 매도 등 정상 경로)이면 **%만 null, 금액은 항상 표시**. 카드에 "금액 (±%)" 병기.
- **거래 등록·삭제 시 당일 자산 스냅샷 즉시 재계산** ([ADR-008](../adr/008-portfolio-snapshots.md), feature-dashboard §5) — 대시보드 추이의 유령값 방지.
- **오늘의 주문표 = 실행일 09:00 KST 직전 상태의 함수 (2026-09-02 B안 → 2026-09-08 [ADR-009](../adr/009-unattended-single-execution.md) 시각 동결)**: 포트 기준 주문표는 실행일(exec_day) **09:00 이전에 등록된 거래(입출금·체결)만**으로 로트·현금을 시점 재생해 계산한다. 09:00 전 등록은 즉시 반영(자본 입출금이 그날 수량에 반영 — 사용자 지시 "수량 실시간 반영"), 09:00 이후 등록은 다음 주문표부터. 09:01 무인 실행기가 같은 기준으로 계산·발주하고 `portfolio_plans.payload.frozen_at` 을 찍어 **동결**하면 이후 화면은 그 스냅샷을 그대로 보인다(`frozen: true`) — HTS 주문장 = 화면. 동결되지 않은 실행일(실행기 미실행)은 09:00 이후 스냅샷을 갱신하지 않되 화면은 재계산값(2026-09-02 "그날 아침의 계획 보존"). 기준일 이전 소급 등록은 반영(자가 치유). 미국 포트는 종전 날짜 기준(실행일 00:00) 유지.
- **무인 매매 (ADR-009)**: 발주 경로는 HTS 직접 또는 09:01 무인 실행뿐. 무인은 **증권사 계좌의 플래그** `auto_exec = {buy, sell, daily_buy_cap_pct}` 로만 켜고 끈다(승인 단계·주문표 버튼 없음). 주문표 상단에 상태 한 줄(수동/대기/실행 중/완료/취소됨/정지/경고)과 '이번 실행일 무인 취소 → 수동'(09:00 전 건너뛰기·되돌리기 / 09:01 후 살아 있는 주문 취소) 버튼만 둔다. 설정에서 플래그를 끄면 그 방향의 오늘 미체결 무인 주문을 즉시 취소한다(사용자 결정).
- **시작 등록 시각**: "보유분 입력하고 시작"·시작 입금은 **직전 영업일 15:30 KST** 로 기록 — 등록 이전부터 보유하던 이력이며, 오늘 시각으로 기록하면 위 계약에 따라 당일 주문표에서 제외되기 때문.
- **계좌에서 불러오기 (2026-09-06 지시)**: 시작 패널에서 증권사 계좌를 고르면 `GET /broker/accounts/{aid}/balance` 로 잔고 요약을 받아 현금 칸에 **D+2 예수금**(가수도정산금액 — 원장 현금과 같은 정의), 보유분 행에 전략 종목(200 ETF·레버리지) 수량·평단을 **미리 채운다**. 자동 확정하지 않는다(계좌를 일지·다른 포트와 공유하면 예수금 전체가 이 전략 몫이 아닐 수 있음). `POST /portfolios` 의 `credential_id` 로 시작과 함께 계좌가 연결된다. 미국 포트는 연결만(국내 잔고 TR).
- **예수금 대조 (2026-09-06 지시, `app/cashcheck.py`)**: 연결된 국내 포트는 15:45/17:10 동기화가 체결을 가져온 뒤 원장 현금 vs 계좌 D+2 예수금을 비교해 `params.cash_check` 에 저장한다. 허용 오차 = max(1만원, 총자산 0.1%) — 수수료·분배금 범위. 초과 시 주문표 위 경고 배너(자동 수정·자동 정지 없음). "차액을 입출금으로 등록"은 오늘 대조 결과이고 대조 이후 원장이 안 바뀐 경우만 입금/출금 한 건(tags `cash_check`)으로 원장을 계좌에 맞춘다. 보정 거래는 TWR·XIRR 에 외부 현금흐름으로 잡힌다(수수료 차액은 손실이 아닌 출금으로 계산됨 — 소액이라 감내).
- 전환 포트: 백테스트 결과 화면의 버튼으로 생성, 파라미터·`backtest_id` 사본 보관.

## 6. 비기능 요구사항

- 현재가 갱신은 WS 구독, 카드 리렌더 시 열 흔들림 없음(tabular-nums).

## 7. 데이터 설계

- `portfolios(id, user_id, name, kind[manual|from_backtest], backtest_id?, params?)`.
- `transactions(id, portfolio_id, instrument_id?, kind[buy|sell|deposit|withdraw], qty🔒, price🔒, amount🔒, executed_at, memo, tags[])` — 🔒 = AES-GCM 암호화 필드.
- `lots(id, portfolio_id, instrument_id, qty_open🔒, price🔒, opened_at)` — FIFO 원장. 실현손익은 매도 시 계산·저장🔒.
- 공통 규칙 [ARCHITECTURE §3](../ARCHITECTURE.md), 암호화 [ARCHITECTURE §6](../ARCHITECTURE.md).

## 8. API 설계

- `POST /positions`(거래 등록), `GET /portfolio/summary`, `GET /portfolios/{id}/positions`, `PATCH /positions/{id}`(목표·손절·메모).
- `GET /portfolio/summary` 확장(2026-09-02): `principal`(납입원금 = 입금−출금), `net_pnl`, `net_pnl_pct`(÷principal, 분모≤0→null), `unrealized_pnl_pct`(÷보유원가, 원가 0→null), `invested_cost`(보유원가) 필드 추가 — 기존 키 비파괴.
- 무인 매매 계좌별 플래그(2026-09-07, 0024; ADR-009 2026-09-08): `GET /settings/auto-exec` → {default, accounts[{…, auto_exec: {buy, sell, daily_buy_cap_pct}}]}, `PUT /settings/auto-exec {buy, sell, daily_buy_cap_pct?}` = 기본값 + 모든 계좌 일괄, `PUT /settings/auto-exec/accounts/{aid} {buy?, sell?, daily_buy_cap_pct?}`. 끈 방향의 오늘 살아 있는 무인 주문은 즉시 취소 — 응답에 `cancelled`·`failed`. `GET /portfolio/{pid}/auto-exec?date=` → {allowed, account, paused…, last_run, skip, exec_day, **state{code,label,detail}**}. `POST /portfolio/{pid}/auto-exec/skip {date}`(이번 실행일 무인 취소 — 09:01 후면 살아 있는 주문 취소, 응답 cancelled/failed) · `POST …/auto-exec/unskip`(09:00 전만) · `POST …/auto-exec/resume`. `GET /portfolio/{pid}/orders?date=` 응답 `auto_exec` 에 같은 view. `/signals/daily` 응답에 `frozen`·`frozen_at`.
- 폐지(2026-09-08, ADR-009): `POST /portfolio/{pid}/orders/approve`, `PUT/POST …/auto-exec/auto-approve[/run-now]`, `POST …/orders/cancel-all`, 설정 `preopen_cancel`, 응답 `preopen`. `POST /portfolio/{pid}/orders/reserve`(예약주문)는 API 만 남고 화면 버튼은 제거.
- 텔레그램 알림(2026-09-07): `GET /settings/notify` (enabled·has_token·token_masked·chat_id·ready·events·categories), `PUT /settings/notify {enabled?, bot_token?, clear_token?, chat_id?, events?}`, `POST /settings/notify/test` (chat_id 자동 확인 + 테스트 메시지). 발송 지점: `activity.log_event` 훅(KIND_TO_CATEGORY), `register_transaction`(체결 등록), `daily_asset_snapshot`(일일 현황).
- 매매 로그(2026-09-06): `GET /logs?days=&portfolio_id=&type=all|trade|order|event&level=all|warn|error&q=&limit=` — 거래 원장·BrokerOrder·ActivityLog(0022) 병합, 최신순, KST 시각. 사전 갭 취소: `GET /portfolio/{pid}/orders` 응답에 `preopen.last_run`, 설정 `auto_exec.preopen_cancel`.
- 예수금 연동(2026-09-06): `GET /broker/accounts/{aid}/balance?market=` (잔고 요약: deposit·deposit_d1·deposit_d2·total_eval·holdings[strategy]), `POST /portfolios` 에 `credential_id`, `GET /portfolio/{pid}/cash-check[?refresh=true]`, `POST /portfolio/{pid}/cash-check/align`, `GET /portfolio/{pid}/broker` 응답에 `cash_check`.

## 9. UI/UX 설계

- 수익률 카드 그리드 + 종목 상세(앵커 탭 + sticky 현재가 헤더). 비용 포함/제외 토글 상시 노출([REQUIREMENTS §7](../SOURCES/REQUIREMENTS.md)).
- **수익률 추이 차트 (2026-09-08 지시 "109 는 다른 뜻처럼 보인다")**: 축·라벨·마지막 값을 TWR 지수(시작 100)가 아니라 **시작 대비 %**(+9.20%)로 표시 — **매매일지 수익률 차트와 같은 스타일**(축 포맷 `+9.2%`, 0% 점선, 격자·글꼴, 크로스헤어로 날짜·값). 평가액 툴팁은 사용자 지시로 제외. 데이터(`/portfolio/equity` 의 `index`)는 그대로, 화면에서 −100.
- **포트 선택 칩 (2026-09-08 지시)**: 선택 = 진한 바탕·흰 굵은 글씨·✓·강조 링, 비선택 = 연한 틴트·회색 글씨 — 한눈에 구분. 사용자 색은 선택 시 점, 비선택 시 20% 틴트.
- **계획 vs 체결 대조 표시 (2026-09-08 지시 "참고용은 이모티콘 + 롤오버, 문장은 핵심만")**: `warn`(계획에 없던 거래·초과 체결)만 ⚠️ 배너(한 줄 문구). `info`(지정가 미체결·부분 체결 = 정상, 2026-09-08 부분 체결도 참고로 내림)는 주문표 제목 옆 **ⓘ** 툴팁('425주 중 25주 체결')으로 — `reconcile.items[].label/plan/filled` 사용. 차이 없으면 표시 없음.
- **플래그 가시성 (2026-09-08 지시 "설정을 바꿔도 주문표에 변화가 없다")**: 상태 줄에 `매수 ON`·`매도 OFF` 배지와 "09:01 발주 예정 n줄 · 수동 m줄" 요약. 표의 '무인' 열은 실행 전에도 줄별로 `🤖 09:01 발주`(켜진 방향) / `수동`(꺼진 방향, 롤오버에 이유) / `수동(취소)` / `—`.
- **증권사 연동 잠금 (2026-09-08 지시)**: 계좌가 연결되면 선택 상자를 비활성(🔒 연결됨) — 체결 가져오기·무인 발주가 그 계좌에 묶여 실수로 바꾸면 원장이 섞이므로. 바꾸려면 확인창이 있는 '연결 해제' 뒤 다시 선택.

## 10. 보안 요구사항

- 수량·단가·금액 필드 AES-GCM 암호화 저장(DB 덤프에서 평문 미노출). 집계는 앱 레벨 복호 후 수행. 소유자 격리 필수.

## 11. 로그 / 분석 요구사항

- `portfolio_created_from_backtest` 이벤트(성공 지표 — 전환율 20%).

## 12. 테스트 시나리오

### 자동 테스트

- FIFO 매칭: 3단 분할매수 후 부분 매도 → 실현손익·잔여 로트 수기 대조. 평단 병기 값 일치.
- XIRR: 입금 2회 + 평가액 상승 고정셋 수기 대조, 입출금 미등록 시 XIRR 미표시. TWR 일별 체인 수기 대조.
- 암호화: DB 원시 조회로 qty/price/amount 평문 미검출. 비용 포함/제외 토글 계산.
- 연환산 30일 억제 경계(29일/30일).
- **주문표 시각 동결 (2026-09-02 B안 → ADR-009)**: 기준일 이전 입금·매수 → 주문표 A → 실행일 당일 10:05 체결 등록 → 주문표 == A (orders·account 불변) → 기준일 이전 소급 입금 → account.cash 증가(재생 갱신). 실행일 08:30 입금 → 즉시 반영, 09:30 입금 → 미반영 · `force_freeze`(09:01 실행기) 뒤에는 조회 시각과 무관하게 스냅샷(`frozen`) · 시점 재생과 등록 경로의 FIFO 동등성: cutoff=미래 재생 == 현재 로트 테이블 · `_next_exec_day` 는 캘린더 휴장 스킵 (`tests/test_signals.py`).
- **무인 매매 단일 실행 (ADR-009, `tests/test_autoexec.py`·`test_account_autoexec.py`·`test_autoexec_review.py`)**: 플래그 기본값·일괄·계좌별·상한·상속·격리 / 상태 off→waiting / 승인·전량 취소 엔드포인트 404 / 09:01 한 번에: 갭 → 그리드 생략·익절 매도(지정가)·레버리지 진입(시장가 price=None) 발주, 매도 먼저, 재실행·감시 중복 없음, 15:45 확정 / 상한·주문가능 수량은 축소(0 이면 생략), 꺼진 방향 '수동 처리', 연속 실패 2회 정지·다시 켜기 / 수동 모드(기록 없음)·기준일 불일치(오류 로그)·사용자 취소(09:00 전 건너뜀·되돌리기, 09:01 후 주문 취소)·설정 해제 즉시 취소 / 09:15 감시 지연 실행(trigger=watchdog) / 원장 vs 계좌 대조 불일치 정지 / 매수가능조회 폴백.
- **비율 분모 경계 3종 (2026-09-02)**: ① 입금 100만→익절→출금 120만(원금 −20만): `net_pnl_pct == null`·금액 정상 ② 입금 없이 매수만: `net_pnl_pct == null`, `unrealized_pnl_pct` 정상 ③ 전량 매도: `unrealized_pnl_pct == null`·평가손익 0·`net_pnl_pct` 실현 반영. 어느 경우도 500/0% 반환 금지.

### 수동 QA

- 차트 클릭 매수 등록 UX, 목표/손절 진행 바, 매매일지 태그 필터.

### 예외 케이스

- 보유 수량 초과 매도 거부, 과거 일시 소급 등록 시 FIFO 재계산, 상폐 종목 보유 시 처리 안내.

### 회귀 테스트 영향

- FIFO 회계는 전략 엔진·백테스트와 공유 개념 — 회계 규칙 변경 시 3개 기능 테스트 동시 확인.

## 13. 완료 조건

- §12 자동 테스트 실제 실행·전부 통과(green), [HISTORY.md](../HISTORY.md) 기록. 백테스트→전환→카드 표시 e2e 1회 통과.

## 14. 참고 ADR

[ADR-003](../adr/003-auth-jwt.md), [ADR-009](../adr/009-unattended-single-execution.md)

## 15. 미결정 사항

### 사용자 확인 필요

- 없음

### 기본값으로 진행한 사항

- 연환산 30일 억제, 암호화 대상 필드 — [ASSUMPTIONS.md](../ASSUMPTIONS.md)

### 후순위 검토 사항

- 목표·손절 브라우저 푸시 알림, 증권사 체결 내역 CSV 임포트
