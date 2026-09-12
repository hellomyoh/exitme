# ADR Index

| 번호 | 제목 | 상태 | 관련 영역 | 관련 feature |
|---|---|---|---|---|
| [ADR-001](001-compose-monolith.md) | docker compose 단일 서버 + api/worker 분리 | Accepted | 아키텍처·배포 | feature-backtest.md, feature-market-data.md |
| [ADR-002](002-timescaledb.md) | TimescaleDB 단일 시세 저장소 (일봉/분봉 분리) | Accepted | DB | feature-market-data.md, feature-backtest.md |
| [ADR-003](003-auth-jwt.md) | JWT access + refresh 회전 인증 | Accepted | 인증·보안 | feature-portfolio.md, feature-dashboard.md |
| [ADR-004](004-market-data-source.md) | 시세 소스 KIS 주 + pykrx 보조 | Accepted | 외부 연동 | feature-market-data.md |
| [ADR-005](005-strategy-single-source.md) | 전략 코드 단일 소스 | Accepted | 아키텍처·테스트 전략 | feature-strategy-engine.md, feature-backtest.md |
| [ADR-006](006-ravg-v2-adoption.md) | RAVG v2 전략 채택과 정본 규칙 | Accepted | 전략·도메인 | feature-strategy-engine.md, feature-backtest.md |
| [ADR-007](007-ravg-v25-adoption.md) | RAVG v2.5 명명 — 정본 v2에 대한 확정 개정 3건 고정 | Accepted | 전략·도메인 | feature-strategy-engine.md, feature-backtest.md |
| [ADR-008](008-portfolio-snapshots.md) | 포트 단위 자산 스냅샷 신설 — 사용자 스냅샷은 합산 유도 | Accepted | DB·대시보드 | feature-dashboard.md, feature-portfolio.md |
| [ADR-008 (무인)](008-controlled-auto-execution.md) | 통제된 무인 실행 — 승인된 지정가를 09:01 시가 확인 후 발주 (번호 중복: 2026-09-06 분기 병합 시 충돌, 파일명 유지) | Superseded (부분, ADR-009) | 외부 연동·상태관리 | feature-portfolio.md, docs/auto-execution-20260906.md |
| [ADR-009](009-unattended-single-execution.md) | 무인 매매 단일 실행 — 계좌 플래그만 참조, 09:01 계산·발주·동결, 취소 버튼, 감시 | Accepted | 외부 연동·상태관리·배포(헬스체크) | feature-portfolio.md, docs/auto-execution-20260906.md |
| [ADR-010](010-bootstrap-entry.md) | 소량 진입 부트스트랩 — 시작 후 10거래일, 목표 미달분 15%(하락장 7.5%) 종가 지정가 | Accepted | 전략·도메인 | feature-strategy-engine.md, feature-portfolio.md |
| [ADR-011](011-manual-order.md) | 앱에서 직접 주문 — 발주 경로에 수동(API) 주문 추가, mode=manual 로 무인과 같은 취소·체결 확정 | Accepted | 외부 연동·상태 기계 | feature-portfolio.md |
| [ADR-012](012-leverage-total-cap.md) | 총 레버리지 상한 — 전술 트랙도 목표 노출에 종속(정본 §7 개정, 감사 A3). 역사 표본 발동 0회의 안전 경계 | Accepted | 전략·도메인 | feature-strategy-engine.md |
