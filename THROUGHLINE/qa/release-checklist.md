# 릴리즈 체크리스트

배포 전 최종 검수. 전 항목 완료 후 배포한다.

## 테스트

- [ ] 전체 자동 테스트 실행 결과 캡처(명령·요약) → [HISTORY.md](../HISTORY.md) 기록 — 실행 없는 통과 주장 무효
- [ ] [regression-checklist.md](regression-checklist.md) 전 항목 수행
- [ ] [manual-test-cases.md](manual-test-cases.md) 변경 영향 항목 수행
- [ ] CI e2e(compose) green

## 환경·Secret

- [ ] `.env.example`이 실제 필요 변수와 일치(`KIS_APP_KEY`, `KIS_APP_SECRET`, `DATABASE_URL`, `REDIS_URL`, `ENCRYPTION_KEY`, `JWT_SECRET`)
- [ ] Secret·키·토큰이 커밋·로그에 없음 (grep 점검)
- [ ] prod compose의 healthcheck·restart 정책 확인

## 데이터

- [ ] DB 마이그레이션 적용·롤백 스크립트 확인
- [ ] `pgdata` 백업 최신본 존재, 복구 절차 1회 리허설([operator-guide.md](../docs/operator-guide.md))
- [ ] 배포 후 첫 일일 배치(시세 수집→시그널) 정상 완료 확인 계획

## 화면·고지

- [ ] "모의·투자 권유 아님" 고지가 시뮬레이터·실전·주문표 화면에 표시
- [ ] 시세 기준시각·지연 표기 동작

## 모니터링·롤백

- [ ] 구조화 로그 수집 확인, `batch_runs` 조회 가능
- [ ] 롤백 방법(직전 이미지 태그 재배포) 문서화·리허설
- [ ] 알려진 이슈 목록 정리 → 릴리즈 노트
