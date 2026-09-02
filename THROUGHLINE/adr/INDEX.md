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
