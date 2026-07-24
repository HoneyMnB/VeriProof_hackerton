# VeriProof AI — Cloud Run container image.
# Slim Python 3.13 + gunicorn, synchronous Django workers.
FROM python:3.13-slim AS base

# Prevent Python from writing .pyc files and buffering stdout/stderr.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps minimal (Pillow ships manylinux wheels; no extra libs needed).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (better layer caching).
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy the application package (config, apps, services, manage.py, etc.).
COPY . .

# Materialise hashed and compressed static assets for WhiteNoise/Gunicorn.
RUN python manage.py collectstatic --noinput

# Cloud Run injects PORT=8080; gunicorn listens on $PORT.
ENV PORT=8080
EXPOSE 8080

# config.wsgi:application is the WSGI callable (config/wsgi.py).
CMD exec gunicorn --bind :${PORT} --workers 2 --threads 4 --timeout 120 config.wsgi:application
