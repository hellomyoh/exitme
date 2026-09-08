# 배포 후 훅 30 — beat → ingest 큐 → 워커 하트비트 확인 (ADR-009 §5). 60초 주기 태스크가 Redis 키를 찍어야 09:01 배치도 돈다.
# 컨테이너 헬스체크(start_period 150초)와 같은 키를 본다. 최대 150초 대기.
for _i in $(seq 1 30); do
  hb="$("${COMPOSE[@]}" exec -T redis redis-cli get autoexec:pipeline:heartbeat 2>/dev/null | tr -d '[:space:]')"
  if [[ -n "$hb" ]]; then
    echo "· 하트비트 $hb (beat 발송 → 워커 소비 정상)"
    "${COMPOSE[@]}" ps worker scheduler --format 'table {{.Service}}\t{{.Status}}' 2>/dev/null || true
    exit 0
  fi
  sleep 5
done
fail "하트비트 없음(150초) — 09:01 무인 실행도 돌지 않습니다. 확인: docker compose logs scheduler worker --tail 50"
