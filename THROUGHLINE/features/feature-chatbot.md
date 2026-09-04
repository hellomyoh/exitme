# feature-chatbot — 매매 도우미 챗봇 (2026-09-04)

사용자 지시: 우측 대화 패널(아이콘으로 열고 X 로 닫기), 매매법 대화용 DB 접근 도구,
OpenRouter 프로바이더(API 키만 넣으면 동작), 현 디자인과 정합.

## 1. 하네스 설계 (기획 검토)

```text
web ChatBot ── POST /chat {messages[≤40]} ──▶ api chat.py
                                             system(전략 지식+오늘 날짜) + 대화
                                             ┌─ OpenRouter /chat/completions (tools, auto)
                                       루프 ≤6│   tool_calls? ─▶ _run_tool(user_id 스코프) ─▶ role:tool 로 회신
                                             └─ content ─▶ SSE {"type":"final"}
        SSE 진행: {"type":"tool","label":"주문표"} … / 실패: {"type":"error"}
```

- **무상태 서버**: 대화 이력은 클라이언트 보관(최근 20턴 전송) — DB 스키마 추가 없음. 패널은
  닫아도 언마운트하지 않아 세션 내 대화 유지.
- **도구는 전부 읽기 전용 + user_id 스코프** (쓰기 도구 금지 — LLM 이 원장·설정을 변경할 수 없음):
  list_portfolios · portfolio_summary · portfolio_journal · order_sheet(실주문표 디스패치와 동일:
  US→TF, KR→RAVG) · list_backtests · algorithm_params · price_history. 기존 엔드포인트 함수 재사용.
- **오류 격리**: 도구 실패는 `{"error":…}` 로 모델에 전달(대화 지속), 업스트림 실패는 SSE error
  이벤트로 사용자에게 표시. 도구 루프 상한 6회.
- **프로바이더**: OpenRouter(OpenAI 호환 tool-calling). `.env` `OPENROUTER_API_KEY`(필수),
  `OPENROUTER_MODEL`(기본 anthropic/claude-sonnet-4.5 — openrouter.ai/models 의 id 로 교체 가능).
  키 미설정 시 503 + 안내 문구가 말풍선으로 표시된다. 키는 서버에만 있고 브라우저로 나가지 않는다.

## 2. UI

- 우하단 플로팅 💬(accent) → 우측 고정 패널(400px, bg-surface·border-line — 기존 토큰),
  헤더(브랜드 도트+제목, 새 대화, ✕), 말풍선(사용자 accent-dim / 봇 inset), 진행 상태
  ("주문표 조회 중…"), 예시 질문 4개, Enter 전송(Shift+Enter 줄바꿈, IME 조합 가드).
- 면책 문구 상시: 모의·과거 데이터 기반, 투자 권유 아님.

## 3. 검증

- pytest 4건: 미인증 401 / 키 미설정 503 / 도구 루프 SSE(tool→final, 사용자 스코프 빈 목록) /
  도구 오류의 모델 전달 / 업스트림 실패 error 이벤트 — 전체 142 passed.
- 헤드리스: 버튼→패널 열림, 예시 질문→전송→503 안내 말풍선, ✕ 닫기, 재열림 시 대화 유지, tsc 클린.
- 실 LLM 응답은 키 투입 후 확인 필요(코드 경계 `_openrouter_call` 은 테스트에서 대체).

## 4. 운영

- 원격: `.env` 에 `OPENROUTER_API_KEY=sk-or-...` 추가 → `docker compose -f docker-compose.yml
  -f docker-compose.prod.yml up -d --build` (restart 는 .env 미반영 주의).
- 후속 후보: 응답 델타 스트리밍, 대화 저장(서버), 마크다운 렌더링, 도구에 시뮬 실행(쓰기) 추가 여부는
  별도 승인 필요.

## 5. 추가 지침·프롬프트 구조 (2026-09-04 후속)

- 시스템 프롬프트 재작성: 역할(가능/불가능 명시 — 읽기 전용, 실행·설정 변경은 화면 안내),
  정본 전략 요약(RAVG v2.5·TF·B안·전량 익절 근사), 도구 사용 규칙(수치는 반드시 조회,
  포트 모호하면 확인, 오류 은폐 금지, 센트→$ 환산), 답변 스타일(**핵심만 간결하게** — 결론 우선,
  서론 금지, '자세히' 요청 시에만 상술).
- 일반 설정 > "챗봇 추가 지침"(user_settings.chat_prompt, 0012): 내장 프롬프트 **뒤에**
  "사용자 추가 지침 (충돌 시 내장 규칙 우선)" 헤더로 덧붙음 — 안전 규칙 대체 불가. ≤4000자.
- E2E: 지침 "서명 붙여라" 저장 → 실 대화에서 list_portfolios→portfolio_summary 체인 후
  "총자산 82,426,502원" + 서명 확인. 테스트 5건(왕복·시스템 메시지 순서 포함) — 전체 143 passed.

## 6. 시스템 프롬프트 전역 편집 — 관리자 전용 (2026-09-04 후속)

3층 구조 확정: ① 코어 계약(코드 고정 — 도구 근거·읽기 전용·단위 환산, 본문과 충돌 시 우선)
② 본문(역할·전략 지식·스타일) — app_settings.chat_system_prompt(0013) 로 **관리자만 전체 교체**,
전 사용자 공통. 비우면 내장 기본 복귀 ③ 개인 추가 지침(현행). 일반 설정의 관리자 카드에서
기본값 불러오기/저장/초기화 제공, 교체 중에는 "이후 전략 개정이 자동 반영되지 않음" 경고 배지.
검증: 관리자 전용 403 · 교체 시 코어 계약·추가 지침 유지 · 초기화 복귀 (테스트 6건, 전체 144 passed),
헤드리스로 카드 표시→교체→경고 배지→초기화 복귀 확인.

## 7. 답변 렌더링 — 마크다운 채택, HTML 기각 (2026-09-04 후속)

- 검토: ① LLM HTML 출력 직접 렌더 — dangerouslySetInnerHTML+새니타이저(DOMPurify)로 가능은 하나,
  봇 출력이 도구 데이터·사용자 입력의 영향을 받으므로 XSS 표면이 생기고(액세스 토큰이 JS 메모리에
  있어 탈취 리스크), 모델별 HTML 품질 편차·디자인 이탈 문제 — **기각**. ② 마크다운 — 모델이 이미
  마크다운으로 답하고(스타일 지침도 표 지시), react-markdown 은 raw HTML 을 렌더하지 않아(skipHtml)
  안전 — **채택**.
- 구현: react-markdown+remark-gfm(GFM 표), 봇 말풍선만 적용(사용자 말풍선은 평문), 표는 말풍선 내
  가로 스크롤, 디자인 토큰으로 컴포넌트 오버라이드. 링크는 새 탭+noreferrer.
- 검증: 실 LLM 응답에서 표 1·굵게 8 렌더, 원문 ** 노출 0 (헤드리스 스크린샷), tsc 클린.
