# Feature: 주식 차트

## 1. 목적

HTS 수준의 캔들 차트 — 지표·드로잉·리플레이(후순위)로 종목 분석과 포지션 복기를 지원한다.

## 2. 범위

### 포함 범위

- 캔들/바/라인/Heikin-Ashi, 주기 1·3·5·15·30·60분/일/주/월, 로그·퍼센트 스케일
- 멀티 페인: 메인(MA·볼린저·일목·VWAP) + 서브(거래량·RSI·MACD·스토캐스틱·OBV)
- 크로스헤어·OHLC 레전드, 휠 줌·드래그 팬, 드로잉 5종(추세선·수평선·채널·피보나치·텍스트) 종목별 저장
- 매수/매도 마커·평단선·목표/손절선 오버레이, RAVG 레짐 구간 음영(전략 엔진 이력 연동)
- TradingView Supercharts식 4-툴바 골격 + 레이아웃 저장

### 제외 범위

- 외국인·기관 수급 페인 — **후순위 강등** (v1 외부 연동에 수급 데이터 소스 없음 — 검토 결론)
- 리플레이 모드 — 후순위([REQUIREMENTS §3 후순위](../SOURCES/REQUIREMENTS.md))

## 3. 사용자 시나리오

종목 검색 → 일봉 차트에 MA·RSI 표시 → 추세선 드로잉(자동 저장) → 실전 포지션의 매수 마커·평단선 확인 → 레이아웃 저장 후 다음 방문 시 복원.

## 4. 최종 합의안

지표 이중 구현(차트=TS 클라이언트, 전략=서버 pandas)을 채택하되 교차 검증 테스트를 의무화했다.

### 검토 요약 (감사용)

- 참여 Agent: [Frontend Engineer](../personas/frontend-engineer.md), [System Architect](../personas/system-architect.md)
- 핵심 쟁점과 결론: Lightweight Charts v5는 지표·드로잉 미제공 → 지표는 클라이언트 자체 구현(Web Worker, 로드 시 1회 계산), 드로잉은 커스텀 프리미티브 5종 한정.
  지표 값이 서버(전략)와 달라 보이면 신뢰 붕괴 → 동일 수식·파라미터 명세 + 교차 검증 테스트. 수급 페인은 데이터 소스 부재로 후순위.
- 남은 쟁점: 없음
- 검토 로그: [discussion/review-chart-20260828.md](../discussion/review-chart-20260828.md)

## 5. 기능 요구사항

- 지표 계산은 클라이언트 TS 구현, Web Worker에서 로드 시 1회 전체 계산 후 시리즈 주입(팬/줌 재계산 금지). 수식·파라미터는 서버 전략 모듈과 동일하게 명세.
- 데이터는 `GET /ohlcv` 페이지 로드 + 과거 스크롤 시 증분 로드. 현재가는 `WS /ws/quotes`로 마지막 봉 갱신.
- 드로잉·레이아웃은 JSON 직렬화로 서버 저장(종목별·사용자별).
- 마커·평단선은 실전 포트 데이터([feature-portfolio.md](feature-portfolio.md)), 레짐 음영은 `GET /signals/history` 연동.

## 6. 비기능 요구사항

- 초기 로드 p95 < 1.5s, 5만 캔들 + 지표 5개에서 팬/줌 60fps([ARCHITECTURE §9](../ARCHITECTURE.md)).

## 7. 데이터 설계

- `chart_layouts(user_id, name, config JSONB)`, `chart_drawings(user_id, instrument_id, items JSONB)`. 공통 규칙은 [ARCHITECTURE §3](../ARCHITECTURE.md).

## 8. API 설계

- `GET/PUT /chart/layouts`, `GET/PUT /chart/drawings?code=`. 시세는 [feature-market-data.md §8](feature-market-data.md) 재사용.

## 9. UI/UX 설계

- 4-툴바 골격(상단 종목·주기·지표 / 좌 드로잉 / 우 관심종목·상세 / 하단 백테스트 결과 탭). 다크 우선, tabular-nums, 상승 적/하락 청 + 색약 토글([REQUIREMENTS §7](../SOURCES/REQUIREMENTS.md)).

## 10. 보안 요구사항

- 레이아웃·드로잉 소유자 격리. 드로잉 JSON 크기 상한(요청 1MB)으로 남용 방지.

## 11. 로그 / 분석 요구사항

- 차트 로드 시간 계측(p95 추적), 지표 사용 빈도 이벤트.

## 12. 테스트 시나리오

### 자동 테스트

- 지표 교차 검증: 동일 입력에 TS 구현 vs 서버 pandas 구현의 MA20/EMA20/ATR20 값 오차 < 1e-8. 드로잉 JSON 직렬화 왕복 무손실. Heikin-Ashi 변환 수기 대조.

### 수동 QA

- 5만 캔들 + 지표 5개 팬/줌 프레임 계측(DevTools, <16.6ms/frame), 반응형 3단, 색약 토글 대비 확인 — [qa/manual-test-cases.md](../qa/manual-test-cases.md).

### 예외 케이스

- 시세 결측 구간 갭 표시, WS 끊김 시 재연결·지연 배지.

### 회귀 테스트 영향

- 지표 수식 변경 시 교차 검증 테스트가 양쪽(TS/py) 동시 갱신을 강제.

## 13. 완료 조건

- §12 자동 테스트 실제 실행·전부 통과(green), [HISTORY.md](../HISTORY.md) 기록. 수동 60fps 계측 결과 기록.

## 14. 참고 ADR

[ADR-005](../adr/005-strategy-single-source.md) (지표 교차 검증 예외 조항)

## 15. 미결정 사항

### 사용자 확인 필요

- 없음

### 기본값으로 진행한 사항

- 드로잉 5종 한정, 수급 페인 후순위 — [ASSUMPTIONS.md](../ASSUMPTIONS.md)

### 후순위 검토 사항

- 리플레이 모드, 수급 페인(데이터 소스 확보 후), 드로잉 종류 확대
