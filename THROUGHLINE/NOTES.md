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

## Docker / compose (추가)

- [2026-08-28] compose 서비스 재생성 시 nginx가 업스트림 IP를 캐시해 502를 반환한다 — api/web 재생성 후에는 `docker compose restart nginx` 필요. (근거: /api/health 502 재현 후 재시작으로 해소. 운영 개선: resolver + 변수 proxy_pass 는 TODO)

## Docker / compose (이미지 공유)

- [2026-08-28] compose에서 같은 build 컨텍스트라도 서비스마다 `build:`를 선언하면 **서비스별 별도 이미지**(exitme-api/exitme-worker/exitme-scheduler)가 생긴다 — `build api`만 재빌드하면 worker/scheduler는 옛 이미지로 남아 의존성 누락(ModuleNotFoundError)이 조용히 발생. api에 `image: stocklab-api`를 지정하고 worker/scheduler가 그 이미지를 공유하도록 통일. `restart`는 이미지를 갱신하지 않으므로 이미지 변경 후에는 `up -d --force-recreate`. (근거: 백테스트 QUEUED 고착 재현 후 해결)

## Next.js / lightweight-charts

- [2026-08-28] **Windows Docker bind mount 는 파일 변경 이벤트를 컨테이너에 전달하지 못한다** — Next dev 가 stale 컴파일을 계속 서빙(수정한 페이지가 반영 안 됨). `WATCHPACK_POLLING=true`(+CHOKIDAR_USEPOLLING) 로 해결, 소스 수정 → 8초 내 반영 실검증. (근거: 홈 리다이렉트 미반영 재현 후 해결)
- [2026-08-28] lightweight-charts `chart.remove()` 후 ref 를 null 로 비우지 않으면 라우트 전환/재렌더에서 이중 remove → "Object is disposed" 런타임 크래시(클라이언트 라우팅까지 마비). dispose 헬퍼(try/catch + ref null)로 통일. (근거: 차트 페이지 크래시 재현 후 수정)

## KIS Open API

- [2026-08-28] 일봉 API(FHKST03010100)는 호출당 최대 100건 — 140 달력일 창으로 페이지네이션하면 안전하다. 모의(vps)에서 TR ID V-치환은 주문 계열(T…)만 적용되고 시세 TR(FHKST…)은 공통. (근거: 공식 GitHub https://github.com/koreainvestment/open-trading-api 예제 분석 + 2026-08-28 실 호출로 10년 시딩 검증)
- [2026-08-28] **토큰 발급은 분당 1회 제한** — 짧은 간격 재발급 시 /oauth2/tokenP 가 403 반환. 프로세스 간 토큰을 Redis 로 공유하고 403 시 65초 대기 재시도로 해결. (근거: 실 호출 재현)
- [2026-08-28] **유량 초과 시 시세 API 가 HTTP 500 반환** — 호출 간 0.15s 스로틀 + 지수 백오프(1/2/4/8s)로 해결. (근거: 10년 시딩 중 재현)
- [2026-08-28] 주식일별분봉조회(FHKST03010230): 호출당 120건, 시간 커서(FID_INPUT_HOUR_1) 내림차순, 과거 보관 약 1년(2025-09-01 실 데이터 수신 확인). 응답 필드 stck_bsop_date/stck_cntg_hour/stck_oprc/hgpr/lwpr/prpr/cntg_vol. (근거: 실 호출 프로브)
- [2026-08-28] 통합 테스트가 실코드(069500)에 합성 데이터를 넣으면 실 시딩과 충돌한다 — 개발 DB 오염 확인 후 source='pykrx' 삭제로 복구. **테스트는 stocklab_ci DB 로 격리 실행** (DATABASE_URL 오버라이드). (근거: 2024년 inserted=0 재현·복구)
