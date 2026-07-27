# VeriProof AI — local development

VeriProof is a creator-rights assistant and agent-accessible licensing service.
The local app runs without Docker at `http://127.0.0.1:55000`.

## 개발자 인수인계 문서와 현재 DB 덤프

코드 기준 인수인계 문서는 [docs/docs_develop_v01.md](../docs/docs_develop_v01.md)입니다. 현재 아키텍처, 요청·데이터 흐름, 서비스, API, DB 스키마, migration, 외부 의존성, 검증 결과와 운영 경계를 정리했습니다. Solana 연결, a2a/x402/AP2 계약, 목업의 실제 동작과 운영 전환 시 제한도 별도 장에서 확인할 수 있습니다.

저장소 루트에는 현재 로컬 SQLite DB의 SQL 덤프와 Django fixture가 함께 있습니다. `veriproof_current_db_sqlite_2026-07-25_post_runtime_e2e.sql`은 SQLite schema·data·index를 포함한 native SQL dump로서 **SQLite에만** 복원합니다. `veriproof_current_db_django_fixture_2026-07-25_post_runtime_e2e.json`은 외부 에이전트 구매와 Gemini 작품 등록 런타임 검증 데이터를 포함한 308건의 portable Django fixture로서 **PostgreSQL에서 migration으로 스키마를 만든 후** 복원합니다. 인증·세션·애플리케이션 데이터가 포함되므로 민감 정보로 취급하십시오.

```bash
# SQLite native SQL dump 복원
sqlite3 restored-veriproof.sqlite < ../veriproof_current_db_sqlite_2026-07-25_post_runtime_e2e.sql
```

```bash
# 대상 PostgreSQL 연결을 설정하고 스키마 생성 후 fixture를 적재한다.
export DATABASE_URL='postgres://USER:PASSWORD@HOST:5432/veriproof'
cd veriproof
python manage.py migrate --noinput
python manage.py loaddata ../veriproof_current_db_django_fixture_2026-07-25_post_runtime_e2e.json
python manage.py check
```

현재 SQL 파일은 SQLite native dump이며 native `pg_dump` archive는 아닙니다. 점검한 실행 환경은 SQLite를 사용했고 PostgreSQL client binary/접속 설정이 없었습니다. 따라서 PostgreSQL은 Django fixture 방식으로 복원해야 합니다. 레코드 구성, 검증 결과, 안전한 취급 방법은 인수인계 문서를 확인하십시오.

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

등록 앵커와 등록/라이선스 인증서는 실제 Solana Memo 트랜잭션으로 제출됩니다.
원본 URL이나 원본 바이트는 온체인 Memo와 인증서 응답에 포함하지 않고, 작품의
검증 가능한 SHA-256 기반 증명값만 기록합니다. `PLATFORM_ESCROW_SECRET_KEY`가
비어 있으면 등록/인증서 발급은 mock으로 대체되지 않고 실패합니다.

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

## Solana payment verification

Payment verification is isolated behind `services.payment_verifier.PaymentVerifier`.
Runtime code always uses the real Solana verifier and never accepts fabricated
`mock:` transaction identifiers. Offline tests inject `tests.fakes.FakeSolanaService`
at the service boundary. Configure `SOLANA_RPC_URL`, `USDC_MINT_ADDRESS`, and
`PLATFORM_ESCROW_SECRET_KEY` for Devnet registration and certificate Memo tests.

## A2A local check

Agent A is an ADK/Gemini seller agent embedded in the Django ASGI process.
Agent B is a separately deployable ADK/Gemini buyer coordinator under
`agents/buyer_agent`; it discovers Agent A through the official A2A Agent Card
and delegates marketplace requests through `RemoteA2aAgent`.

```text
repository/
├── veriproof/                  # Django + Agent A A2A endpoint
├── agents/buyer_agent/         # separately deployable Agent B
├── Dockerfile.web
└── Dockerfile.buyer-agent
```

Configure Vertex AI with Application Default Credentials. Do not place a
service-account JSON key in the repository.

```bash
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT='your-project'
export GOOGLE_CLOUD_LOCATION='asia-northeast3'
export ADK_MODEL='gemini-2.5-flash'
export A2A_PUBLIC_BASE_URL='https://your-web-service.run.app'
export SELLER_AGENT_CARD_URL='https://your-web-service.run.app/.well-known/agent-card.json'
export BUYER_AGENT_PUBLIC_BASE_URL='https://your-buyer-agent.run.app'
```

Build and run the two Cloud Run deployment units locally:

```bash
./ctl.sh build api
./ctl.sh build buyer-agent
./ctl.sh api run
./ctl.sh buyer-agent run
```

An external agent can discover and exercise the public contract in this order:

1. `GET /.well-known/agent-card.json` for the official A2A 1.0 card.
2. `POST /a2a/` using the JSON-RPC binding advertised by that card.
3. Agent A calls the read-only public catalog tools.
4. Existing payment and settlement APIs handle the chosen asset separately.

The legacy `/.well-known/ai-plugin.json` and REST/x402 APIs remain available
for compatibility. The A2A layer does not itself assert that payment or
original-file delivery completed.

The initial hackathon runtime uses ADK's in-memory session services and the
A2A in-memory task store. Keep each demo service at one Cloud Run instance.
Before enabling horizontal scale-out or resumable/long-running A2A tasks,
provide `to_a2a()` with a PostgreSQL-backed `DatabaseTaskStore` and a persistent
ADK session service.

The local delegated-wallet x402 purchase flow is documented in
[`docs/autonomous-x402-payment.md`](../docs/autonomous-x402-payment.md).
