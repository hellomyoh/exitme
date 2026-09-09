FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# beat 스케줄 상태 파일 자리 — 코드 디렉터리 밖(compose 의 `beat -s`). 저장소에 남은 파일을 읽는 일이 없게 (2026-09-09)
RUN mkdir -p /var/lib/celery
# 이미지 빌드 시각(UTC) — /health 와 화면에 표시해 "새로 빌드됐는지"를 운영자가 바로 확인 (2026-09-05)
RUN date -u +%Y-%m-%dT%H:%M:%SZ > /srv/app/BUILD_TIME

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
