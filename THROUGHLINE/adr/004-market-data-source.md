# ADR-004: 시세 소스 — KIS Open API 주 + pykrx 보조

## 상태

Accepted

## 배경

일봉·분봉·현재가가 필요하고 v1은 시세 조회만 사용한다([REQUIREMENTS §5](../SOURCES/REQUIREMENTS.md)).

## 선택지

1. KIS Open API 단독
2. pykrx 단독
3. KIS 주 + pykrx 보조(시딩·검증·폴백)

## 결정

3안. **우선순위: 같은 데이터에 대해 KIS 우선, pykrx는 결측 보충·검증·10년 시딩용.** 불일치 발견 시 로그 기록.
pykrx 적재분은 OHLC 관계식(`low ≤ open,close ≤ high`)·결측·중복 검증 통과분만 저장한다.
KIS 장애·한도 초과 시 pykrx 폴백 + 화면 "시세 지연" 표기.

## 이유

KIS는 공식 API지만 과거 데이터 대량 시딩에 부적합, pykrx는 시딩에 강하나 비공식 크롤링이라 스키마 변경 위험([검토 로그](../discussion/review-market-data-20260828.md)). 상호 보완이 유일한 현실안.

## 영향

`KIS_APP_KEY`/`KIS_APP_SECRET` 서버 보관·로그 마스킹 의무. KIS 호출 한도는 Phase 1 실측 후 폴링 주기 확정.

## 관련 feature / ARCHITECTURE 항목

[ARCHITECTURE §2·§7](../ARCHITECTURE.md), [feature-market-data.md](../features/feature-market-data.md)
