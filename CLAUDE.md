# CLAUDE.md

상세 작업 규칙은 루트의 AGENTS.md를 **반드시 먼저 읽고** 따르라.
프레임워크 문서는 루트 3파일(README.md/AGENTS.md/CLAUDE.md)을 제외하고 모두 THROUGHLINE/ 안에 있다.

## 오작동 방지 (AGENTS.md를 읽지 못한 경우에도 지킬 것)

- 산출물을 THROUGHLINE/ 밖에 만들지 않는다.
- 프로젝트를 재초기화하지 않는다 (KICKOFF/ADOPT 재실행 금지).
- 테스트는 실제로 실행한 결과만 보고한다. 실행 없이 통과를 주장하지 않는다.
- 코드와 명세가 다를 때 임의로 명세를 고쳐 불일치를 지우지 않는다 (권위 진단 — AGENTS.md 참조).
- main/master 직접 push 금지. Secret·인증서·개인키·토큰 파일 commit 금지.
- THROUGHLINE/SOURCES/ 원본과 discussion/ 로그는 수정하지 않는다 (불변).
- 되돌리기 어려운 삭제·파괴적 변경과 ARCHITECTURE 횡단 계약 변경은 사용자 확인 후 수행한다.
