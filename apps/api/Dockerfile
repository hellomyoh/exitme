FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# 이미지 빌드 시각(UTC) — /health 와 화면에 표시해 "새로 빌드됐는지"를 운영자가 바로 확인 (2026-09-05)
RUN date -u +%Y-%m-%dT%H:%M:%SZ > /srv/app/BUILD_TIME

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
