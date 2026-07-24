#!/usr/bin/env bash
# Start VeriProof locally on http://127.0.0.1:55000.
# Only processes recorded by this project are stopped; unrelated services are safe.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/veriproof"
REQUIREMENTS_FILE="$APP_DIR/requirements.txt"
PYTHON_BIN="${VERIPROOF_PYTHON:-/opt/anaconda3/envs/agent01/bin/python}"
RUNTIME_DIR="$APP_DIR/.runtime"
WEB_PID_FILE="$RUNTIME_DIR/web.pid"
CELERY_PID_FILE="$RUNTIME_DIR/celery.pid"
WEB_LOG="$RUNTIME_DIR/web.log"
CELERY_LOG="$RUNTIME_DIR/celery.log"
HOST="${VERIPROOF_HOST:-127.0.0.1}"
PORT="${VERIPROOF_PORT:-55000}"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python executable not found: $PYTHON_BIN" >&2
    echo "Set VERIPROOF_PYTHON to a Python with the project dependencies." >&2
    exit 1
fi

if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    echo "Requirements file not found: $REQUIREMENTS_FILE" >&2
    exit 1
fi

"$ROOT_DIR/stop.sh" || true
mkdir -p "$RUNTIME_DIR"
cd "$APP_DIR"

echo "Checking required Python packages..."
"$PYTHON_BIN" scripts/verify_requirements.py

echo "Checking Django configuration and migration state..."
"$PYTHON_BIN" manage.py check
"$PYTHON_BIN" manage.py makemigrations --check --dry-run
"$PYTHON_BIN" manage.py migrate --noinput
"$PYTHON_BIN" manage.py showmigrations --plan

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $PORT is already in use by a process not managed by this project." >&2
    echo "Refusing to terminate an untracked process. Free the port, then retry." >&2
    exit 1
fi

echo "Starting VeriProof at http://$HOST:$PORT ..."
nohup "$PYTHON_BIN" manage.py runserver "$HOST:$PORT" --noreload >"$WEB_LOG" 2>&1 &
echo $! >"$WEB_PID_FILE"

# This repository has no Celery application. Set CELERY_APP (for example,
# config.celery) only when one is introduced; then this runner manages it too.
if [[ -n "${CELERY_APP:-}" ]]; then
    nohup "$PYTHON_BIN" -m celery -A "$CELERY_APP" worker --loglevel="${CELERY_LOGLEVEL:-INFO}" >"$CELERY_LOG" 2>&1 &
    echo $! >"$CELERY_PID_FILE"
    echo "Celery worker started (app: $CELERY_APP)."
else
    echo "Celery worker skipped: this project does not define a Celery app."
fi

sleep 1
if ! kill -0 "$(cat "$WEB_PID_FILE")" 2>/dev/null; then
    echo "Server failed to start. See $WEB_LOG" >&2
    rm -f "$WEB_PID_FILE"
    exit 1
fi
echo "Ready: http://$HOST:$PORT (log: $WEB_LOG)"
