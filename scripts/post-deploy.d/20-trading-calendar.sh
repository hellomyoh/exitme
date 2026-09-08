# 배포 후 훅 20 — 거래일 캘린더 갱신 (2026-09-08, ADR-009 §2-3 선결). KIS 국내휴장일조회(CTCA0903R)로 오늘부터 120일을 채운다.
# 휴장이 등록되지 않으면 그날 09:01 은 '시가 확인 실패', 다음 거래일은 '기준일 불일치'로 발주하지 않는다 — 무인 운영의 선결 조건.
# 멱등: 같은 값은 건드리지 않고 바뀐 날만 갱신·출력. 워커도 매주 일요일 06:00 에 같은 일을 한다.
out="$("${COMPOSE[@]}" exec -T api python -m app.services.calendar --days 120 2>/dev/null)" || fail "거래일 캘린더 갱신 실패 — KIS 키(.env KIS_APP_KEY/KIS_APP_SECRET)·네트워크 확인: ${out:-응답 없음}"
echo "· $out"
