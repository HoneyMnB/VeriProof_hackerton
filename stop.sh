#!/usr/bin/env bash
# Stop only VeriProof processes started through start.sh.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/veriproof"
RUNTIME_DIR="$APP_DIR/.runtime"
PORT="${VERIPROOF_PORT:-55000}"

stop_pid_file() {
    local pid_file="$1"
    local label="$2"
    [[ -f "$pid_file" ]] || return 0

    local pid
    pid="$(cat "$pid_file")"
    if [[ ! "$pid" =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pid_file"
        return 0
    fi

    # PID reuse must never stop an unrelated process. Require its cwd to match
    # this app directory before sending a signal.
    local cwd
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
    if [[ "$cwd" != "$APP_DIR" ]]; then
        echo "Not stopping $label PID $pid: it is no longer a VeriProof process." >&2
        rm -f "$pid_file"
        return 0
    fi

    echo "Stopping $label (PID $pid)..."
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep .25
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "$label did not exit gracefully; sending TERM again." >&2
        kill -TERM "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
}

stop_pid_file "$RUNTIME_DIR/web.pid" "Django server"

# start.sh 이전에 수동으로 띄운 동일 프로젝트의 서버도 포트 재시작 계약에
# 포함한다. cwd와 명령행을 동시에 확인하므로 다른 프로젝트 서버는 종료하지 않는다.
stop_project_listener() {
    local pid cwd command
    while IFS= read -r pid; do
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
        command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
        if [[ "$cwd" == "$APP_DIR" && "$command" == *"manage.py runserver"* ]]; then
            echo "Stopping VeriProof Django server on port $PORT (PID $pid)..."
            kill "$pid" 2>/dev/null || true
            for _ in {1..20}; do
                kill -0 "$pid" 2>/dev/null || break
                sleep .25
            done
        fi
    done < <(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
}

stop_project_listener
echo "VeriProof stopped. Unrelated servers were not touched."
