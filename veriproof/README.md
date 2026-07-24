# VeriProof AI — local development

VeriProof is a creator-rights assistant and agent-accessible licensing service.
The local app runs without Docker at `http://127.0.0.1:55000`.

## Start and stop

```bash
./start.sh
./stop.sh
```

`start.sh` uses Conda `agent01`, stops only the PID files owned by this project,
runs the `requirements.txt` installation/version check, then runs Django checks
and migrations before starting the server on port 55000.

Install or repair the declared Python dependencies with the same interpreter
that `start.sh` uses:

```bash
/opt/anaconda3/envs/agent01/bin/python -m pip install -r veriproof/requirements.txt
```

GCP 파이프라인을 활성화하는 배포 환경은 별도 호환 범위가 기록된
`requirements-gcp.txt`를 사용합니다. 로컬 서버 시작은 GCP SDK가 비활성화된
상태도 지원하므로 이 선택 의존성 설치를 요구하지 않습니다.

## Solana 공개키 검증과 로컬 목업

지갑 주소 검증에는 `solders`가 필요하지만 RPC URL, API 키, 지갑 비밀키는
필요하지 않습니다. 공개키 문자열이 Solana의 32바이트 base58 형식인지 확인하는
용도이므로, 개발 환경은 다음으로 의존성을 설치합니다.

```bash
python -m pip install 'solders>=0.21'
```

`requirements.txt`에는 전체 Solana 도구 체인이, `pyproject.toml`에는
`.[solana]` 선택 의존성이 선언되어 있습니다. SPL 모듈(`spl.*`)은 별도
`spl-token` PyPI 패키지가 아니라 `solana` 패키지에 포함되므로 별도 설치하지
않습니다. 공개키 검증기가 없으면 서비스는 느슨한 정규식으로 대체하지 않고 등록
요청을 거부합니다.

키가 없는 로컬 환경에서는 기본값 `SOLANA_ADAPTER=mock`이 등록 앵커와 인증서를
`mock:solana:` 접두사의 명시적 로컬 신호로 처리합니다. 이는 실제 온체인 서명이나
권리 보호를 의미하지 않습니다. 실제 Devnet/운영에서는 `SOLANA_ADAPTER=real`과
RPC·서명자 자격 증명을 제공해야 합니다.

## Gemini: real provider path

The creator assistant uses `gemini-3.1-flash-lite`. No mock LLM is used in the
runtime path. The service uses the official `google-genai` SDK and supports one
of the following external configurations (do not commit credentials):

```bash
# Gemini Developer API
export GEMINI_API_KEYS='your-key'

# or Vertex AI with Application Default Credentials
export VERTEX_ENABLED=true
export VERTEX_PROJECT='your-gcp-project'
export VERTEX_LOCATION='global'
gcloud auth application-default login
```

Check non-secret configuration at `GET /api/v1/assistant/status`. With no API
key or ADC the chat endpoint returns HTTP 503; it never invents an AI answer.

## Local payment mock and production switch

Payment verification is isolated behind `services.payment_verifier.PaymentVerifier`.
Local development defaults to `PAYMENT_VERIFIER=mock`. The mock accepts only a
transaction identifier beginning with `mock:`, for example `mock:demo-001`; it
is explicitly a test payment and does not submit anything to Solana.

For a real integration, set `PAYMENT_VERIFIER=solana`, configure the Solana RPC,
USDC mint and signer credentials, then implement/connect the SPL transfer
adapter. The settlement pipeline itself is unchanged because it depends only on
the verifier contract.

## A2A local check

An external agent can discover and exercise the public contract in this order:

1. `GET /.well-known/ai-plugin.json`
2. `GET /api/v1/openapi.json`
3. `GET /api/v1/catalog`
4. `GET /api/v1/ip/{asset_id}` with an agent `Accept` header

For an unlicensed shared asset step 4 returns the actual HTTP 402 x402 payment
envelope. Submit `mock:<id>` only when running local mock settlement; switch to
the Solana verifier before any real deployment.
