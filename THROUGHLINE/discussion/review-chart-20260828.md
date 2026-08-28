# 검토 로그: 주식 차트 (2026-08-28)

실행 방식: `role-play` (단일 에이전트가 페르소나 인스턴스를 로드하여 순차 검토)

## 참여 페르소나와 선정 이유

- [Frontend Engineer Agent](../personas/frontend-engineer.md) — 렌더 성능·지표 자체 구현이 이 기능의 핵심 위험
- [System Architect Agent](../personas/system-architect.md) — 지표 계산 위치(서버/클라이언트) 결정

## 페르소나별 검토

### Frontend Engineer

- 위험: Lightweight Charts v5는 지표를 제공하지 않음 — MA·볼린저·RSI 등 10여 종을 자체 구현해야 하며, 5만 캔들 × 다중 지표 재계산이 팬/줌마다 발생하면 60fps 붕괴. 실패 조건: 5만 캔들 + 지표 5개 오버레이에서 팬 프레임 드랍(<30fps). 제안: 지표는 로드 시 1회 전체 계산 후 시리즈로 주입(뷰포트 재계산 금지), 계산은 Web Worker. (근거: [REQUIREMENTS §3-2·§7](../SOURCES/REQUIREMENTS.md), [ARCHITECTURE §9](../ARCHITECTURE.md))
- 위험: 드로잉(추세선·피보나치)은 라이브러리 미지원 — 커스텀 프리미티브 플러그인으로 구현해야 함. 규모가 과소평가되기 쉬움. 제안: v1 드로잉은 5종(추세선·수평선·채널·피보나치·텍스트)으로 한정하고 종목별 저장은 JSON 직렬화. (근거: REQUIREMENTS §3-2)
- 위험: 수급(외국인·기관) 서브 페인은 OHLCV 외 별도 데이터 필요 — 시세 파이프라인 범위와 충돌. 제안: 수급 페인은 후순위로 강등 (데이터 소스 확보 후). 

### System Architect

- 쟁점: 지표 계산 위치. 서버 계산은 전략 엔진(pandas)과 코드 공유가 되지만 차트 인터랙션마다 왕복 발생. → **차트 표시용 지표는 클라이언트 계산**(TS 자체 구현), **전략 신호용 지표는 서버 계산**(정본 수식, [ADR-005](../adr/005-strategy-single-source.md)) — 두 용도를 분리하고 값 일치는 골든 테스트로 상호 검증. 표시용과 신호용이 다른 값을 보여주면 안 되므로 동일 수식·동일 파라미터를 feature 문서에 명세.

## 쟁점과 충돌

1. 지표 이중 구현(TS/py) vs 단일 구현 — 성능상 이중 구현 채택, 대신 교차 검증 테스트 의무화로 조정.
2. 수급 페인 데이터 소스 부재 — MVP에서 후순위로 이동 (`requires user decision` 아님: REQUIREMENTS §3-2에 나열돼 있으나 §5 외부 연동에 수급 소스가 없어 실현 불가 — 보수적 축소).

## 결론(합의안)과 반영처

- 지표 클라이언트 계산 + Web Worker + 교차 검증 테스트 — `resolved` → [feature-chart.md](../features/feature-chart.md) §5·§12
- 드로잉 5종 한정·JSON 저장 — `resolved` → feature §5
- 수급 페인 후순위 강등 — `resolved`(보수적 축소, [ASSUMPTIONS.md](../ASSUMPTIONS.md) 기록) → feature §2 제외 범위
- **위험→테스트 추적**: 60fps 성능 테스트·지표 교차 검증은 feature §12 및 [qa/manual-test-cases.md](../qa/manual-test-cases.md)에 등재
