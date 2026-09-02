# Feature: 자산 대시보드

## 1. 목적

총자산·손익·레짐을 한 화면에서 점진 공개 원칙으로 보여준다. 루프(검증→실행→관리)의 관리 단계.

## 2. 범위

### 포함 범위

- 히어로: 총자산 단일 수치 + 전일대비(금액·%) + 전체 기간 수익률
- 자산 구성 도넛(주식/현금/기타 수동 등록), 보유 종목 테이블, 비중 상위 5·업종 분산
- **자산 내용 카드(2026-09-02)**: 총자산(KRW) + 한국 주식 카드(실전매매 KR 포트 합산 — 평가액·보유원가·평가손익 금액/%) + 미국 주식 카드(**$ 표기**, KRW 총자산 합산 제외 — 환율 미도입)
- 자산 추이 라인(1M/3M/6M/1Y/전체) — **총자산 + 포트별 다선**(KRW 포트, 2026-09-02), 일간 손익 캘린더 히트맵
- **RAVG 레짐·노출 게이지**(현재 레짐, E, 목표 배분 — [feature-strategy-engine.md §8](feature-strategy-engine.md) `signals/history` 연동)
- 포트 성과 비교(실전 vs 백테스트 기대), 시세 기준시각 상시 표기

### 제외 범위

- 알림·이벤트 피드(후순위), 기타 자산의 시세 자동 평가(수동 입력 값 사용)

## 3. 사용자 시나리오

로그인 → 총자산과 전일대비 확인 → 레짐 게이지에서 "중립 / E=0.58" 확인 → 손익 캘린더에서 이번 달 성과 훑기 → 실전 포트 카드에서 백테스트 기대 대비 확인.

## 4. 최종 합의안

초기 범위는 자명한 읽기 집계였으나, 2026-09-02 자산 구분·포트별 추이 확장은 데이터 모델 신설을 수반해 Multi-Agent 검토를 수행했다.

### 검토 요약 (감사용)

- 참여 Agent: [Backend/Quant](../personas/backend-engineer.md), [Database](../personas/database-engineer.md), [QA](../personas/qa.md) (병렬 서브에이전트 3기, 2026-09-02)
- 핵심 쟁점과 결론: 포트 스냅샷을 **정수 원천**으로 확정하고 사용자 스냅샷은 합산 유도(반올림·시점 불일치 제거). 신규 FK는 CASCADE(포트 삭제 사고 재발 방지), 적재는 ON CONFLICT 단일문 + kst_today 통일(배치 UTC 결함 동시 수정), 행에 currency 비정규화. 미국 자산은 $ 별도 표기(환율 미도입 — 사용자 결정).
- 남은 쟁점: 소급 거래 시 과거 스냅샷 불변으로 인한 전일대비·캘린더 표시 왜곡은 `deferred`(§12 예외 케이스로 명문화), 환율 도입은 §15
- 검토 로그: [discussion/review-dashboard-asset-breakdown-20260902.md](../discussion/review-dashboard-asset-breakdown-20260902.md)

## 5. 기능 요구사항

- 총자산 = 보유 종목 평가액(현재가) + 현금(입출금 원장) + 기타 자산(수동 등록). 암호화 필드는 앱 레벨 복호 집계([feature-portfolio.md §10](feature-portfolio.md)).
- 일별 자산 스냅샷을 배치로 적재(추이·캘린더의 원천 — 소급 계산 아님).
- **스냅샷 원천·유도 (2026-09-02, [ADR-008](../adr/008-portfolio-snapshots.md))**: 포트 단위 스냅샷(portfolio_snapshots)을 정수로 먼저 확정하고, 사용자 asset_snapshots = Σ(KR 포트 equity) + 기타 자산을 **같은 트랜잭션에서 유도**한다. 적재는 `ON CONFLICT (portfolio_id, snap_date) DO UPDATE` 단일문, 날짜는 배치·API 모두 `kst_today()`.
- **시세 일괄 조회**: 스냅샷·카드 집계의 최신 종가는 종목 집합 단위 1쿼리(`DISTINCT ON`)로 조회한다 — 로트당 개별 조회(N+1) 금지.
- **자산 내용 카드**: 한국 주식 = 실전매매 KR 포트 합산 {평가액, 보유원가, 평가손익 금액, 평가손익%(÷보유원가)}. 미국 주식 = US 포트 합산을 센트 그대로 **$ 표기**(환율 미도입 — KRW 총자산·도넛·추이 합산에서 제외 유지).
- **거래 등록·삭제 시 당일 스냅샷 즉시 재계산** — 열람 시점에 적재된 당일 스냅샷이 삭제된 거래를 반영하지 못하는 유령값 방지(검토 Q1).
- 기타 자산: 이름·분류(채권/펀드/금/코인/부동산 등)·평가액 수동 등록·수정.
- 벤토 그리드, 히어로 타일 화면당 2개 이하([REQUIREMENTS §7](../SOURCES/REQUIREMENTS.md)).

## 6. 비기능 요구사항

- 대시보드 초기 로드 < 1.5s(집계는 스냅샷 테이블 조회로 충족).

## 7. 데이터 설계

- `asset_snapshots(user_id, date, total🔒, stock🔒, cash🔒, other🔒)` — 일별 배치 적재, 🔒 암호화. **포트 스냅샷 합산의 유도값**([ADR-008](../adr/008-portfolio-snapshots.md)).
- `portfolio_snapshots(id, portfolio_id FK CASCADE, snap_date, equity🔒, stock_value🔒, cash🔒, currency, UNIQUE(portfolio_id, snap_date))` — 포트 단위 일별 스냅샷, currency(KRW|USD)는 적재 시점 비정규화(값 단위를 행 안에 보존). 일반 테이블(하이퍼테이블 불채택 — ADR-008 논증).
- `manual_assets(id, user_id, name, category, value🔒, updated_at)`.

## 8. API 설계

- `GET /portfolio/summary`(히어로·도넛·테이블), `GET /portfolio/trend?range=`, `GET /portfolio/calendar?month=`, `POST/PATCH/DELETE /manual-assets`.
- `/dashboard` 확장(2026-09-02): `kr_stock {value, cost, pnl, pnl_pct}` + `us_stock {value_cents, cost_cents, pnl_cents, pnl_pct}` — pnl_pct 분모(보유원가) ≤ 0 이면 null.
- `/portfolio/trend` 확장: 기존 `items` **비파괴 유지** + `series: [{portfolio_id, name, market, currency, points[{date, equity}]}]` 추가. 웹 차트는 `currency='KRW'` 시리즈만 그린다(US는 카드로만 — 환율 도입 시 재검토). ALL 구간의 series 는 주 단위 샘플(주 마지막 스냅샷).

## 9. UI/UX 설계

- 벤토 그리드(히어로 6열, 상태 타일 2×2), 점진 공개 — 첫 화면 3~5개 지표. 반응형 3→2→1 컬럼 + 하단 탭바.

## 10. 보안 요구사항

- 전 리소스 소유자 격리, 금액 필드 암호화([ADR-003](../adr/003-auth-jwt.md)).

## 11. 로그 / 분석 요구사항

- `visit` 이벤트(주간 재방문 40% 지표), 스냅샷 배치 로그.

## 12. 테스트 시나리오

### 자동 테스트

- 총자산 = 구성 요소 합 일치(암호화 복호 집계), 전일대비 계산, 스냅샷 결측일 처리(휴장일), 기타 자산 CRUD 소유자 격리(타인 접근 403).
- **합산 불변식(2026-09-02)**: 임의 (user, date)에서 `Σ(portfolio_snapshots.equity WHERE currency='KRW') + other == asset_snapshots.total`.
- **KR/US 혼재**: KR 포트(원) + US 포트(센트) 사용자 — kr_stock 에 센트 미혼입, us_stock 에 원 미혼입, trend `items`·total 은 KR 한정 유지, series 에 US 는 currency='USD' 로 반환.
- **중복 적재 유일성**: 같은 날 /dashboard 2회 열람 → (portfolio_id, snap_date) 행 수 1 (ON CONFLICT 갱신).
- **거래 삭제 후 정합**: 입금 → /dashboard(적재) → 입금 삭제 → trend 오늘 포인트 == summary total_equity.
- **포트 삭제**: 스냅샷 보유 포트 DELETE /portfolios/{id} → 200 (FK CASCADE).

### 수동 QA

- 벤토 반응형 3단, 다크/라이트, 히트맵 색 대비(WCAG AA), 기준시각 표기.

### 예외 케이스

- 데이터 없음(신규 가입) 빈 상태 화면, 스냅샷 배치 실패 시 최신 스냅샷 기준 표시 + 지연 배지.
- **소급 거래 왜곡(허용·명문화)**: 과거 스냅샷은 불변이므로 스냅샷 적재 이전 날짜로 소급 입금/삭제하면 전일대비·캘린더가 그 시점 원장과 어긋날 수 있다 — 당일 스냅샷만 재계산하며 과거 구간 재계산은 후순위(TODO).

### 회귀 테스트 영향

- 포트폴리오 회계 변경 시 총자산 합 일치 테스트 재확인.

## 13. 완료 조건

- §12 자동 테스트 실제 실행·전부 통과(green), [HISTORY.md](../HISTORY.md) 기록. 접근성 수동 QA 완료.

## 14. 참고 ADR

[ADR-003](../adr/003-auth-jwt.md), [ADR-008](../adr/008-portfolio-snapshots.md)

## 15. 미결정 사항

### 사용자 확인 필요

- 없음

### 기본값으로 진행한 사항

- 기타 자산은 수동 평가액 방식 — [ASSUMPTIONS.md](../ASSUMPTIONS.md)

### 후순위 검토 사항

- 알림·이벤트 피드, 업종 분산 자동 분류 고도화
- 환율 도입(미국 자산 KRW 환산 합산) — 외부 연동이라 도입 시 별도 ADR (2026-09-02 사용자 결정: 1단계 $ 별도 표기)
- 소급 거래 시 과거 스냅샷 재계산, trend 정밀 다운샘플
