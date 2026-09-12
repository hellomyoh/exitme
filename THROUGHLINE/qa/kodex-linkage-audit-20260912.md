# KODEX 연동 감사 실행 기록·후속 QA (2026-09-12)

기준 `45eb373`, [결과 보고서](../docs/kodex-linkage-audit-20260912.md). 체크되지 않은 항목은 **수정·회귀 검증 미완료**이지 감사 미수행을 뜻하지 않는다.

## 실제 실행

주 에이전트: 아래 명령 **62 passed in 2.34s**.

```powershell
docker compose exec -T api python -m pytest -q tests/test_strategy_planner.py tests/test_strategy_backtest.py tests/test_indicators.py tests/test_bootstrap_entry.py
```

독립 Quant: 위 범위에 `tests/test_signals.py::test_truncated_backtest_final_plan_equals_full_run_plan` 추가, **63 passed, 2 warnings in 2.93s**. 중복 테스트 수를 합산하지 않는다.

읽기 전용 실제 시세 재실행(보고서 표의 FULL/NO_LEV/NO_EXTRA_FEE):

```powershell
docker compose exec -T api python -c "import hashlib,json; from datetime import date; from dataclasses import replace; from sqlalchemy import text; from app.db import SessionLocal; from app.backtests import load_aligned_bars; from app.strategy.backtest import run_backtest; from app.strategy.params import Params,AblationFlags; s=SessionLocal(); s.execute(text('SET TRANSACTION READ ONLY')); a,b,fp=load_aligned_bars(s,date(2017,1,2),date(2026,9,11)); print('bars',len(a),a[0]['date'],a[-1]['date'],'fingerprint',fp,'sha256',hashlib.sha256(json.dumps([a,b],sort_keys=True,separators=(',',':')).encode()).hexdigest()); p=Params(); print('params',p); configs=[('FULL',p),('NO_LEV',replace(p,flags=AblationFlags(f4_leverage=False))),('NO_EXTRA_FEE',replace(p,fee_200=0,fee_lev=0))]; [(print(name,run_backtest(a,b,100000000,q).kpi)) for name,q in configs]; s.rollback(); s.close()"
```

후속 데이터가 적재되면 fingerprint가 달라질 수 있다. 원본 내용 해시는 보고서 §4에 보존했다. 이 명령은 백테스트 결과를 DB에 저장하지 않는다.

A3 합성 반례 실행:

```powershell
docker compose exec -T api python -c "from tests.test_strategy_planner import *; p=plan(I,mk_market(sigma_down=.15,sigma_ref=.13,sigma20=.2),mk_lev(),Regime.BULL,pf_with(0,[Lot(K200,1200,70000,'core',None,0),Lot(LEV,350,20000,'lev_strat',None,0),Lot(LEV,225,20000,'lev_tact1',None,0),Lot(LEV,225,20000,'lev_tact2',None,0)]),P); print('target',p.e_target,'actual',1.16,'orders',p.orders)"
```

실제 출력: `target 1.1 actual 1.16 orders ()`. 출력의 actual은 입력 보유액에서 수기 계산한 값이며 엔진이 반환하는 값은 아니다.

## 실패 조건 추적

- [x] A1 ✅ #201 — 전술1·2 실제 체결을 재생해 다음 날에도 종류 보존. EMA 회복 시 전술만 청산, 중복 진입 없음. 부분체결·FIFO·수동 보유·재실행 포함.
- [x] A2 ✅ #201 — 신규 grid의 체결일 익절가가 다음 종가 변동에도 불변. 코어 전환가는 전환일에 한 번 설정. 초기 수동 보유 예외와 분리하고 시딩 소멸 후 동일성 확인.
- [x] A3 ✅ #203 (ADR-012) — E 1.30→1.10, 기존 전술 보유에서 전체 레버리지 목표 초과가 밴드 밖이면 축소. E=1 경계·EMA 동시 청산 시 매도 중복 없음.
- [x] A4 ✅ 본 PR 명세 확정 — 실제 ETF 시장가격과 합성 비용 전 가격을 구분. 운용보수 중복 차감 제거 여부를 명세 확정 후 검증. 거래비용은 유지.
- [x] A5 ✅ #204 — 한 종목의 정상 거래일 봉 삭제 시 명시적 오류. 두 배열 길이가 같아도 날짜가 다르면 거부. 실제 휴장·정지와 구분.
- [x] A6 ✅ #204 — OHLC/adj_factor 정정 시 fingerprint 변경. 대상 기간 밖 변경의 정책 명시. 원본·수정 버전 재현 가능.
- [x] A7 ✅ 본 PR 명세 확정(분배금 미모델 명시) — 분배금 지급 또는 적정 총수익 자료, 동일 세금·비용 벤치마크, 실제 보유 노출 별도 집계. 체결 모델의 순서·부분체결 민감도 검증.
- [x] A8 ✅ #202 — 두 종목 주문 총액+수수료가 공통 가용 현금·버퍼를 준수. 레버리지 시장가 상승 갭과 K200 동시 체결에서도 예산 안전.
- [x] A9 ✅ #204 문구 확정 — 최초 평가일 Neutral 고정인지 초기 prior만 Neutral인지 권위 확정. 최초 출력 레짐·E·레버리지 주문을 회귀 테스트로 고정.
- [x] A10 ✅ #204 — 비용의 음수·비유한값·비현실적 상한 초과를 명시적 검증 오류로 거부.
- [x] A11 ✅ #204 — 과거 target에 미래 봉이 있어도 계약에 따라 과거까지만 계산하거나 과거 실행 요청을 거부. 최신일로 조용히 바꾸지 않음. 실제 배치 d일 동일성·쿼리 범위 검증.
- [x] A12 ✅ #204 — 포트별 동결 E와 w_200/w_lev/기준일/지표가 동일 계획에서 나옴. 공용 모델과 파라미터를 다르게 설정해 응답 불변식 검사.
- [x] 테스트 공백 ✅ #204 (G3 3점) — pass 자리표시자를 실제 어서션으로 교체하고 명세 G3의 0.35/0.90/1.30을 모두 검사. 다중 날짜 절단·실전 원장 재생 동일성 확인.
- [ ] 성과: 파라미터 고정 후 미사용 구간·국면별 워크포워드, 블록 신뢰구간, 동일 조건 벤치마크와 비교. 겹친 시작일을 독립 표본으로 계산하지 않음.
- [ ] 수동 QA: 모의 계좌의 전술 부분체결·다음 날 청산·그리드 익절 고정·동결 주문표·재실행 중복을 원장과 대조. 실제 발주는 이번 감사에서 수행하지 않음.

릴리즈 검수·전체 회귀는 미수행. 감사 산출물만 변경하므로 운영 기능 회귀 영향은 없으며, 위 항목 수정 시 전략·실전·브로커 회귀가 필요하다.

## 후속 조치 결과 (2026-09-12)

체크된 항목은 코드·명세 수정과 자동 테스트까지 완료(PR 번호 참조). 검토 의견·결정 근거: [kodex-linkage-audit-review-20260912](../docs/kodex-linkage-audit-review-20260912.md).
- 실전 재구성 = 백테스트 정본 동일성 테스트(`tests/test_lot_tags.py`)가 A1·A2·A3(lev_cap 귀속)을 함께 고정한다.
- 미완: **성과**(파라미터 고정 후 미사용 구간·워크포워드·블록 신뢰구간·동일 비용 벤치마크)와 **수동 QA**(모의 계좌 장중 대조)는 별도 판단 필요 — 검토 의견 §7.

