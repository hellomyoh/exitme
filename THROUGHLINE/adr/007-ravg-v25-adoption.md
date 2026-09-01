# ADR-007: RAVG v2.5 명명 — 정본 v2에 대한 확정 개정 3건 고정

## 상태

Accepted

## 배경

2026-08-31 사용자 승인으로 전략 규칙에 3건의 변경이 반영되었다.

| 항목 | 정본 v2 | v2.5 확정 | 성격 | 근거 |
|---|---|---|---|---|
| `sigma20_liquidate` | 0.25 | **0.35** | 정본 §10 튜닝(○) 범위 내 값 조정 | [HISTORY 2026-08-31](../HISTORY.md), 25→60% 스윕 — MDD 전 구간 불변 |
| `target_downside_vol` | 0.13 | **0.20** | 정본 §10 튜닝(○) 범위 내 값 조정 | KR IS·OOS 동시 개선, t_OOS +3.02, MDD 불변 |
| `ma200_exit_buffer` | (없음) | **0.02 신설** | **§4 상태머신 구조 변경** — 이탈의 MA200 다리에 히스테리시스 추가 | [regime-buffer-study](../docs/regime-buffer-study-20260831.md) 3중 검증 |

앞의 2건은 정본이 스스로 허용한 튜닝이지만, `ma200_exit_buffer`는 정본 §4가 의도적으로
무완충("이탈은 OR — 신속")으로 설계한 다리의 전이 조건 자체를 `Close < MA200×(1−ε)`로 바꾼
규칙 추가로, §10 튜닝 표 밖에 있다. 결합 성과도 크게 달라졌다
(KR 10년 +140.2%→+230.8%, MDD −26.0%→−20.6%, 샤프 0.84→0.98 — [HISTORY 2026-08-31](../HISTORY.md)).
정본 [trade_algorithm_final.md](../SOURCES/trade_algorithm_final.md)는 SOURCES 불변 원칙으로 수정할 수 없어,
확정값 권위가 [feature-strategy-engine.md](../features/feature-strategy-engine.md) §15 이력에 분산되어 있었다.

## 선택지

1. 계속 "RAVG v2"로 부르고 feature 문서 이력으로만 관리
2. 명칭을 **RAVG v2.5**로 올리고 본 ADR이 개정 목록을 고정
3. 새 정본 문서를 SOURCES 변경요청으로 작성 (v3 상당)

## 결정

2안 (사용자 결정 2026-09-01). **RAVG v2.5 = 정본 v2 + 본 ADR의 개정 3건.**

- 권위 체인: 정본 + 본 ADR > [feature-strategy-engine.md](../features/feature-strategy-engine.md)(구현 세부) > 기타.
- 정본·과거 리포트([ablation-report](../docs/ablation-report-20260828.md) 등 일자 문서)·discussion 로그는 당시 v2 기준 기록으로 소급 개명하지 않는다.
- 이후 신규 백테스트 리포트·UI·가이드는 v2.5로 표기한다.

## 이유

- 구조 변경(ε 신설)이 버전 경계를 만들며, 성과 프로파일 변화로 기존 리포트와의 비교 불가 경계 표시가 필요하다.
- 번호 2.5는 변경 규모(마이너)보다 성과 성격 변화를 강조하는 사용자 선택.
- 3안(새 정본)은 이미 개별 승인·검증된 변경의 재포장이라 과잉.

## 영향

- 명칭 스윕: 웹 UI 문자열·전략 모듈 독스트링·사용자/운영 가이드·feature 인덱스 (동일 commit).
- 엔진 동작·파라미터 기본값 변경 없음 — 개정 3건은 이미 코드에 반영되어 있고 본 ADR은 명명·권위 정리다.
- feature §7의 `strategy_params(version, …)` 테이블은 미구현 상태(v1 범위 외) — 별도 정리 대상으로 존치.

## 관련 feature / ARCHITECTURE 항목

[feature-strategy-engine.md](../features/feature-strategy-engine.md), [feature-backtest.md](../features/feature-backtest.md), [ADR-006](006-ravg-v2-adoption.md)
