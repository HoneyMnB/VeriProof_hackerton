# VeriProof AI Cloud Run 컨테이너 이미지.
# Python 3.13 slim과 Uvicorn으로 Django 및 A2A ASGI 애플리케이션을 구동한다.
FROM python:3.13-slim AS base

# Python의 .pyc 파일 생성과 표준 출력·오류 버퍼링을 비활성화한다.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Pillow가 manylinux wheel을 제공하므로 시스템 의존성은 최소화한다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Docker 계층 캐시를 활용할 수 있도록 Python 의존성을 먼저 설치한다.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# config, apps, services, manage.py 등의 애플리케이션 파일을 복사한다.
COPY . .

# WhiteNoise가 사용할 해시·압축 정적 파일을 생성한다.
RUN python manage.py collectstatic --noinput

# Cloud Run이 주입하는 PORT 환경변수로 Uvicorn을 실행한다.
ENV PORT=8080
EXPOSE 8080

# config.wsgi:application is the WSGI callable (config/wsgi.py).
CMD ["sh", "-c", "exec gunicorn --bind :${PORT:-8080} --workers 2 --threads 4 --timeout 120 config.wsgi:application"]
# A2A는 ASGI 애플리케이션에만 마운트되므로 여기서는 WSGI를 사용하지 않는다.
# CMD exec uvicorn config.asgi:application --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
