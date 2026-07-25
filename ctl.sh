#!/usr/bin/env bash

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/.env"

set -a
. "$ENV_FILE"
set +a

DOCKER_NETWORK="${DOCKER_NETWORK:-google-solana}"
CONTAINER_ADC_FILE="/var/run/secrets/google/application_default_credentials.json"

docker_host_path()
{
    local path="$1"

    if command -v cygpath >/dev/null 2>&1; then
        cygpath -m "$path"
        return
    fi

    printf '%s\n' "$path"
}

find_adc_file()
{
    local candidate

    if [[ -n ${GOOGLE_APPLICATION_CREDENTIALS:-} ]]; then
        candidate="$GOOGLE_APPLICATION_CREDENTIALS"
        if [[ -f $candidate ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    fi

    if [[ -n ${APPDATA:-} ]]; then
        candidate="$APPDATA/gcloud/application_default_credentials.json"
        if [[ -f $candidate ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    fi

    candidate="${HOME:-}/.config/gcloud/application_default_credentials.json"
    if [[ -f $candidate ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi

    return 1
}

configure_adc_mount()
{
    ADC_RUN_ARGS=()

    if ADC_HOST_FILE="$(find_adc_file)"; then
        # 로컬 ADC를 컨테이너 전용 경로에 읽기 전용으로 전달한다.
        ADC_DOCKER_HOST_FILE="$(docker_host_path "$ADC_HOST_FILE")"
        ADC_RUN_ARGS=(
            --mount "type=bind,source=$ADC_DOCKER_HOST_FILE,target=$CONTAINER_ADC_FILE,readonly"
            --env "GOOGLE_APPLICATION_CREDENTIALS=$CONTAINER_ADC_FILE"
        )
        echo "Using local Application Default Credentials"
    elif [[ ${VERTEX_ENABLED:-false} == "true" || ${GOOGLE_GENAI_USE_VERTEXAI:-false} == "true" ]]; then
        echo "Warning: Vertex AI is enabled, but local ADC was not found." >&2
        echo "Run 'gcloud auth application-default login' before starting the container." >&2
    fi
}

build()
{
    if [[ $1 == "api" ]]; then
        IMAGE_NAME="api"
        DOCKERFILE="$ROOT_DIR/Dockerfile.web"
    elif [[ $1 == "buyer-agent" ]]; then
        IMAGE_NAME="buyer-agent"
        DOCKERFILE="$ROOT_DIR/Dockerfile.buyer-agent"
    else
        echo "Invalid image name"
        exit 1
    fi

    echo "Building the Docker image..."

    BUILD_ENV="${2:-development}"
    echo "BUILD_ENV: $BUILD_ENV"

    docker build -t "$IMAGE_NAME" \
        -f "$DOCKERFILE" \
        --build-arg "BUILD_ENV=$BUILD_ENV" \
        "$ROOT_DIR"
}

run()
{
    PORT_ARG=
    if [[ -n $PORT ]]; then
        PORT_ARG="-p $PORT"
    fi

    configure_adc_mount
    DOCKER_ENV_FILE="$(docker_host_path "$ENV_FILE")"

    MSYS_NO_PATHCONV=1 docker run -dt \
    --name "$SERVICE_NAME" \
    --env-file "$DOCKER_ENV_FILE" \
    --network "$DOCKER_NETWORK" \
    "${ADC_RUN_ARGS[@]}" \
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

    buyer-agent)
        shift
        PORT=8001:8080
        IMAGE_NAME="buyer-agent"
        SERVICE_NAME="buyer-agent"
        IMAGE_COMMAND=
        run_command "$@"
        ;;

    *)
        echo "Invalid command"
        exit 1
        ;;
esac
