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

## 영향

토큰 갱신 흐름·WS 재연결 처리 필요. 암호화 필드는 DB 집계 불가 → 총자산 집계는 앱 레벨.

## 관련 feature / ARCHITECTURE 항목

[ARCHITECTURE §6](../ARCHITECTURE.md), [feature-portfolio.md](../features/feature-portfolio.md), [feature-dashboard.md](../features/feature-dashboard.md)
