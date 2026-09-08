# Docs Index

| 문서 | 대상 독자 | 한 줄 설명 |
|---|---|---|
| [user-guide.md](user-guide.md) | 일반 사용자 | 차트·백테스트·실전 기록·대시보드 기본 사용 방법 + 증권사 연동·예약주문·무인 실행·완전 무인·사전 갭 취소·예수금 대조·매매 로그·텔레그램 알림 |
| [strategy-guide.md](strategy-guide.md) | 일반 사용자 | RAVG v2.5 전략과 주문표를 이해하고 활용하는 방법 |
| [operator-guide.md](operator-guide.md) | 운영자 | 설치·시딩·배치 운영·장애 대응 |
| [ablation-report-20260828.md](ablation-report-20260828.md) | 운영자 | RAVG v2 절제 5종 실데이터 백테스트 결과 (Phase 4 게이트) |
| [formula-verification-20260828.md](formula-verification-20260828.md) | 운영자 | 알고리즘 계산 공식 전수 검증 — 치명 2·중 10건 수정 내역 |
| [entry-gate-study-20260829.md](entry-gate-study-20260829.md) | 운영자 | 위험 극단 시 매수 정지 게이트 검토 — 미채택 실증 |
| [us-backtest-20260831.md](us-backtest-20260831.md) | 운영자 | 미국 시장 이식 백테스트 (QQQ+QLD / QQQ+TQQQ, 배율 보정 검증) |
| [regime-buffer-study-20260831.md](regime-buffer-study-20260831.md) | 운영자 | 절제 ③ 후속 — v1 복귀·완충 확대 기각, MA200 이탈 완충 ε=2% 권고 (3차 검증 완료) |
| [us-transfer-study-20260831.md](us-transfer-study-20260831.md) | 운영자 | 한·미 동일 알고리즘 적용 검증 — 파라미터 고원 확인, 세금·환율·운영 선결 조건 |
| [ltv-strategy-study-20260906.md](ltv-strategy-study-20260906.md) | 운영자 | 미국 지수 추세 자산용 신규 공식 LTM 연구 — 문헌·4차 개선 반복·민감도·국내 상장 S&P 판정 |
| [krx-aftermarket-20260914-review.md](krx-aftermarket-20260914-review.md) | 운영자 | KRX 애프터마켓 개장(2026-09-14) 영향 검토 — ETF 제외로 공식 수정 없음, 운영 보완(20:15 동기화·예약주문 창 확인), 11월 NXT ETF 관찰 항목, ETF 밤갭 통계 |
| [fast-entry-study-20260908.md](fast-entry-study-20260908.md) | 운영자 | 소량 진입(부트스트랩) 알고리즘 탐색 — 234개 조합·3 표본: 첫 10거래일 미달분 15%(하락장 7.5%) 종가 지정가 → 첫 체결 1~2일, 250일 수익 차이 ≈ 0. 채택 → [ADR-010](../adr/010-bootstrap-entry.md) 구현(2026-09-08) |
| [cold-start-entry-study-20260908.md](cold-start-entry-study-20260908.md) | 운영자 | 보유 0·현금만으로 시작하는 사용자의 진입 속도 검증(87개 시작·250일) — 상승·중립장 첫 체결 2~4일, 하락장 시작은 매수 정지로 중앙 78일 대기(규칙), 시가 부트스트랩은 기대 수익 −0.7%p 라 공식 유지·화면 안내만 제안. §6 하루 매수 상한 재검증: 안전장치가 아니라 진입 속도 제한(CAGR 무관, 정상 주문의 25%를 자름) → 제거 또는 기본 0 권고 |
| [auto-execution-20260906.md](auto-execution-20260906.md) | 운영자·개발자 | 무인 매매 단일 실행([ADR-009](../adr/009-unattended-single-execution.md), 2026-09-08) — 계좌 플래그 → 09:01 계산·시가 확인·발주·동결 + 09:15 감시·하트비트 + 15:45 확정·예수금 대조 + 매매 로그·텔레그램 알림: 흐름·코드 지도·상태 흐름·테스트·운영 절차·한계(§5-1 은 2026-09-06 검증 이력) |
| [broker-reserved-orders-20260905.md](broker-reserved-orders-20260905.md) | 운영자·개발자 | 증권사(KIS) 조회 연동·예약주문 접수·장 마감 동기화 설계 |
| [mjournal-broker-link-review-20260905.md](mjournal-broker-link-review-20260905.md) | 운영자·개발자 | 매매일지 증권사 연동 검토 — 체결 가져오기·기초 보유 등록·계좌 평가금액 |
| [manual-holdings-tp-review-20260903.md](manual-holdings-tp-review-20260903.md) | 운영자 | 수동 등록 보유분의 전량 익절 근사 검토(설계 정합 유지) |
| [minute-fill-study-20260901.md](minute-fill-study-20260901.md) | 운영자 | 1분봉 체결 시뮬 대조 — 일봉 엔진 유지 |
| [order-formula-study-20260903.md](order-formula-study-20260903.md) | 운영자 | 주문표 공식 수리 검토 — E 탄력성·그리드 반사원리 검증, 예산 가중 50/30/20 후보 (미채택) |
| [yearly-trade-frequency-20260903.md](yearly-trade-frequency-20260903.md) | 운영자 | 2020~2026 연도별 시뮬레이션·체결 횟수 분해 (평균 주 1.3회, 2022년 방어 17.1%p) |
- [market-research/](market-research/README.md) — 유사 서비스 시장 조사(11건, 기능·유료화 모델·ExitMe 시사점, 2026-09-05)
