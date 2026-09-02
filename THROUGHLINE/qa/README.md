# QA 운영 방식

## QA 진행 원칙

- QA 기준은 두 층: **기능별 테스트 시나리오**(각 [feature 문서](../features/README.md) §12)와 **전체 체크리스트**(이 폴더).
- **"테스트 통과"의 정의: 테스트가 실제로 실행되고 결과(실행 명령 + 통과/실패 요약)가 [HISTORY.md](../HISTORY.md)에 기록되었을 때만 통과로 인정한다.** 실행 없는 통과 주장은 무효.
- Multi-Agent 검토([discussion/](../discussion/))에서 제기된 검증 가능한 실패 조건은 feature §12 또는 이 폴더의 체크리스트로 반드시 추적된다.

## 테스트 DB 격리 (필수 — 2026-09-01부터 코드로 강제)

- 통합 테스트는 실코드(069500 등)에 합성 데이터를 적재하므로 **개발 DB(stocklab)가 아니라 격리 DB(stocklab_ci)에서 실행**한다.
- **강제 장치**: `tests/conftest.py`가 DATABASE_URL의 DB 이름이 `_ci`로 끝나지 않으면 자동으로 `stocklab_ci`로 재지정하고, 격리 DB가 없으면 생성 + 마이그레이션까지 수행한다. 컨테이너 안에서 `pytest`만 실행해도 안전하다:
  `docker compose exec api python -m pytest -q`
- 배경: 수동 오버라이드 규칙만으로는 지켜지지 않아 2026-08-28·2026-09-01 두 차례 개발 DB 오염이 실제 발생 (HISTORY·NOTES 참조). `-e DATABASE_URL=...stocklab_ci` 수동 지정도 여전히 유효하다.
- 오염 여부 점검 쿼리는 [NOTES.md](../NOTES.md) "테스트 DB 격리" 항목 참조.

## 자동 테스트와 수동 QA의 구분

- **자동**: 전략 모듈 골든·경계 테스트, 백테스트 정합성(look-ahead·체결·비용·KPI), API 계약, FIFO 회계, 파이프라인 검증 규칙, CI e2e(compose).
- **수동**: 차트 60fps 계측, 반응형·다크/라이트, 접근성(WCAG 2.2 AA), 실 KIS 계정 배치 완주, 화면 수치 표본 검산.

## 기능별 테스트와 회귀 테스트의 관계

- 기능 개발 시 feature §12를 구현·실행한다. 그중 다른 기능과 공유되는 계약(전략 골든, FIFO 회계, adj_version 연동)은 [regression-checklist.md](regression-checklist.md)에 승격되어 릴리즈마다 재확인한다.
- 전략 파라미터·절제 플래그 변경은 회귀 영향이 가장 크다 — 골든 파일 diff가 의도된 변경인지 반드시 검토.

## QA 결과 기록 위치

- 실행 결과: [HISTORY.md](../HISTORY.md) (명령·통과/실패 요약). 진행 상태: [PROGRESS.md](../PROGRESS.md).

## 릴리즈 전 QA 절차

1. 전체 자동 테스트 실행 → 결과 기록
2. [regression-checklist.md](regression-checklist.md) 수행
3. [manual-test-cases.md](manual-test-cases.md) 중 변경 영향 항목 수행
4. [release-checklist.md](release-checklist.md) 최종 검수
