# 체결률 연구 QA (2026-09-12)

[보고서](../docs/fill-rate-study-20260912.md), [결과 JSON](fill-rate-results-20260912.json), [연구 스크립트](../../apps/api/scripts/fill_rate_audit.py).

## 실제 실행과 검증

```powershell
docker compose exec -T api python -m scripts.fill_rate_audit --windows --sensitivity
docker compose exec -T api python -m scripts.fill_rate_audit
```

첫 실행은 8변형 전 구간·비용 민감도·87창×8변형 완료(exit 0). 두 번째는 수량·기준금액 가중 체결률 추가 후 전 구간 재실행(exit 0), 기존 KPI 동일 확인. 비용 전부 0인 4변형은 별도 읽기 전용 명령으로 실제 실행했으며 이후 동일 계산을 `--sensitivity` 옵션에 편입했다. 최종 스크립트 `docker compose exec -T api python -m scripts.fill_rate_audit --sensitivity`도 재실행(exit 0)하여 전 구간·비용 결과 **28개 레코드가 저장 JSON과 동일**함을 확인했다. 최종판에서 87창 루프를 다시 실행하지는 않았다(창 로직 변경 없음).

- [x] 연구 wrapper 기본값과 제품 run_backtest 전체 결과 동일.
- [x] 현행 그리드 1,943개 지정가 재계산 일치.
- [x] 계획→체결 키 연결 및 매수 사유 합계 일치(2,046=294+1,702+25+25).
- [x] 매수 가격 변형은 기존 주문 명목예산 이하 수량, 양수 주문만 생성.
- [x] 마지막 미실행 계획 제외, 매수/매도·주문선/FIFO 거래 수 분리.
- [x] 전체/87 겹침창/비중첩 8창·레짐별·2023년 전후를 구분. 비중첩 결과의 반대 방향도 보고.
- [x] 현금 초기화와 과거 시장 레짐 이어받기 분리. 첫 K200 체결과 레버리지 최초 체결 혼동 금지.
- [x] 수수료 2배·추가 보수 0·비용 전부 0에서 직접 가격 변형 순위 점검.
- [x] 생산 데이터 조회 세션 `SET TRANSACTION READ ONLY`; 결과 DB 저장·브로커 호출·설정 변경 없음.

독립 QA 실제 실행:

```powershell
docker compose exec -T api pytest -q tests/test_indicators.py tests/test_strategy_planner.py tests/test_strategy_backtest.py tests/test_bootstrap_entry.py tests/test_signals.py::test_truncated_backtest_final_plan_equals_full_run_plan
```

**63 passed, 2 warnings in 3.10s**. 전체 브로커·UI 회귀는 미실행.

주 에이전트도 동일 범위를 `python -m pytest -q`로 재실행해 **63 passed, 2 warnings in 3.21s**를 확인했다. 경고는 FastAPI `on_event` 폐기 예정 안내 2건이며 실패가 아니다. 독립 실행과 중복이므로 테스트 수를 합산하지 않는다.

## 미해결 실패 조건·후속 검증

- [ ] F1: 실전 체결률은 계획 원수량(`response.plan_qty`)과 발주 축소 후 수량을 분리. submitted를 체결로 세지 않고 filled/partial/unfilled를 구분.
- [ ] F2: 무주문·발주 실패/생략·갭취소·가격 미도달·현금 부족을 거래일·종목·종류별 추적. 무주문이 정상인지 노출 축소 누락인지 구분.
- [ ] F3: 레버리지 전술2의 현금부족 18/26 원인을 공통 예산과 시가 변동으로 분해하고, 실전 수량 축소와 엔진 전량 건너뛰기 차이를 모의 계좌에서 확인.
- [ ] F4: 선행 [감사 QA A1/A2/A3/A8](kodex-linkage-audit-20260912.md) 수정 후 같은 체결률·성과 실험 재실행.
- [ ] F5: 분배금·실제 과세·호가 잔량·부분체결·09:01 이후 가격을 반영한 모델에서 비교. 일봉 Low 터치만으로 실제 체결을 보증하지 않음.
- [ ] F6: 새로운 미사용 기간에서 파라미터 동결 후 검증. 겹침창의 iid 부트스트랩·부호검정으로 유의성을 주장하지 않음.
- [ ] F7: 특정 사용자 포트에서 초기 진입이 적용되지 않으면 동결 params·시작일·보유 입력·최신 시세·무인 실행 상태를 읽기 전용으로 대조(이번 요청은 특정 계좌 미지정).

이번 변경은 독립 실행 연구 스크립트·문서뿐이다. 앱 매매 경로·설정·배포 변경 없음. 자동 전략 개정 또는 실거래 안전 승인에 해당하지 않는다.
