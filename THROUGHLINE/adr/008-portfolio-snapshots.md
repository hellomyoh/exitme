# ADR-008: 포트 단위 자산 스냅샷 신설 — 사용자 스냅샷은 합산 유도

## 상태

Accepted

## 배경

대시보드 개편(2026-09-02 사용자 지시·결정)으로 ① 자산 추이의 **포트별 다선** 표시, ② 한국/미국 주식 자산 구분 카드(미국은 $ 별도 표기 — 환율 미도입), ③ 실전매매 손익 비율(이중 기준)이 필요하다.
현행 [asset_snapshots](../features/feature-dashboard.md)는 사용자 단위 4값(total/stock/cash/other)뿐이라 포트별 시계열이 없고, `compute_user_snapshot`은 float 누적·로트당 `latest_close` 조회(N+1)·SELECT-then-INSERT(경합)·배치 UTC 날짜(API는 KST) 등 구조 문제가 검토에서 확인됐다 ([검토 로그](../discussion/review-dashboard-asset-breakdown-20260902.md)).

## 선택지

1. asset_snapshots에 포트별 JSONB 컬럼 추가
2. **portfolio_snapshots 테이블 신설 + 사용자 스냅샷을 포트 합산으로 유도 계산**
3. 스냅샷 없이 조회 시점 재계산(거래 재생)

## 결정

2안.

```text
portfolio_snapshots(
  id, portfolio_id FK→portfolios ON DELETE CASCADE,
  snap_date date, equity🔒, stock_value🔒, cash🔒,   -- 🔒 EncryptedBigInt (정수 확정 후 저장)
  currency text (KRW|USD — 적재 시점 비정규화),
  UNIQUE(portfolio_id, snap_date)
)
```

- **원천·유도 구조**: 포트 스냅샷을 정수로 먼저 확정하고, 사용자 asset_snapshots = Σ(KR 포트 equity) + other 로 같은 트랜잭션에서 유도한다. 반올림·시점 불일치로 인한 "합산 ≠ total" 불변식 붕괴를 구조적으로 제거.
- **적재**: `INSERT … ON CONFLICT (portfolio_id, snap_date) DO UPDATE` 단일문(경합 제거). 날짜는 `kst_today()`로 통일 — 배치의 UTC `date.today()` 사용은 기존 결함으로 함께 수정.
- **통화**: US 포트는 센트 정수 그대로 적재하고 행에 `currency` 저장(값 단위를 행 안에 보존 — 환율 도입 시 백필 없이 환산 가능). KRW 합산·차트에는 `currency='KRW'`만 사용.
- **FK CASCADE**: `delete_portfolio`의 수동 자식 삭제 목록에 의존하지 않는다(portfolio_plans 0008 FK 위반 사고 재발 방지).
- **일반 테이블(하이퍼테이블 불채택)**: 1,000사용자×5포트×365일 ≈ 180만 행/년(~400MB), btree(portfolio_id, snap_date) 범위 스캔으로 충분. 값이 암호문이라 continuous aggregate 등 TimescaleDB 이득을 쓸 수 없음. 1억 행 도달 전 재평가.

## 이유

포트별 시계열은 카드·차트·향후 포트 비교의 공통 원천이며, 사용자 스냅샷을 유도값으로 재정의하면 정합 불변식(`Σ포트 + other = total`)이 테스트 가능한 계약이 된다. JSONB(1안)는 포트 추가·삭제 시 部分 갱신과 조회가 모두 불리하고, 조회 시점 재계산(3안)은 대시보드 <1.5s 예산과 암호화 복호 비용에 반한다.

## 영향

- 마이그레이션 0011 (테이블 신설). 과거 포트별 데이터는 없음 — 도입 시점부터 축적 (사용자 합산선은 기존 asset_snapshots로 소급 표시 유지).
- `compute_user_snapshot` 재작성: 종목 일괄 시세 조회(`DISTINCT ON`), 포트→사용자 유도, ON CONFLICT upsert.
- 거래 등록·삭제 시 당일 스냅샷 즉시 재계산(유령값 방지 — 검토 Q1).
- `/portfolio/trend` 응답에 `series[{portfolio_id,name,market,currency,points}]` 추가(기존 `items` 비파괴). ALL 구간은 주 단위 샘플.
- 손익 비율 계약: 순손익% = 순손익 ÷ 납입원금(입금−출금), 평가손익% = 평가손익 ÷ 보유원가 — **분모 ≤ 0이면 %는 null, 금액은 항상 표시**.

## 관련 feature / ARCHITECTURE 항목

[feature-dashboard.md](../features/feature-dashboard.md), [feature-portfolio.md](../features/feature-portfolio.md), [검토 로그](../discussion/review-dashboard-asset-breakdown-20260902.md)
