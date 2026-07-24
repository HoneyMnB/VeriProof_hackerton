#!/usr/bin/env bash

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/.env"
IMAGE_NAME="api"
DOCKER_NETWORK="${DOCKER_NETWORK:-google-solana}"

set -a
. "$ENV_FILE"
set +a

db_migrate()
{
    docker run --rm \
        --env-file "$ENV_FILE" \
        --network "$DOCKER_NETWORK" \
        "$IMAGE_NAME" \
        python manage.py migrate
}

case $1 in
    migrate)
        shift
        db_migrate
        ;;
    *)
        echo "Invalid command"
        exit 1
        ;;
esac
