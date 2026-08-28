# 검토 로그: 시세 데이터 파이프라인 (2026-08-28)

실행 방식: `role-play` (단일 에이전트가 페르소나 인스턴스를 로드하여 순차 검토. 서브에이전트는 같은 날 백테스트·전략 엔진 검토에 투입되어 본 기능은 역할극으로 수행 — 병렬 검토로 보고하지 않음)

## 참여 페르소나와 선정 이유

- [System Architect Agent](../personas/system-architect.md) — 시세 단일 저장소 원칙의 적용 지점
- [Database Engineer Agent](../personas/database-engineer.md) — 하이퍼테이블·수정주가 스키마 소유
- [Security Agent](../personas/security.md) — KIS 키 보관·외부 호출 경계

## 페르소나별 검토

### System Architect

- 위험: 수집 배치와 백테스트 워커가 같은 Celery 풀을 쓰면 장 마감 직후 수집이 대화형 백테스트를 블로킹. 실패 조건: 시세 수집 중 백테스트 잡 대기 > 60s. 제안: 큐 분리(`ingest`/`backtest`). (근거: [ARCHITECTURE §1·§9](../ARCHITECTURE.md), 백테스트 검토 로그 R8과 수렴)
- 위험: 장중 폴링 주기 미정의 — KIS 호출 한도와 충돌 가능. 제안: 기본 폴링 10초(관심 종목만), 한도 도달 시 지연 표시. (근거: [REQUIREMENTS §5](../SOURCES/REQUIREMENTS.md))

### Database Engineer

- 백테스트 검토 로그의 R1(일봉/분봉 분리)·R2(원본 보존)·R7(status_history)이 이 기능의 스키마에 귀속됨 — [review-backtest-20260828.md](review-backtest-20260828.md) 참조. 실패 조건 동일.
- 위험: 시딩 10년(약 2,500 거래일 × 종목 수)의 pykrx 호출량 — 재시도·이어받기 없으면 중단 시 처음부터. 실패 조건: 시딩 중단 후 재실행 시 중복 행 삽입 또는 전체 재수집. 제안: 종목×기간 단위 체크포인트 + `ON CONFLICT DO NOTHING`.

### Security

- 위험: KIS 앱키·시크릿이 로그(요청 URL·헤더 덤프)에 노출. 실패 조건: 로그 grep으로 `KIS_APP_KEY` 값 검출. 제안: 로그 마스킹 필터 + `.env`만 보관. (근거: [ARCHITECTURE §6·§7](../ARCHITECTURE.md))
- 위험: pykrx는 비공식 크롤링 기반 — 응답 스키마 변경 시 조용한 오염. 제안: 수집 후 검증 규칙(OHLC 관계식 `low ≤ open,close ≤ high`, 결측·중복 검사) 통과분만 적재, 실패는 `batch_runs`에 기록.

## 쟁점과 충돌

1. KIS 주 소스 vs pykrx 보조의 우선순위 충돌(같은 날짜에 값이 다를 때) → **KIS 우선, pykrx는 결측 보충·검증용**으로 확정. 불일치 발견 시 로그 기록.

## 결론(합의안)과 반영처

- 큐 분리, 폴링 10초, 시딩 체크포인트, OHLC 검증 규칙, 로그 마스킹, KIS 우선 순위 — 모두 `resolved` → [feature-market-data.md](../features/feature-market-data.md) §5·§7·§10 반영
- KIS 호출 한도 실측(계정 등급별 상이) — `deferred(Phase 1 구현 시 실측, Backend)` → feature §15
- **위험→테스트 추적**: OHLC 검증·체크포인트 재실행·로그 마스킹은 feature §12 및 [qa/regression-checklist.md](../qa/regression-checklist.md)에 등재. ADR: [ADR-004](../adr/004-market-data-source.md)
