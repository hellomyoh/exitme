# NOTES.md

<!-- 개발 중 학습한 "확인된 사실"만 기록한다. 추측·판단은 ASSUMPTIONS.md로. -->

## KODEX 연동 감사 (2026-09-12)

- 같은 제품 코드에서 현행 K200 그리드 생성가격 1,943개 검산 일치. 낮은 합산 주문선 체결률(11.57%)은 깊은 2·3단을 동일 1건으로 세는 영향이 있으며 수량률15.14%·금액가중률16.15%와 구분해야 한다. 실제 계좌 발주 성공률이 아니라 일봉 모의체결 지표다. 근거: [체결률 연구](docs/fill-rate-study-20260912.md).

- `45eb373`의 실전 상태 재생은 레버리지 전술 종류·K200 체결일 익절 스냅샷을 보존하지 않는다. 초기 수동 보유 근사만의 문제가 아니며 신규 체결에도 적용된다. 상세 코드 근거와 후속 실패 조건은 [감사 보고서](docs/kodex-linkage-audit-20260912.md) A1/A2와 [QA](qa/kodex-linkage-audit-20260912.md).
- 같은 기준에서 E=1.10·실제 보유 근사 노출=1.16인 전술 보유 반례가 주문 0건을 반환했다. 전략 기본 테스트 62개 통과와 별개다. 재현 명령은 위 QA에 보존했다.

## KRX / pykrx

- [2026-08-28] KRX 데이터포털(data.krx.co.kr)이 pykrx 1.0.51의 요청을 차단한다 — JSON 대신 "LOGOUT"(400) 반환. 브라우저 UA + JSESSIONID 세션 쿠키를 붙여도 동일. (근거: 컨테이너에서 requests로 직접 재현) → **시딩·수집은 KIS 키가 실질 필수.** pykrx 폴백 경로는 코드에 유지하되 현재 동작하지 않음.
- [2026-08-28] pykrx는 `pkg_resources`를 임포트한다 — setuptools 81부터 제거되어 `setuptools>=75,<81` 핀 필요. python:3.12-slim에는 setuptools 자체가 없음. (근거: 컨테이너 임포트 오류 재현 후 핀으로 해결)
- [2026-08-28] pykrx는 KRX 응답 오류를 삼키고 **빈 DataFrame을 반환**한다(예외 없음). 빈 응답을 실패로 처리하는 가드가 없으면 0건 시딩이 조용히 "성공"한다. (근거: 시딩 스모크에서 캘린더 전체가 휴장으로 오염되는 것 확인 → seed.py에 가드 추가)

## 테스트 DB 격리

- [2026-09-01] 개발 DB 오염 **재발** — qa/README의 수동 `DATABASE_URL=...stocklab_ci` 규칙만으로는 격리가 지켜지지 않는다. 컨테이너 안 `pytest` 직접 실행(compose 환경변수 = 개발 DB)으로 통합 테스트의 `seed_synthetic`이 069500/122630에 합성 봉 28개(휴장일 날짜만 — 실데이터와 겹치는 날짜는 `ON CONFLICT DO NOTHING`이 차단), 102110에 400개(실데이터 없어 전량 유입)를 `source='pykrx'`로 적재했다. (근거: `ingested_at` 타임스탬프와 pytest 실행 시각 일치 재현) → **conftest.py가 `_ci` 미접미 DATABASE_URL을 stocklab_ci로 강제 재지정 + DB 자동 생성·마이그레이션**으로 코드 강제화.
- [2026-09-01] 오염 탐지 쿼리 3종 (db 컨테이너에서 실행): ① 소스별 집계 — `SELECT i.code, d.source, count(*) FROM ohlcv_daily d JOIN instruments i ON i.id=d.instrument_id GROUP BY 1,2;` (`pykrx` 행은 전부 합성 — KRX 차단으로 pykrx 실수집 불가). ② 휴장일 봉 — `... LEFT JOIN trading_calendar tc ON tc.cal_date=d.trade_date AND tc.is_open WHERE i.market='KOSPI' AND tc.cal_date IS NULL`. ③ 일간 ±12% 초과 점프 나열(lag 윈도) — 실제 급변일(2026-03-04, 2026-07-31)도 나오므로 후보 목록으로 취급. 복구 = `DELETE FROM ohlcv_daily WHERE source='pykrx'` 후 해당 종목 재시딩.
- [2026-09-01] `tests/test_ws_quotes.py`는 **라이브 Redis를 앱(worker·scheduler)과 공유**해 간헐 실패한다(같은 캐시 키·채널 경합, 단독 재실행 통과 확인). DB와 달리 Redis는 격리 미적용 — 별도 개선 대상.

## 일봉 확정봉 가드

- [2026-09-07] **국내에서도 미완성 봉 오염이 발생**(2026-08-31 미국 사고의 재발). 운영 DB 의 `ohlcv_daily` 2026-09-07 행 8건이 **08:40·09:26 KST**(장 시작 20분 전·개장 26분 후)에 적재됐고 거래량이 102110=**0**, 122630=0, 069500=3, 005930=1 이었다 — 거래가 없던 시점의 스텁 봉. KIS 일봉 API 는 장 시작 전·장중에도 '오늘 날짜' 행을 돌려준다. (근거: `ingested_at` UTC→KST 환산 + 거래량, 평소 102110 거래량은 1,200만 주대)
- [2026-09-07] 이 유령 봉이 **09/08 주문표 5개 값 전부를 만들어냈다** — 가짜 종가 105,880 으로 역산한 그리드 101,640/97,405/93,170·익절 110,120·갭 97,308 이 화면값과 완전히 일치. 주문표 기준일이 'DB 의 마지막 일봉'이라, 봉 하나가 오염되면 주문 가격 전체가 오염된다.
- [2026-09-07] `upsert_daily_bars` 는 `on_conflict_do_nothing`(원본 불변, ADR-002)이라 **마감 후 진짜 종가가 들어와도 조용히 무시**되고 유령 봉이 영구 잔존한다 — MA·ATR·σ·백테스트까지 계속 오염. 복구는 해당 일자 DELETE 후 재적재뿐. → `upsert_daily_bars` 진입점에 **확정봉 가드**(`bar_is_final`: 미래 봉 거부, 오늘 봉은 시장 정규장 마감 후에만 허용 — KR 15:30 KST / US 16:00 ET)를 넣어 네 적재 경로를 한 곳에서 막았다.

## 매매일지 시세

- [2026-09-07] `ohlcv_daily` 에는 **전략 대상 6종(069500·102110·122630·QQQ·QLD·TQQQ)만 시딩**돼 있다. 매매일지가 그 밖의 종목(예: 005930)을 담으면 종가 조회가 실패해 평가에서 빠지고, `unrealized_pct` 의 분모(`cost_priced`)에서도 함께 빠져 "일부 종목만의 수익률"이 전체 수익률처럼 표시됐다. (근거: 3종목 일지 재현 — 원가 220만 중 120만(55%)이 분모 누락) → `enrich_valuation` 에 KIS 일봉 보충 경로 연결 + 커버리지 노출로 해결.
- [2026-09-07] 평가(`enrich_valuation`)와 수익률 차트(`journal_return_series`)가 **종목코드 해석을 각자 구현**하고 있었다 — 평가만 이름 매칭을 갖추자 "도넛·카드에는 +21.2% 로 뜨는 삼성전자가 차트에서만 '코드 없음'으로 빠지는" 화면 간 불일치가 발생(사용자 보고). 같은 도메인 규칙은 한 헬퍼(`_codes_by_name`)를 두 경로가 공유해야 한다.
- [2026-09-07] 수익률 차트용 `_ensure_daily_bars` 는 신규 종목 자동 등록·일봉 보충을 이미 하고 있었는데 **평가 경로만 이를 쓰지 않았다** — 같은 데이터가 화면 A(차트)에는 있고 B(평가 카드)에는 없는 불일치의 원인. 새 경로를 만들지 말고 기존 경로를 연결하는 것이 맞다.

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

- 2026-08-29 | σ 임계 매수 정지 게이트 검토 — 이벤트 스터디·엔진 절제 모두 역효과로 미채택. 방어는 E 연속 축소 + BEAR 정지가 담당. [docs/entry-gate-study-20260829.md](docs/entry-gate-study-20260829.md)
- [2026-08-31] **KIS 해외주식 기간별시세(HHDFS76240000)는 미 장마감 전 호출 시 당일의 미완성 봉을 반환한다** — 21:12 KST(프리마켓 08:12 ET) 수집에서 2026-08-31 QQQ/QLD 봉이 거래량 20만(평소 3,400만)으로 적재됨. 적재가 ON CONFLICT DO NOTHING(ADR-002 원본 불변)이고 `ingest_us_daily` 증분이 마지막 저장일에서 멈추므로 **미완성 봉은 자동 교체되지 않고 영구 잔존한다**. 미국 수집은 미 장마감 확정 후에만 당일 봉을 저장해야 한다. (근거: DB `ohlcv_daily` 실데이터, [docs/us-transfer-study-20260831.md](docs/us-transfer-study-20260831.md) §4)

- 2026-08-31 | 레짐 '추세 강도 마진'(BULL 진입에 MA20>MA60×(1+δ)) 실험 — δ 0.5~2% 전 구간에서 US 악화(19년 +720→661~708%), KR 노이즈, MDD 무개선 → **기각**. 약한 상승장 강등은 진짜 추세 초입 상실 + 중립 익절의 추세 절단 비용이 더 큼. 관련: 약한 추세 구간의 저수익은 판정 오류가 아니라 시장에 벌 거리가 없던 것.

## Celery beat / 헬스체크 (2026-09-08)

- 2026-09-08 09:01 `auto_execute_open` 이 돌지 않았는데(승인 3줄 `approved` 잔존, 워커·스케줄러 로그에 태스크 기록 없음) `docker compose ps` 는 두 컨테이너 모두 **healthy** 였다 — 종전 scheduler 헬스체크가 `python -c "import app.worker"`(모듈 import 성공 = 건강)라 beat 루프의 실제 발송 여부와 무관했고, worker 의 `celery inspect ping` 도 큐 소비 여부를 말하지 않는다. 그래서 ADR-009 §5 의 하트비트(beat 가 60초마다 태스크를 보내고 워커가 Redis 키를 갱신, 헬스체크는 키 존재 확인)를 두었다. 근본 원인(beat 미발송 vs 큐 미소비)은 미확정 — 사용자 측 로그 확인 대기.
- 하트비트 키 확인은 컨테이너 안 `python -c "import redis,os;print(redis.from_url(os.environ['REDIS_URL']).get('autoexec:pipeline:heartbeat'))"`. `start_period` 150초를 두지 않으면 첫 60초 틱 전에 unhealthy 로 뜬다. `docker compose restart` 는 헬스체크 정의 변경을 반영하지 않는다 — `docker compose up -d <svc>` 가 필요하다.

## KIS 국내휴장일조회 (2026-09-08 실측)

- `GET /uapi/domestic-stock/v1/quotations/chk-holiday`, tr_id `CTCA0903R`, 파라미터 `BASS_DT`(YYYYMMDD)·`CTX_AREA_NK`·`CTX_AREA_FK`(빈 문자열). 응답 `output` 은 **BASS_DT 부터 하루 한 행, 주말 포함**, 페이지당 24일. `ctx_area_nk` 에 다음 페이지의 BASS_DT 가 공백 패딩으로 온다(`'20261001            '`), `msg1` 은 "조회가 계속됩니다..". 필드 `wday_dvsn_cd` 는 01=일 … 07=토(2026-09-08 화요일 = 03), `opnd_yn` 이 개장 여부(`bzdy_yn` 영업일·`tr_day_yn` 거래일·`sttl_day_yn` 결제일과 별개). 미래 날짜도 준다 — 2026-09-24(목)·25(금) 추석 휴장이 `opnd_yn=N` 으로 확인됐다.
- pykrx `get_previous_business_days(year, month)` 는 이 환경에서 `'RangeIndex' object has no attribute 'month'` 로 실패하고 애초에 과거만 다룬다 — 미래 캘린더 소스로 쓸 수 없다.

## KIS 유량 한도 — 09:01 동시 호출 사고 (2026-09-09)

- 2026-09-09 09:01 첫 무인 실행에서 두 계좌가 다른 지점에서 EGW00201("초당 거래건수를 초과하였습니다")로 실패했다: 한 계좌는 시가 조회 뒤 **잔고 조회**가 4회 재시도(1/2/4/8초) 뒤에도 실패해 전 줄 생략, 다른 계좌는 그리드 1·2차 접수 뒤 **3차 주문 POST** 가 실패(주문 POST 는 재시도 없음이었다). 09:01 에는 같은 워커(동시성 4)에서 ① 실행기(포트마다 시가 1 + 잔고 1 + 줄마다 매수가능 1 + 주문 1) ② 10초 시세 폴링(전 종목, .env 앱키) ③ 08:30~09:10 예상 시가 폴링(.env 앱키)이 **서로 다른 프로세스**로 돌아, 인스턴스 단위 0.15초 스로틀은 프로세스를 넘지 못한다. 계좌들이 같은 KIS 앱키를 쓰거나(한 앱에 여러 계좌) .env 시세 키가 계좌 키와 같으면 한 앱키의 초당 건수가 합산된다. 모의(vps) 계좌는 한도가 초당 2건이라 실행기 혼자서도 넘길 수 있다.
- KIS 한도(문서): 실전 REST 초당 20건, 모의 초당 2건 — 앱키 기준. EGW00201 은 HTTP 500 으로 온다(조회는 본문 msg_cd 로 구분해 재시도).
- 조치(0.13.1): 앱키·환경별 **Redis 공용 초당 카운터**(실전 10, 모의 1) 를 모든 호출 앞에 둠 · 주문 POST 는 **EGW00201 에만** 1/2/4초 재시도(거절된 주문은 접수 전이라 중복 없음, 다른 오류는 종전대로 재시도 없음) · 실행기가 `autoexec:running` 플래그를 세우면 시세·예상 시가 폴링이 KIS 호출을 건너뜀 · 포트 사이 1초 간격.

## 텔레그램 Bot API 접속 (2026-09-09 실측)

- 개발 Mac(사내망)과 그 위 컨테이너에서 `api.telegram.org`·`core.telegram.org`·`t.me` 모두 **TCP 연결은 되나 TLS ClientHello 직후 Connection reset** (curl exit 35, requests `SSLEOFError UNEXPECTED_EOF_WHILE_READING`, verify=False 도 동일, google.com 은 정상) — 인증서 문제가 아니라 네트워크(DPI) 차단이다. 이 환경에서는 봇 토큰 검증·전송 테스트가 불가능하고, 운영 서버가 같은 망이면 알림도 같은 이유로 실패한다. 앱은 실패를 `notify.failed`(매매 로그 "알림 전송 실패")와 설정 › 알림의 "마지막 전송 실패" 줄로 드러낸다(0.14.2).
- 서버 쪽 확인 명령: `docker compose exec api python -c "import requests;print(requests.get('https://api.telegram.org/bot<토큰>/getMe',timeout=8).text)"` — `{"ok":true,...}` 면 망·토큰 정상, SSL/Connection 오류면 차단.

## Celery beat 상태 파일 이미지 혼입 — 배포마다 지난 크론 재실행 (2026-09-09 사고)

- 증상: 배포(`up -d --build`) 직후 텔레그램 일일 현황이 다시 오고 일봉 수집이 다시 돌았다. 서버 로그: `beat: Starting...` 바로 다음 줄에 `Sending due task daily-ingest`·`daily-snapshot`.
- 원인: beat 는 작업 디렉터리에 `celerybeat-schedule`(shelve, 항목별 last_run_at)을 쓴다. 서버가 초기에 dev override(바인드 마운트)로 돌던 시기에 이 파일이 `~/exitme/apps/api/` 에 생겼고(gitignore 라 pull 에도 남음), Dockerfile 이 `COPY . .` 이고 `.dockerignore` 가 없어 **배포마다 이미지에 복사**됐다. 새 컨테이너의 beat 가 낡은 last_run_at 을 읽고 "지난 크론"으로 판정해 기동 즉시 실행(Celery 5.4 `crontab.is_due` — `beat_cron_starting_deadline` 미설정이면 기한 없이 따라잡기). 즉시 실행된 두 항목이 정확히 9월 5일 이전부터 있던 항목(daily-ingest·daily-snapshot)이라 파일 시점이 그 이전임을 알 수 있었다. 무인 실행 항목은 파일에 없어 발주는 유발되지 않았지만, 서버에서 바인드 마운트로 한 번이라도 다시 돌면 배포마다 09:01 발주가 되살아날 수 있는 구조였다.
- 조치(0.15.0): `apps/api/.dockerignore`(`celerybeat-schedule*` 등) · beat 를 `-s /var/lib/celery/celerybeat-schedule` 로(코드 밖, Dockerfile mkdir) · `beat_cron_starting_deadline=3600` · 09:01/09:15 실행 `LATE_RUN_LIMIT` 09:30(넘으면 'late' 기록만) · 일일 현황 하루 1회(`notify.daily_status_sent`) · deploy.sh 가 빌드 전 `apps/api/celerybeat-schedule*` 삭제.
- 확인 명령: `docker run --rm --entrypoint sh stocklab-api -c 'ls -la /srv/app/celerybeat-schedule*'` → 없어야 정상. 로컬 dev 는 바인드 마운트라 `apps/api/celerybeat-schedule` 이 계속 생기지만 `-s` 로 읽지 않는다(지워도 무해).
- 일반 원칙: 프로세스 상태 파일(shelve·pid·캐시)은 빌드 컨텍스트에 두지 않는다. 컨테이너 재시작(`restart`)은 파일시스템을 유지하고 재생성(`up --build`)은 버린다 — 따라잡기 동작은 그 차이에 좌우된다.

## autoflush=False 세션 — 계산 전 flush (2026-09-09 사고: 반쪽 스냅샷)

- 증상: 대시보드 전일 대비 +5,746,150원, 챗봇(오늘 보유 × 가격 변동) +2,326,150원. 차이 3,420,000원 = 전날 21:42 등록한 매도 30주 × 114,000원 대금. 서버 복호화 조회로 09-08 M-신한-ETF 스냅샷이 주식 636주(매도 후)·현금은 매도 대금 누락(매도 전)인 반쪽 상태였다.
- 원인: `SessionLocal(autoflush=False)`. 거래 등록은 매도 시 기존 로트 객체 수량을 메모리에서 줄이고 새 거래 행을 `add` 한 뒤 flush 없이 `compute_user_snapshot` 을 호출한다. 로트 조회는 identity map 의 변경된 객체를 돌려주지만 거래 행 조회에는 아직 INSERT 되지 않은 새 거래가 없다 → 주식은 매도 후, 현금은 매도 전. 입금·매수도 한 거래씩 늦게 반영된다(로컬 재현: 입금 직후 cash 0, 매수 직후 stock 0). 대시보드를 다시 열면 커밋 데이터로 고쳐지지만, 그날 열지 않으면 반쪽 값이 남고 다음 날 전일 대비·추이에 거래 금액이 손익처럼 나타난다.
- 조치(0.15.1): 등록·삭제·예수금 보정 경로에 `session.flush()` 후 재계산 · `app/services/snapshot_repair.py`(as-of 원장 재계산, dry-run 기본)로 저장된 날짜 보정 · `tests/test_snapshot_consistency.py`.
- 원칙: autoflush 가 꺼진 세션에서 "방금 add/변경한 것을 다시 조회해 계산"하는 코드는 반드시 그 앞에 flush. 특히 계산 결과를 **저장**하는 경로(스냅샷·요약 캐시)에서 치명적이다.
