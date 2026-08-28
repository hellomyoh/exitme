# NOTES.md

<!-- 개발 중 학습한 "확인된 사실"만 기록한다. 추측·판단은 ASSUMPTIONS.md로. -->

## KRX / pykrx

- [2026-08-28] KRX 데이터포털(data.krx.co.kr)이 pykrx 1.0.51의 요청을 차단한다 — JSON 대신 "LOGOUT"(400) 반환. 브라우저 UA + JSESSIONID 세션 쿠키를 붙여도 동일. (근거: 컨테이너에서 requests로 직접 재현) → **시딩·수집은 KIS 키가 실질 필수.** pykrx 폴백 경로는 코드에 유지하되 현재 동작하지 않음.
- [2026-08-28] pykrx는 `pkg_resources`를 임포트한다 — setuptools 81부터 제거되어 `setuptools>=75,<81` 핀 필요. python:3.12-slim에는 setuptools 자체가 없음. (근거: 컨테이너 임포트 오류 재현 후 핀으로 해결)
- [2026-08-28] pykrx는 KRX 응답 오류를 삼키고 **빈 DataFrame을 반환**한다(예외 없음). 빈 응답을 실패로 처리하는 가드가 없으면 0건 시딩이 조용히 "성공"한다. (근거: 시딩 스모크에서 캘린더 전체가 휴장으로 오염되는 것 확인 → seed.py에 가드 추가)

## TimescaleDB

- [2026-08-28] 하이퍼테이블에 `INSERT ... ON CONFLICT`를 실행하면 SQLAlchemy `rowcount`가 -1로 반환된다 — 삽입 건수는 `RETURNING`으로 세어야 한다. (근거: 통합 테스트 실패 재현 후 RETURNING으로 해결)

## Docker / compose

- [2026-08-28] busybox wget(alpine 계열 이미지)은 `localhost`를 IPv6(::1)로 먼저 해석한다 — IPv4만 리슨하는 서비스(Next dev, nginx `listen 80`)의 healthcheck는 `127.0.0.1`을 써야 한다. (근거: web/nginx healthcheck connection refused 재현 후 해결)
- [2026-08-28] compose exec-form healthcheck(`["CMD", ...]`)에서는 `$$VAR` 셸 확장이 일어나지 않는다(셸이 없음) — celery ping은 `-d celery@$$HOSTNAME` 없이 전체 ping으로. (근거: worker unhealthy 재현 후 해결)

## Docker / compose (계속)

- [2026-08-28] compose 익명 볼륨(`/srv/web/node_modules`)은 이미지를 재빌드해도 기존 컨테이너의 것을 재사용한다 — 의존성 추가 후에는 `docker compose up -d -V <svc>`로 익명 볼륨을 갱신해야 한다. (근거: lightweight-charts 미해석 500 재현 후 -V로 해결)
- [2026-08-28] email-validator(pydantic EmailStr)는 `.local` 등 특수 도메인을 기본 거부한다 — 테스트 계정은 실 TLD 형태 사용. `Secure` 쿠키는 http TestClient로 전송되지 않는다 — `base_url="https://testserver"` 사용. (근거: 테스트 실패 재현)

## KIS Open API

- [2026-08-28] 일봉 API(FHKST03010100)는 호출당 최대 100건 — 140 달력일 창으로 페이지네이션하면 안전하다. 모의(vps)에서 TR ID V-치환은 주문 계열(T…)만 적용되고 시세 TR(FHKST…)은 공통. (근거: 공식 GitHub https://github.com/koreainvestment/open-trading-api 예제 분석; 실 호출 검증은 키 기입 후 예정)
