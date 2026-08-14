#!/usr/bin/env bash

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/.env"
IMAGE_NAME="api"
DOCKER_NETWORK="${DOCKER_NETWORK:-google-solana}"

set -a
# Windows 편집기가 저장한 CRLF도 환경 변수 값에 ``\r`` 없이 읽는다.
. <(sed 's/\r$//' "$ENV_FILE")
set +a

docker_host_path()
{
    local path="$1"

    if command -v cygpath >/dev/null 2>&1; then
        cygpath -m "$path"
        return
    fi

    printf '%s\n' "$path"
}

configure_source_mount()
{
    local source_path
    local source_name
    local source_docker_path

    SOURCE_RUN_ARGS=()
    SOURCE_HOST_DIR="$ROOT_DIR/veriproof"

    for source_path in "$SOURCE_HOST_DIR"/*; do
        [[ -e $source_path ]] || continue
        source_name="${source_path##*/}"

        case "$source_name" in
            db.sqlite3|media|staticfiles)
                continue
                ;;
        esac

        source_docker_path="$(docker_host_path "$source_path")"
        SOURCE_RUN_ARGS+=(
            --mount "type=bind,source=$source_docker_path,target=/app/$source_name"
        )
    done
}

db_migrate()
{
    configure_source_mount
    DOCKER_ENV_FILE="$(docker_host_path "$ENV_FILE")"

    MSYS_NO_PATHCONV=1 docker run --rm \
        --env-file "$DOCKER_ENV_FILE" \
        --network "$DOCKER_NETWORK" \
        "${SOURCE_RUN_ARGS[@]}" \
        "$IMAGE_NAME" \
        python manage.py migrate "$@"
}

case $1 in
    migrate)
        shift
        db_migrate "$@"
        ;;
    *)
        echo "Invalid command"
        exit 1
        ;;
esac
