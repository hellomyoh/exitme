# ADR-003: JWT access + refresh 회전 인증

## 상태

Accepted

## 배경

회원 단일 등급, 본인 데이터만 접근([REQUIREMENTS §9](../SOURCES/REQUIREMENTS.md)). REST + WS 양쪽에서 인증이 필요하다.

## 선택지

1. 서버 세션 (쿠키)
2. JWT access(단기) + refresh(회전)
3. OAuth 외부 IdP

## 결정

2안. access 15분(메모리 보관), refresh 14일(httpOnly Secure 쿠키, 회전). WS는 핸드셰이크 시 토큰 검증.
개인 계좌정보(수량·단가·금액)는 AES-GCM 애플리케이션 레벨 암호화, 종목 코드는 평문([검토 로그](../discussion/review-portfolio-20260828.md) Security 절).

## 이유

REQUIREMENTS §9가 JWT+refresh를 명시. 단일 서버라 세션도 가능하나 WS·API 이중 채널에 토큰이 단순하다. 외부 IdP는 범위 과잉.

## refresh 수명 변경 이력 (결정 §19 의 14일은 실행되지 않았다)

원안은 refresh 14일이었으나 구현은 1시간으로 시작했고, 이후 두 번 사용자 지시로 바뀌었다.
ADR 본문은 당시 결정의 기록이므로 고치지 않고 여기에 실제 값을 적는다 — **ARCHITECTURE §6 이 현행 값의 권위**다.

| 시점 | refresh TTL | 사유 |
|---|---|---|
| 최초 구현 | 1시간 | 원안 14일 대비 보수적으로 시작 |
| 2026-09-02 지시 | 3시간 | 잦은 로그아웃 불편 |
| 2026-09-14 지시 | 12시간 | 하루 한 번 열면 그날은 유지되도록 |
| **2026-09-15 지시** | **24시간** | 어제 쓰던 탭이 오늘 그대로 열리도록 |

access 15분·회전·httpOnly Secure SameSite=strict 쿠키는 모두 그대로다. 늘어난 것은 **완전히 손을 뗀 뒤
재로그인까지의 허용 시간**이며, 쿠키 탈취 시 유효 기간도 같이 늘어난다는 점은 감수한 트레이드오프다.
단일 사용자·개인 계좌 도구라는 전제에서 편의를 택했다. 서버는 refresh 를 즉시 무효화할 수단이 없으므로
(무상태 JWT), 분실·유출이 의심되면 `JWT_SECRET` 교체가 유일한 전면 로그아웃 수단이다.

## 영향

토큰 갱신 흐름·WS 재연결 처리 필요. 암호화 필드는 DB 집계 불가 → 총자산 집계는 앱 레벨.

## 관련 feature / ARCHITECTURE 항목

[ARCHITECTURE §6](../ARCHITECTURE.md), [feature-portfolio.md](../features/feature-portfolio.md), [feature-dashboard.md](../features/feature-dashboard.md)
