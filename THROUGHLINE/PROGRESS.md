# PROGRESS.md

## 현재 Phase

Phase 0~6 **코드 구현 완료** (브랜치 feat/market-data). 잔여 = KIS 키 의존 검증 항목만.

## 완료된 작업

- Phase 0 인프라 / 1 시세 파이프라인 / 2 차트+인증 / 3 전략·백테스트·위저드 / 4 시그널·주문표 / 5 실전매매 / 6 대시보드
- 자동 테스트: api **88 passed** + web vitest **4 passed** (2026-08-28, HISTORY 기록)
- 화면 7종(/,dashboard,chart,simulator,signals,portfolio,login) 스모크 200, compose 7/7 healthy

## 진행 중인 작업

- 없음

## 남은 작업 (전부 KIS 키 기입 대기)

1. `.env` 키 기입 → `docker compose run --rm api python -m scripts.seed --years 10` 실 시딩 + 데이터 검증 리포트
2. 일일 배치(수집→시그널→스냅샷) 3거래일 연속 검증, 배치 30분 실측
3. RAVG v2 절제 5종 **실데이터** 백테스트 리포트 → 파라미터 확정 변경요청 제출 (Phase 4 게이트)
4. 릴리즈 QA: [qa/release-checklist.md](qa/release-checklist.md) + 접근성 수동 QA
5. 백로그(TODO.md): 드로잉 4종, 매수 마커, 범용 조건식 백테스트, 알림 등

## QA 상태

- 자동 green (88+4). 수동: 60fps 계측·실 KIS 배치·접근성 — 실데이터 확보 후

## 다음 세션 첫 명령

"사용자의 KIS 키 기입 여부를 확인하고, 기입됐으면 실 시딩 → 절제 5종 백테스트 리포트 → 일일 배치 검증 순으로 잔여 검증을 수행하세요. 미기입이면 TODO 백로그(차트 드로잉 4종 등)를 진행하세요."
