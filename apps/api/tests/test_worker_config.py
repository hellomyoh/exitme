"""스케줄러 안전 설정 (2026-09-09 사고 — 낡은 beat 상태 파일이 이미지에 들어가 배포마다 지난 크론 재실행). 네트워크·DB 없음."""
from __future__ import annotations

import pathlib


def test_beat_catch_up_deadline_and_state_file_isolation():
    from app.worker import celery_app

    assert celery_app.conf.beat_cron_starting_deadline == 3600          # 1시간 넘게 지난 크론은 기동 시 건너뛴다
    assert celery_app.conf.timezone == "Asia/Seoul"
    api_root = pathlib.Path(__file__).resolve().parents[1]
    ignore = (api_root / ".dockerignore").read_text(encoding="utf-8")
    assert "celerybeat-schedule*" in ignore                             # 상태 파일은 이미지에 들어가지 않는다
    assert "/var/lib/celery" in (api_root / "Dockerfile").read_text(encoding="utf-8")   # beat -s 경로가 이미지에 존재
    # 17:10 재시도 beat 항목은 retry=True 로 (변경 없으면 알림 생략)
    assert celery_app.conf.beat_schedule["broker-post-close-sync-retry"]["kwargs"] == {"retry": True}
