# Features Index

| 기능 | 문서 | 상태 | Phase | 관련 ADR |
|---|---|---|---|---|
| 시세 데이터 파이프라인 | [feature-market-data.md](feature-market-data.md) | 진행 중 (코드 완료 — 실 시딩·3거래일 배치 = KIS 키 대기) | Phase 1 | [ADR-002](../adr/002-timescaledb.md), [ADR-004](../adr/004-market-data-source.md) |
| 주식 차트 | [feature-chart.md](feature-chart.md) | 진행 중 (드로잉 4종·60fps 계측 잔여) | Phase 2 | [ADR-005](../adr/005-strategy-single-source.md) |
| 백테스트 3스텝 위저드 | [feature-backtest.md](feature-backtest.md) | 진행 중 (코드·테스트 완료 — 절제 실데이터 리포트 = 키 대기) | Phase 3 | [ADR-001](../adr/001-compose-monolith.md), [ADR-002](../adr/002-timescaledb.md), [ADR-005](../adr/005-strategy-single-source.md), [ADR-006](../adr/006-ravg-v2-adoption.md), [ADR-007](../adr/007-ravg-v25-adoption.md) |
| RAVG v2.5 전략 엔진 | [feature-strategy-engine.md](feature-strategy-engine.md) | 진행 중 (코드·테스트 완료 — 실데이터 배치 검증 = 키 대기) | Phase 4 | [ADR-005](../adr/005-strategy-single-source.md), [ADR-006](../adr/006-ravg-v2-adoption.md), [ADR-007](../adr/007-ravg-v25-adoption.md) |
| 실전매매 기록 | [feature-portfolio.md](feature-portfolio.md) | 완료 (마커·음영은 TODO) | Phase 5 | [ADR-003](../adr/003-auth-jwt.md), [ADR-008](../adr/008-portfolio-snapshots.md) |
| 자산 대시보드 | [feature-dashboard.md](feature-dashboard.md) | 완료 (2026-09-02 자산 구분·포트별 추이 확장) | Phase 6 | [ADR-003](../adr/003-auth-jwt.md), [ADR-008](../adr/008-portfolio-snapshots.md) |
