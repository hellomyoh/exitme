#!/usr/bin/env bash
# ExitMe 원격 서버 배포 스크립트 (2026-09-05 지시) — 태그(버전)를 받아 코드 체크아웃 → 이미지 재빌드 → 마이그레이션 → 헬스 검증.
#
#   사용법:  scripts/deploy.sh <태그|브랜치> [--stash] [--no-build] [--prune] [--port 12010]
#   예시:    scripts/deploy.sh v0.1.1            # 태그 v0.1.1 로 패치 배포
#            scripts/deploy.sh v0.1.1 --stash    # 추적 파일 로컬 변경을 stash 로 치우고 진행 (변경은 보존됨)
#            scripts/deploy.sh main              # main 최신으로 (개발·검증용)
#            scripts/deploy.sh v0.1.1 --prune    # 배포 후 안 쓰는 이미지 정리
#   다른 위치의 복사본으로 실행할 때: cd /path/to/exitme && bash /tmp/deploy.sh v0.1.1  (저장소는 현재 디렉터리)
#
#   운영 구성(docker-compose.prod.yml)은 소스를 이미지에 넣어 실행하므로 `restart` 만으로는 반영되지 않는다.
#   이 스크립트는 반드시 `up -d --build` 로 다시 만들고, 새 이미지 안에서 alembic 을 돌린 뒤
#   /api/health 의 version(app/VERSION)·build_time(이미지 빌드 시각)·db_revision 으로 반영 여부를 검증한다.

# `sh deploy.sh` 로 실행돼도 bash 로 넘어간다 — 우분투의 sh(dash)는 `set -o pipefail`·[[ ]] 를 모른다 (2026-09-05 원격 오류)
if [ -z "${BASH_VERSION:-}" ]; then
  command -v bash >/dev/null 2>&1 || { echo "bash 가 필요합니다: apt-get install -y bash" >&2; exit 1; }
  exec bash "$0" "$@"
fi
set -euo pipefail

# 저장소 위치: 스크립트가 저장소 안(scripts/)에 있으면 그 상위, 아니면(복사본 실행) 현재 디렉터리
if [[ -z "${EXITME_DIR:-}" ]]; then
  _here="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || true)"
  if [[ -f "$_here/docker-compose.yml" ]]; then EXITME_DIR="$_here"; else EXITME_DIR="$PWD"; fi
  export EXITME_DIR
fi
# git checkout 이 실행 중인 이 파일을 바꾸면 bash 가 깨질 수 있어 임시 복사본으로 재실행한다
if [[ -z "${DEPLOY_SH_COPY:-}" ]]; then
  _tmp="$(mktemp)"; cp "$0" "$_tmp"
  DEPLOY_SH_COPY=1 exec bash "$_tmp" "$@"
fi

REF="${1:-}"
if [[ -z "$REF" || "$REF" == "-h" || "$REF" == "--help" ]]; then
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi
shift
BUILD=1; PRUNE=0; PORT=12010; STASH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --prune) PRUNE=1 ;;
    --stash) STASH=1 ;;
    --port) PORT="${2:?--port 값 필요}"; shift ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
  shift
done

cd "$EXITME_DIR"
[[ -f docker-compose.yml && -d .git ]] || { echo "저장소 디렉터리가 아닙니다: $EXITME_DIR (cd /path/to/exitme 후 실행)" >&2; exit 1; }
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
log() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
# JSON 한 줄에서 "key":"value" / "key":value 를 뽑는다 (jq 없이)
pick() { sed -n "s/.*\"$1\":\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p"; }
# 세 자리 버전 비교: vercmp a b → -1(a<b) 0(같음) 1(a>b). "v" 접두·"-3-gabc" 꼬리 무시, 숫자 아니면 0 취급
vercmp() {
  local a b i x y
  a="${1#v}"; a="${a%%-*}"; b="${2#v}"; b="${b%%-*}"
  IFS=. read -r -a x <<< "$a"; IFS=. read -r -a y <<< "$b"
  for i in 0 1 2; do
    local p="${x[$i]:-0}" q="${y[$i]:-0}"
    [[ "$p" =~ ^[0-9]+$ ]] || p=0; [[ "$q" =~ ^[0-9]+$ ]] || q=0
    (( p > q )) && { echo 1; return; }
    (( p < q )) && { echo -1; return; }
  done
  echo 0
}

# 배포 전 현재 버전 기록 — 완료 후 상위 버전으로 올라갔는지 비교해 출력한다 (2026-09-05 지시)
PREV_OUT="$(curl -fsS "http://localhost:$PORT/api/health" 2>/dev/null || true)"
PREV_VER="$(echo "$PREV_OUT" | pick version)"; PREV_DB="$(echo "$PREV_OUT" | pick db_revision)"
if [[ -n "$PREV_VER" ]]; then
  log "배포 전: version=$PREV_VER db=$PREV_DB"
else
  log "배포 전: 실행 중인 서비스 없음(또는 헬스 응답 없음) — 첫 배포로 간주"
fi

# 0) 작업 트리에 추적 파일 변경이 있으면 덮어쓰지 않는다 (.env 등 미추적 파일은 무관).
#    파일 모드(755/644)만 다른 것은 무시. 컨테이너가 호스트 파일을 다시 쓴 경우(개발 구성 bind mount 로 띄웠을 때
#    next-env.d.ts·package-lock.json)가 가장 흔하다 — 어떤 파일인지와 처리법을 함께 보여준다.
DIRTY="$(git -c core.fileMode=false status --porcelain --untracked-files=no)"
if [[ -n "$DIRTY" ]]; then
  log "추적 파일 로컬 변경 감지"
  echo "$DIRTY"
  git -c core.fileMode=false diff --stat | tail -n 5
  GEN=0; MANUAL=0
  while read -r _st f; do
    case "$f" in
      apps/web/next-env.d.ts|apps/web/package-lock.json|apps/api/alembic/versions/*) GEN=1 ;;
      *) MANUAL=1 ;;
    esac
  done <<< "$DIRTY"
  [[ $GEN -eq 1 ]] && echo "· next-env.d.ts / package-lock.json 은 개발 구성(bind mount)으로 띄웠을 때 컨테이너가 다시 쓴 파일입니다 — 버려도 됩니다: git checkout -- <파일>"
  [[ $MANUAL -eq 1 ]] && echo "· 서버에서 직접 고친 파일(nginx 설정·compose 등)이면 보존이 필요합니다 — 커밋해서 올리거나 --stash 로 치우고 배포 후 git stash pop"
  if [[ $STASH -eq 1 ]]; then
    git stash push -m "deploy.sh auto-stash $(date -u +%FT%TZ) before $REF" >/dev/null
    echo "→ --stash: 변경을 stash 에 보관하고 진행합니다 (복구: git stash pop)"
  else
    fail "추적 파일에 로컬 변경이 있습니다 — 위 안내대로 정리하거나 --stash 옵션으로 다시 실행하세요"
  fi
fi

# 1) 코드 체크아웃 — 태그면 detached, 브랜치면 fast-forward
log "git fetch --tags origin"
git fetch --tags --prune origin
if git rev-parse -q --verify "refs/tags/$REF" >/dev/null; then
  git checkout -q --detach "tags/$REF"
elif git rev-parse -q --verify "refs/remotes/origin/$REF" >/dev/null; then
  git checkout -q "$REF"
  git pull -q --ff-only origin "$REF"
else
  fail "태그/브랜치 '$REF' 를 origin 에서 찾을 수 없습니다 (git tag -l 로 확인)"
fi
HEAD_DESC="$(git describe --tags --always)"
FILE_VER="v$(tr -d '[:space:]' < apps/api/app/VERSION)"
log "체크아웃: $HEAD_DESC ($(git rev-parse --short HEAD)) · app/VERSION=$FILE_VER"
if [[ "$REF" == v* && "$FILE_VER" != "$REF" ]]; then
  echo "⚠ app/VERSION($FILE_VER) 이 태그($REF) 와 다릅니다 — 태그 커밋에 VERSION 갱신이 빠졌을 수 있습니다 (AGENTS.md 버저닝 규칙)"
fi

# 2) 이미지 재빌드 + 기동 (변경된 서비스만 재생성)
if [[ $BUILD -eq 1 ]]; then
  log "docker compose up -d --build (api/worker/scheduler/web 재빌드)"
  "${COMPOSE[@]}" up -d --build --remove-orphans
else
  log "docker compose up -d (빌드 생략 — 코드 변경은 반영되지 않습니다)"
  "${COMPOSE[@]}" up -d --remove-orphans
fi

# 3) 마이그레이션 — API 는 기동 시에도 자동 적용하지만 결과를 눈으로 확인하기 위해 한 번 더 (멱등)
log "alembic upgrade head"
for i in 1 2 3 4 5 6; do
  if "${COMPOSE[@]}" exec -T api alembic upgrade head; then break; fi
  [[ $i -eq 6 ]] && fail "마이그레이션 실패 — docker compose logs api 를 확인하세요"
  echo "  api 준비 대기… ($i/6)"; sleep 5
done
DB_REV="$("${COMPOSE[@]}" exec -T api alembic current 2>/dev/null | grep -oE '^[0-9a-f]+' | head -1 || true)"

# 4) 헬스 검증 — version·build_time·db_revision
log "헬스 확인 http://localhost:$PORT/api/health"
OUT=""
for i in $(seq 1 30); do
  OUT="$(curl -fsS "http://localhost:$PORT/api/health" 2>/dev/null || true)"
  [[ -n "$OUT" ]] && break
  sleep 3
done
[[ -n "$OUT" ]] || fail "헬스 응답 없음 — docker compose ps / logs 를 확인하세요"
echo "$OUT"
H_VER="$(echo "$OUT" | pick version)"; H_BUILD="$(echo "$OUT" | pick build_time)"; H_DB="$(echo "$OUT" | pick db_revision)"

STATUS=0
if [[ "$H_VER" == "$FILE_VER" ]]; then echo "✓ version   $H_VER"; else echo "✗ version   응답=$H_VER 기대=$FILE_VER (옛 이미지가 돌고 있습니다)"; STATUS=1; fi
# 이전 버전 대비 — 상위 버전으로 올라갔는지 (2026-09-05 지시)
if [[ -z "$PREV_VER" ]]; then
  echo "· 업데이트  (이전 버전 없음) → $H_VER"
else
  case "$(vercmp "$H_VER" "$PREV_VER")" in
    1)  echo "✓ 업데이트  $PREV_VER → $H_VER (상위 버전으로 정상 갱신)" ;;
    0)  echo "· 업데이트  $PREV_VER → $H_VER (같은 버전 재배포 — 코드 변경이 있었다면 VERSION 갱신이 빠진 것)" ;;
    *)  echo "✗ 업데이트  $PREV_VER → $H_VER (이전보다 낮은 버전 — 의도한 롤백이 아니면 확인하세요)"; STATUS=1 ;;
  esac
  [[ -n "$PREV_DB" && -n "$H_DB" && "$H_DB" != "$PREV_DB" ]] && echo "· DB 리비전  $PREV_DB → $H_DB"
fi
if [[ -n "$H_BUILD" && "$H_BUILD" != "null" ]]; then
  AGE=$(( $(date -u +%s) - $(date -u -d "$H_BUILD" +%s 2>/dev/null || echo 0) ))
  if [[ $BUILD -eq 1 && $AGE -gt 3600 ]]; then echo "✗ build_time $H_BUILD (${AGE}s 전 — 방금 빌드된 이미지가 아닙니다)"; STATUS=1; else echo "✓ build_time $H_BUILD (${AGE}s 전)"; fi
else
  echo "⚠ build_time 없음 — 개발 구성(bind mount)으로 떠 있는 것 같습니다"
fi
if [[ -n "$DB_REV" && "$H_DB" == "$DB_REV" ]]; then echo "✓ db        $H_DB (alembic head 일치)"; else echo "⚠ db        응답=$H_DB alembic=$DB_REV"; fi

if [[ $PRUNE -eq 1 ]]; then
  log "docker image prune -f (안 쓰는 이미지 정리)"
  docker image prune -f >/dev/null
fi

log "서비스 상태"
"${COMPOSE[@]}" ps --format 'table {{.Service}}\t{{.Status}}' 2>/dev/null || "${COMPOSE[@]}" ps
[[ $STATUS -eq 0 ]] && printf '\n\033[32m✓ 배포 완료: %s\033[0m\n' "$HEAD_DESC" || fail "배포 검증 실패 — 위 ✗ 항목을 확인하세요"
