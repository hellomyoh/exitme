# Sources Index

사용자 제출 자료의 인덱스입니다. 관리 규칙은 `KICKOFF.md` 15.2, 반영 절차는 `DEVELOPINIT.md` 4.2를 따릅니다.

| 파일 | 유형 | 제출일 | 상태 | 요약 | 반영 산출물 / 대체 관계 |
|---|---|---|---|---|---|
| [REQUIREMENTS.md](REQUIREMENTS.md) | 초기 요구사항 | 2026-08-28 | 반영 완료 | StockLab 개발 요구사항 정의서 — RAVG v2 전략 내장 백테스트·실전기록·대시보드 웹 | [ARCHITECTURE.md](../ARCHITECTURE.md), [PLAN.md](../PLAN.md), [features/README.md](../features/README.md) (feature 6종), [adr/INDEX.md](../adr/INDEX.md) (ADR 6종), [docs/README.md](../docs/README.md), [qa/README.md](../qa/README.md) |
| [basic_trade.md](basic_trade.md) | 참고자료 | 2026-08-28 | 반영 완료 | ETF 매매법 v1 알고리즘 + 검토의견(버그 4건·보완 4건) | [trade_algorithm_final.md](trade_algorithm_final.md)로 확정, REQUIREMENTS.md에 반영 |
| [trade_web_system.md](trade_web_system.md) | 참고자료 | 2026-08-28 | 반영 완료 | 웹 시스템 PRD (아키텍처·UX·기능·마일스톤) | REQUIREMENTS.md에 반영 |
| [trade_algorithm_final.md](trade_algorithm_final.md) | 참고자료 (파생 산출물) | 2026-08-28 | 확정 | 매매법 최종안 RAVG v2 — 검토의견 전량 채택 + 레짐 상태머신·리밸런싱 밴드 보강. **매매 규칙의 정본** | REQUIREMENTS.md가 정본으로 참조 |

- **유형**: `초기 요구사항`(KICKOFF가 1회 처리, 프로젝트당 1개) / `변경요청`(DEVELOPINIT 4.2로 처리) / `참고자료`(등록·요약, 근거로만 사용)
- **상태**: `미반영` / `검토 중` / `반영 완료` / `반려(사유 기록)` / `대체됨(대체 문서 명시)`
- 원본은 `반영 완료` 후 **불변**입니다. 내용을 바꾸려면 새 문서를 추가하고 이전 문서를 `대체됨`으로 표시하세요.
