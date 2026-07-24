#!/usr/bin/env bash

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/veriproof"
ENV_FILE="$ROOT_DIR/.env"

set -a
. "$ENV_FILE"
set +a

DOCKER_NETWORK="${DOCKER_NETWORK:-google-solana}"

build()
{
    if [[ $1 == "api" ]]; then
        IMAGE_NAME="api"
    else
        echo "Invalid image name"
        exit 1
    fi

    echo "Building the Docker image..."

    BUILD_ENV="${2:-development}"
    echo "BUILD_ENV: $BUILD_ENV"

    docker build -t "$IMAGE_NAME" --build-arg "BUILD_ENV=$BUILD_ENV" "$APP_DIR"
}

run()
{
    PORT_ARG=
    if [[ -n $PORT ]]; then
        PORT_ARG="-p $PORT"
    fi

    docker run -dt \
    --name "$SERVICE_NAME" \
    --env-file "$ENV_FILE" \
    --network "$DOCKER_NETWORK" \
    $PORT_ARG \
    "$IMAGE_NAME" $IMAGE_COMMAND

    log
}

stop()
{
    docker stop "$SERVICE_NAME"
    docker rm "$SERVICE_NAME"
}

reload()
{
    stop
    run
}

log()
{
    docker logs -f "$SERVICE_NAME"
}

run_command()
{
    case $1 in
        run)
        shift
        run
        ;;

        reload)
        shift
        reload
        ;;

        stop)
        shift
        stop
        ;;

        *)
        echo "Invalid run command"
        exit 1
        ;;
    esac
}

case $1 in
    build)
        shift
        build "$@"
        ;;

    api)
        shift
        PORT=8000:8080
        IMAGE_NAME="api"
        SERVICE_NAME="cs-api"
        IMAGE_COMMAND=
        run_command "$@"
        ;;

    *)
        echo "Invalid command"
        exit 1
        ;;
esac
