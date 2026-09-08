# 배포 후 훅 10 — ADR-009 전환 확인 (2026-09-08). 승인 단계 폐지로 09:01 실행기가 읽지 않는 approved 행이 남아 있으면 실패.
# 정리 자체는 alembic 0025 가 한다(정확히 한 번) — 여기서는 적용됐는지만 본다.
n="$("${COMPOSE[@]}" exec -T api python -c "
from sqlalchemy import text
from app.db import SessionLocal
with SessionLocal() as s:
    print(s.execute(text(\"SELECT count(*) FROM broker_orders WHERE status = 'approved'\")).scalar())
" 2>/dev/null | tr -d '[:space:]')"
[[ "$n" =~ ^[0-9]+$ ]] || fail "approved 잔존 건수를 읽지 못했습니다 (api 컨테이너·DB 확인)"
[[ "$n" == "0" ]] || fail "approved 행 $n 건 잔존 — alembic 0025 가 적용되지 않았습니다: docker compose exec api alembic current"
echo "· approved 잔존 0건 (alembic 0025 적용됨)"
