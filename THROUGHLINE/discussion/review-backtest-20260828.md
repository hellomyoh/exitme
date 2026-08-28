# 검토 로그: 백테스트 3스텝 위저드 (2026-08-28)

실행 방식: `parallel-subagents`

서브에이전트 증거:

| 식별자 | 부여한 입력 범위 | 출력 요약 |
|---|---|---|
| SA-db (2026-08-28, 소요 66.6s, 26,490 tokens, tool 2회) | REQUIREMENTS.md §6·§8·§10·§11 + trade_web_system.md, DB 관점 | 위험 8건: 일봉/분봉 청크 혼재, 수정주가 덮어쓰기로 재현성 붕괴, 결과 볼륨 무제한, 시그널 버전 체인 비결정, look-ahead 데이터 계층 미강제, stale 결과 무경고 표시, 생존 편향, concurrency=4로 60s 목표 미검증 |
| SA-qa2 (2026-08-28, 소요 103.9s, 33,574 tokens, tool 4회) | REQUIREMENTS.md + trade_algorithm_final.md §11 + trade_web_system.md §4.2·§5, QA 관점 | 정합성 7건(C1~C3·D1~D4), 비용 4건(E1~E4), KPI 5건(K1~K5), 잡 수명주기 4건(J1~J4), 절제·오버레이 4건(A1~A4) + 사양 블로커 6건(체결 판정식, 벤치마크 불일치, KPI 규약, 정지·상폐 규칙, 취소 API, 세금 모델) |

## 참여 페르소나와 선정 이유

- [Database Engineer Agent](../personas/database-engineer.md) — TimescaleDB 스키마와 재현성이 이 기능의 토대
- [QA Agent](../personas/qa.md) — look-ahead 부재 증명과 KPI 정확성이 REQUIREMENTS §11 필수 시나리오

## 페르소나별 검토

### Database Engineer (SA-db)

- **R1**: 일봉·분봉 밀도 차 ~390배 — 단일 하이퍼테이블 청크 간격 결정 불가. 제안: `ohlcv_daily`(청크 1년)/`ohlcv_intraday`(청크 7일) 물리 분리 + 논리 뷰. (근거: REQUIREMENTS §6·§8)
- **R2**: 수정주가 UPDATE는 원본 소실 → 골든 테스트·재현성 붕괴. 제안: `close_raw`(불변)+`adj_factor`+`corporate_actions`, factor는 `adj_version` append-only. (근거: §6·§8, §11-1)
- **R3**: 유니버스×5년 거래내역 무제한 증가. 제안: 자산곡선 정규 테이블 + 거래내역 잡별 아티팩트, TTL 90일(KPI·곡선 영구). (근거: §6, trade_web_system §4.2)
- **R4**: 시그널 버전 공존 시 전일 상태 체인 비결정. 제안: `is_current` partial unique index, 승격 시 이후 전 구간 동일 트랜잭션 재계산. (근거: §10·§6)
- **R5**: look-ahead가 앱 코드에만 의존(`shift(1)` 누락 한 줄이면 침묵 오류). 제안: 리포지토리 단일 게이트웨이 + 쿼리 로그 어서션, RLS는 후순위. (근거: §8·§11-2)
- **R6**: `adj_version` 갱신 후 이전 잡이 무경고 표시. 제안: 잡에 `data_fingerprint` 저장 → stale 배지 + 오버레이 혼합 차단. (근거: §11-4, §7-4)
- **R7**: 정지·상폐를 현재 상태로 저장하면 생존 편향. 제안: `symbol_status_history` 시점 속성, 상폐 잔여 포지션 마지막 종가 강제청산. (근거: §5-정합성·§6)
- **R8**: concurrency=4·단일 서버로 "유니버스 전체<60s" 미검증, "유니버스 전체"의 종목 수 정의 부재. 제안: 벡터화 전제 + 일괄 로드 + 큐 분리(backtest 2/ingest 2), M2 실측 후 유니버스 정의 확정. (근거: §11 품질, trade_web_system §2)

### QA (SA-qa2)

- **C1~C3**: 미래 봉 마스킹 동일성(경계는 d+1 시가까지), 워밍업 가드 부재, 익일 휴장 시 주문 이월/취소 미정의.
- **D1~D4**: 지정가 체결 판정식 부재(Open≤L→Open 체결 / Low≤L<Open→L 체결 / else 미체결), 갭 필터 vs 체결 순서 충돌(필터 우선이어야), 익절 Grid 스냅샷 시점, 수정주가 소급 적용 자체가 잠재 look-ahead.
- **E1~E4**: 비용 방향성 단조성, 레버리지 과세 모델(실현차익 vs 과표기준가) 미정의, 보수 일할 기준(365 vs 252)·KODEX 200 보수 포함 여부, 지정가 체결 슬리피지는 이중 보수화 — 시장가성 청산에만 적용해야.
- **K1~K5**: CAGR 연환산 기준일수, MDD 종가 기준 여부, 샤프 rf·ddof, 거래 1건 정의(로트 FIFO 라운드트립), 벤치마크 불일치(PRD "KOSPI" vs 정본 "KODEX 200 매수보유").
- **J1~J4**: 진행률 Pub/Sub 유실 대비 스냅샷 병행 저장, 취소 엔드포인트 계약 부재, 재시도 멱등성(부분 저장 롤백), 재현성엔 `data_fingerprint` 선행 필요.
- **A1~A4**: 절제 32조합 vs 오버레이 5개 한도, 플래그 종속성(모듈4 OFF→레버리지 규칙 무의미, 모듈2 OFF→σ 정의 연동), 오버레이 정규화·파라미터 불일치 경고, 절제 골든 파일은 J4 선행 조건.

## 쟁점과 충돌

1. **벤치마크(K5)**: PRD는 KOSPI, 전략 정본 §11은 KODEX 200 매수보유. → 정본 우선 원칙(REQUIREMENTS §13)에 따라 **기본 벤치마크 = KODEX 200 매수보유**, KOSPI 지수는 보조 오버레이로 병기.
2. **60s 목표(R8)**: 사양 유지 vs 완화. → 목표는 유지하되 v1 기본 유니버스를 "유동성 상위 500 종목"으로 정의(보수적), M2 종료 실측 후 변경요청으로 재확정.
3. **과세 모델(E2)**: 과표기준가 정밀 계산은 외부 데이터(과표기준가 시계열) 필요. → v1은 실현차익 15.4% 단순화(손실 시 0, 이월 없음), 정밀 모델은 후순위. 화면에 "세금 단순화 계산" 명시(정직한 수치 원칙).

## 결론(합의안)과 반영처

합의안은 [feature-backtest.md](../features/feature-backtest.md) §5·§7·§8·§12와 [feature-market-data.md](../features/feature-market-data.md) §7에 반영. 쟁점 상태:

- R1 일봉/분봉 분리, R2 원본 보존+corporate_actions, R4 is_current 체인, R7 status_history — `resolved` (feature-market-data §7, feature-strategy-engine §7)
- R3 결과 TTL 90일, R6 data_fingerprint+stale 배지 — `resolved` (feature-backtest §7)
- R5 look-ahead 리포지토리 게이트웨이 + 쿼리 로그 어서션 — `resolved` (feature-backtest §5·§12); RLS 물리 차단은 `deferred(Phase 3 실측 후, Backend)`
- D1~D3 체결 판정식·갭 필터 우선·Grid 스냅샷 — `resolved` (feature-backtest §5)
- E1~E4 비용 모델(과세 단순화·보수 365 일할·지정가 슬리피지 0) — `resolved` (feature-backtest §5); 과표기준가 정밀 과세 `deferred(후순위, PM)`
- K1~K4 KPI 규약(252일 연환산·MDD 종가 기준·샤프 rf=0/ddof=1·거래=로트 FIFO 라운드트립) — `resolved` (feature-backtest §5)
- K5 벤치마크 통일 — `resolved` (feature-backtest §5, REQUIREMENTS 대비 변경은 [ASSUMPTIONS.md](../ASSUMPTIONS.md) 기록)
- J1~J3 진행률 스냅샷·취소 API `POST /backtests/{id}/cancel`·재시도 멱등(단일 트랜잭션 저장) — `resolved` (feature-backtest §8)
- A1~A2 절제 플래그 종속성·오버레이 한도 동작 — `resolved` (feature-backtest §5·§9)
- R8 유니버스 정의(상위 500)와 60s 실측 — `deferred(M2 종료 시 실측 후 변경요청, PM/Architect)` — feature §15에 등재

**위험→테스트 추적**: C1~C3, D1~D4, E1~E4, K1~K5, J1~J4, A1~A4 전 항목을 feature-backtest §12에 등재. ADR: [ADR-002](../adr/002-timescaledb.md)(시세 저장소).
