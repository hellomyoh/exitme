# 검토 로그: 대시보드 자산 구분·포트별 추이·손익 비율 (2026-09-02)

실행 방식: `parallel-subagents`

서브에이전트 증거 (3기 병렬, 2026-09-02 12:20~12:25 KST 실행):

| 페르소나 | 실행 증거 | 입력 범위 | 출력 요약 |
|---|---|---|---|
| Backend/Quant | 완료 12:24, 도구 호출 8회, 100.9s | dashboard.py·portfolios.py·models.py + 설계 브리프 | 위험 5건 — 스냅샷 합산 불변식 부재, 납입원금 음수 경계, latest_close N+1, trend 페이로드, US 필터 계약화 |
| Database | 완료 12:24, 도구 호출 11회, 97.2s | models.py·crypto.py·alembic 최근본·dashboard.py | 위험 5건 — 포트 삭제 FK 위반 재발, upsert 경합+UTC/KST 불일치, 복호 비용, 통화 해석 위임, 하이퍼테이블 불필요 논증 |
| QA | 완료 12:26, 도구 호출 9회, 113.9s | dashboard.py·portfolios.py·test_dashboard.py·feature §12 | 위험 6건 — 거래 삭제 후 스냅샷 유령값, 소급 거래 왜곡, 분모 경계 3종, KR/US 혼재, 중복 적재, 기존 테스트 회귀·시간대 플래키 |

## 참여 페르소나와 선정 이유

- [Backend/Quant Engineer](../personas/backend-engineer.md) — 집계 로직·API 계약 영향
- [Database Engineer](../personas/database-engineer.md) — 신규 테이블(portfolio_snapshots) 스키마·마이그레이션
- [QA](../personas/qa.md) — 회귀·경계·테스트 추적 (feature-dashboard §12 확장)

## 페르소나별 검토

### Backend/Quant

- **위험 B1 — 사용자 스냅샷 ≠ Σ포트 스냅샷**: `compute_user_snapshot`이 float 누적 후 1회 반올림(dashboard.py:51-86), 포트별 별도 반올림·별도 시점 `latest_close` 조회 시 불일치. 실패 조건: `sum(portfolio_snapshots.equity WHERE market='KR') + other ≠ asset_snapshots.total`. 제안: **포트 스냅샷을 정수 원천으로 확정하고 사용자 스냅샷은 합산 유도**, 단일 트랜잭션.
- **위험 B2 — 납입원금 ≤ 0은 정상 경로**: 출금 가드는 현금 잔고만 검사(portfolios.py:135-139) — 이익 실현 후 원금 초과 출금 가능. 실패 조건: 입금 100만→익절→출금 120만에서 순손익% ≠ null 또는 부호 반전. 제안: 분모≤0→% null·금액은 표시 계약 명시.
- **위험 B3 — latest_close N+1**: 로트당 `ORDER BY desc LIMIT 1`(dashboard.py:64). 실패 조건: 10포트×20로트에서 쿼리 수 > 종목 수. 제안: `DISTINCT ON (instrument_id)` 일괄 조회 유틸.
- **위험 B4 — trend 다선 페이로드**: ALL=36500일 무제한 × 포트 수 × 암호화 3컬럼 복호. 제안: ALL은 주 단위 샘플, 응답은 기존 키 비파괴 + `series` 추가.
- **위험 B5 — US 센트 혼합**: `user_flows_between`·`compute_user_snapshot`의 market='KR' 필터(dashboard.py:34-35·57-58)와 신규 카드·trend의 필터 일관성. 실패 조건: US 포트($ 센트) 값이 KRW 시리즈·total에 합산되면 실패(~×10³ 왜곡). 제안: 응답에 `market`·`currency` 명시 + 계약 테스트.

### Database

- **위험 D1 — 포트 삭제 FK 위반 재발(심각)**: `delete_portfolio`가 자식 테이블 하드코딩 수동 삭제(portfolios.py:380-393), portfolio_plans(0008)와 동일 패턴 사고 전례. 실패 조건: 스냅샷 1행 있는 포트 DELETE → IntegrityError 500. 제안: 신규 FK는 `ondelete="CASCADE"`.
- **위험 D2 — upsert 경합 + 날짜 기준 불일치**: SELECT-then-INSERT 패턴은 배치·열람 동시 실행 시 중복 INSERT; 배치는 UTC `date.today()`(worker.py:291), API는 `kst_today()` — KST 00~09시에 다른 snap_date. 실패 조건: 동시 실행 unique 위반 500, 새벽 배치 이틀치 중복/공백. 제안: `INSERT … ON CONFLICT DO UPDATE` 단일문 + **kst_today로 통일(기존 배치 결함 동시 수정)**.
- **위험 D3 — 암호화 컬럼 복호 비용**: 값이 base64 암호문이라 DB 집계·인덱스 불가, 행×3회 AES-GCM. 제안: 차트 조회는 필요한 컬럼만, 행 수 = 포트×기간 상한을 명세에 고정.
- **위험 D4 — 통화 해석을 portfolios.market에 위임**: 값의 단위가 행 밖에 있음 — 환율 도입·market 변경 시 재해석 위험. 제안: 행에 `currency`(KRW|USD, 평문) 비정규화 저장 + market 변경 API 미제공 명문화.
- **위험 D5 — 하이퍼테이블 불필요(반대 논증)**: 1,000사용자×5포트×365일 ≈ 180만 행/년·~400MB, btree(portfolio_id, snap_date)로 충분, 암호문이라 continuous aggregate 이득 없음. 제안: 일반 테이블 + unique 제약, 1억 행 도달 전 재평가 조건만 기록.

### QA

- **위험 Q1 — 거래 삭제 후 당일 스냅샷 유령값**: `_rebuild_ledger`(portfolios.py:189-)는 스냅샷 미갱신. 실패 조건: 입금 100만→/dashboard(적재)→입금 삭제→trend 오늘 포인트 100만 vs summary 0. 제안: 거래 삭제·등록 시 **당일 스냅샷 즉시 재계산**.
- **위험 Q2 — 소급 거래 시 change/calendar 왜곡**: 스냅샷 불변인데 흐름(user_flows_between)은 실시간 재조회 — 소급 입금 시 분자·분모 어긋남. 실패 조건: D-1 이전 소급 입금 후 change_amount에 +입금액 반영. 제안: 왜곡 허용 범위를 예외 케이스로 명문화(과거 스냅샷 재계산은 후순위).
- **위험 Q3 — 분모 경계 3종**: ① 원금 초과 출금(net%=null·금액 표시) ② 입금 없이 buy만(net%=null, 평가%정상) ③ 전량 매도(평가%=null·평가금액 0). 셋 중 500/ZeroDivision/0% 반환이면 실패.
- **위험 Q4 — KR/US 혼재 단위 혼합**: KR 100만원 + US 10만센트 사용자에서 카드 상호 오염 검증. 제안: 혼재 픽스처를 공용화.
- **위험 Q5 — 중복 적재·신규 첫날**: 같은 날 /dashboard 2회 열람 → (portfolio_id, snap_date) 1행. 신규 사용자 빈 상태 계약 확정.
- **위험 Q6 — 기존 테스트 회귀·시간대 플래키**: `test_dashboard_total_is_sum_of_components`·`test_trend_and_calendar`는 기존 응답 형태 가정 — 비파괴 확장 필수. 테스트의 `date.today()` vs 서버 `kst_today()` — UTC 15~24시 CI 플래키. 제안: 테스트 날짜 산출 kst_today 통일.

## 쟁점과 충돌

- **스냅샷 원천 방향**: 사용자 스냅샷 우선(현행 유지) vs 포트 스냅샷 원천·사용자 유도(Backend) — 유도 방식 채택. 반올림·시점 불일치를 구조적으로 제거하고 D2의 단일 트랜잭션 upsert와 결합.
- **US 시리즈 차트 표시**: 사용자 결정(A안: $ 별도, 환율 미도입)에 따라 KRW 차트에서 US 라인 제외. API는 전 포트 시리즈+`currency`를 반환하되 웹이 KRW만 그림 — 환율 도입 시 재검토.
- **trend 다운샘플 수준**: 정밀 다운샘플(LTTB 등) vs 단순 주 단위 — 1단계는 ALL에서 주 단위 샘플로 단순화(구현 비용 대비 충분).

## 결론(합의안)과 반영처

| 쟁점 | 상태 | 반영처 |
|---|---|---|
| 포트 스냅샷 원천 + 사용자 유도 합산 | resolved | feature-dashboard §5·§7, ADR-008 |
| portfolio_snapshots 스키마(FK CASCADE·currency·unique·일반 테이블) | resolved | ADR-008, feature-dashboard §7 |
| ON CONFLICT upsert + kst_today 통일(배치 UTC 결함 수정 포함) | resolved | feature-dashboard §5, 구현 |
| latest_close 일괄 조회 유틸 | resolved | 구현 (dashboard.py) |
| 손익 % 계약(이중 기준, 분모≤0→null, 금액 상시 표시) | resolved | feature-portfolio §5·§8, §12 |
| 거래 삭제·등록 시 당일 스냅샷 재계산 | resolved | feature-portfolio §5, §12 |
| KR/US 필터 계약 + trend `series.currency` | resolved | feature-dashboard §8, §12 |
| ALL 주 단위 샘플 | resolved | feature-dashboard §8 |
| 소급 거래의 과거 스냅샷 왜곡 | deferred(과거 구간 재계산은 후순위 — 예외 케이스 명문화, 담당: 백로그) | feature-dashboard §12 예외, TODO |
| 환율 도입(KRW 환산 합산) | requires user decision(도입 시 ADR 별도) | feature-dashboard §15 |
| 하이퍼테이블 | resolved(불채택 — 재평가 조건 기록) | ADR-008 |

위험→테스트 추적: B1·B2·B5·Q1·Q3·Q4·Q5·D1·D2는 feature-dashboard·feature-portfolio §12 자동 테스트 항목으로 등재(본 커밋에서 구현). Q2는 §12 예외 케이스로 명문화. Q6은 기존 테스트 비파괴 확장 + 날짜 산출 통일로 처리.
