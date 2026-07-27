# VeriProof AI 개발자 인수인계 문서 v0.1

> 작성 기준: 2026-07-25. 이 문서는 현재 저장소의 실행 코드, Django 설정, 마이그레이션, 테스트 및 로컬 DB를 직접 점검해 작성했다. 기획 문서가 아니라 현재 구현을 우선하며, 계획과 구현의 차이는 별도로 표시한다.

## 1. 시스템 목적과 범위

VeriProof AI는 창작물이 등록·보호·공개되고, 외부 구매자 에이전트 또는 웹 사용자가 가격 협상과 USDC 결제를 거쳐 라이선스를 받는 Django 서비스다. 창작자용 워크스페이스에는 Gemini 기반 비서, 대화형 등록 초안, 매출/비용 현황이 포함된다.

핵심 원칙은 다음과 같다.

- 관계형 데이터의 기준 원장은 Django DB이며, `AgentEvent`는 Firestore·BigQuery에 보조 복제될 수 있다.
- 등록은 작품 매니페스트 SHA-256 중복 검사, AI 분석, 미리보기/임시 원본 저장, Solana 앵커 및 등록 인증서 발급이 모두 성공할 때만 DB에 확정된다. 이미지 작품은 여러 장을 하나의 작품으로 등록할 수 있다.
- 원본 파일 URL과 바이트는 공개 카탈로그 응답에 노출하지 않는다. 공개 미리보기는 서버의 `/previews/...` 경로로 읽는다.
- 로컬 기본값은 외부 서비스를 흉내 내는 숨은 fallback이 아니라, 명시적인 `mock` Solana/결제 어댑터와 로컬 저장소다. Gemini가 설정되지 않은 경우 AI 답변·협상은 503/실패로 종료한다.

## 2. 저장소와 실행 단위

```text
GoogleSolana/
├── start.sh / stop.sh                 # 로컬 서버 수명주기, 프로젝트 PID만 제어
├── .env                               # 저장소 루트에서 로드, 커밋 금지
├── docs/                              # PRD, SPEC, 본 인수인계 문서
├── veriproof/                         # Django 프로젝트 루트
│   ├── config/                        # settings, URL mount, WSGI/ASGI, agent discovery
│   ├── apps/                          # 도메인별 HTTP 경계와 Django 모델
│   ├── services/                      # 유스케이스와 외부 I/O 어댑터
│   ├── templates/, static/            # Django template + Vanilla CSS/JS
│   ├── tests/                         # unit/integration/smoke pytest
│   ├── demo_assets/                   # DEBUG 전용 데모 등록 소스
│   ├── db.sqlite3                     # 이 점검 시점의 기본 로컬 DB
│   └── Dockerfile                     # Cloud Run용 Gunicorn 이미지
└── veriproof_current_db_django_fixture_2026-07-25_post_runtime_e2e.json
                                      # 현재 DB 데이터 덤프(루트)
```

애플리케이션 패키지는 `apps*`, `services*`, `config*`이며 Python 3.11 이상을 요구한다. 컨테이너는 Python 3.13 slim에서 Gunicorn worker 2개, thread 4개로 `config.wsgi:application`을 실행한다.

## 3. 실행 및 환경 구성

### 3.1 로컬 개발

프로젝트 루트에서 `./start.sh`를 실행한다. 스크립트는 기본적으로 `/opt/anaconda3/envs/agent01/bin/python`을 사용하며, 필요하면 `VERIPROOF_PYTHON`으로 교체한다. 의존성 확인, `manage.py check`, migration drift 확인, `migrate` 후 `127.0.0.1:55000` 서버를 시작한다. 종료는 반드시 `./stop.sh`를 사용한다.

Celery 앱과 워커 실행 경로는 현재 존재하지 않는다. `start.sh`와 `stop.sh`는 Django 서버만 관리한다.

### 3.2 설정의 실제 기본값

`config/settings.py`는 저장소 루트 `.env`를 `override=False`로 로드한다. 환경에 `DATABASE_URL`이 없으면 SQLite `veriproof/db.sqlite3`을 사용한다. 이 점검 시점의 실제 접속 DB도 SQLite다. 운영 PostgreSQL은 `DATABASE_URL=postgres://...`로 명시적으로 전환해야 한다.

| 영역 | 기본값 | 운영 전환 시 필수 검토 |
|---|---|---|
| Django | `DEBUG=true`, localhost 허용 | `DJANGO_DEBUG=false`, `DJANGO_SECRET_KEY`, `VERIPROOF_ALLOWED_HOSTS` |
| DB | SQLite | PostgreSQL URL, migrate, fixture 복원 |
| 저장소 | `STORAGE_BACKEND=local` | GCS bucket/권한 또는 운영 저장소 정책 |
| AI | 키 없음, Gemini 호출 불가 | `GEMINI_API_KEYS` 또는 Vertex ADC/project/location |
| Solana | Devnet RPC 기본 URL, signer 없으면 Memo 제출 fail-closed | RPC, escrow/KMS signer |
| 결제 | 실제 Solana verifier 사용, 테스트는 fake 주입 | mint/수취자/서명 검증 |
| GCP 보조 sink | Firestore/BigQuery/PubSub 비활성 또는 no-op | 각 SDK(`requirements-gcp.txt`), 프로젝트/토픽/데이터셋/권한 |

`requirements.txt`는 Django, Pillow, ReportLab, DB URL 파서, Gemini SDK, Solana/solders와 테스트 도구를 설치한다. `pyproject.toml`의 `ai`, `solana`, `gcp`, `dev` extras는 패키지 배포 관점의 선택 의존성이다. GCP 전용 호환 버전은 `requirements-gcp.txt`를 따른다.

## 4. 요청 흐름과 아키텍처

```text
Browser / external agent
  -> config.urls
  -> apps/* views (입력·인증·HTTP 응답 경계)
  -> services/* (도메인 유스케이스)
  -> Django ORM / local-or-cloud adapters
  -> SQLite 또는 PostgreSQL (기준 데이터)
     + 선택적 Firestore, BigQuery, Pub/Sub, GCS, Gemini, Solana
```

`config.urls`는 `/api/v1/` 아래 IP·협상·정산·sandbox 앱을 mount하고, 웹 페이지와 `/.well-known/ai-plugin.json`을 별도 mount한다. `AgentDiscoveryMiddleware`가 에이전트 발견 헤더/표현을 보조한다. 뷰는 JSON/템플릿 경계에 머물고, 비즈니스 상태 변경은 서비스가 수행한다.

### 4.1 주요 유스케이스

1. **등록** — `RegistrationService.register`가 첫 이미지와 추가 이미지의 순서 있는 SHA-256 목록을 하나의 작품 매니페스트로 해시해 중복을 확인하고, 첫 이미지에 대해 Gemini 분석을 수행한다. 모든 이미지는 워터마크 미리보기와 임시 원본으로 보관되며, 추가 이미지는 `AssetImage`로 부모 `IpAsset`에 연결된다. 앵커와 등록 인증서, 저장소가 성공한 뒤 하나의 DB transaction에서 `Creator`, `IpAsset`, `AssetImage`/보조 파일, 구독 차감, 이벤트를 확정한다. 이미지 세트에는 인증서와 라이선스가 각각 한 건만 발급된다. 부모 작품은 존재해야 하고 royalty share는 1–10,000 bps여야 한다.
2. **발견 및 x402 접근** — `CatalogService`는 공개·앵커·등록 인증서 조건을 만족한 자산만 검색/직렬화한다. 라이선스 없는 원본 접근은 `X402Service`가 x402/HTTP 402 payment-required envelope을 생성한다.
3. **협상** — `NegotiationEngine.run_round`가 Gemini 구조화 협상 결과를 검증하고 최소가·최대 라운드·수취 지갑 규칙을 강제한다. `NegotiationSession.rounds`에 이력을 저장하고 ACCEPT 시 payment destination을 확정한다. Gemini가 없거나 오류이면 가격을 임의 추정하지 않는다.
4. **정산/라이선스** — settlement API가 제출 결제를 해석·검증한 뒤 `LicenseService.grant`로 작품 단위 라이선스를 만든다. `payment_tx_sig` unique가 멱등키다. 인증서와 다운로드 토큰/만료시각을 부여하며, 다중 이미지 작품의 토큰은 모든 구성 이미지를 하나의 ZIP으로 전달한다. 2차 창작물은 `RoyaltyService`가 원작/2차 창작자 분배 leg를 만든다.
5. **창작자 비서** — `CreatorAssistantService`가 Gemini 대화, 대화 이력/지침/첨부를 묶는다. 모델이 제안한 변경은 `CreatorActionService`의 allowlist(`record_expense`, `update_asset_terms`, `prepare_registration`)만 실행하며, DB 재조회로 결과를 검증하고 `AssistantAction` 감사 행을 남긴다.
6. **이벤트 팬아웃** — `EventRecorder.record`는 먼저 `common.AgentEvent`에 저장하고, 활성화된 Firestore와 BigQuery sink로 best-effort 복제한다. 보조 sink 실패는 기준 DB transaction을 취소하지 않는다.

## 5. 앱별 책임과 API

| 앱 | 책임 | 주요 HTTP 경로 |
|---|---|---|
| `accounts` | 로그인/가입, preferences, 공개 지갑 구성 | `/accounts/login/`, `signup/`, `preferences/`, `wallets/`, `password/` |
| `ip` | 자산 등록·검색·인증서·이벤트, 창작자 비서 | `/api/v1/ip/register`, `/api/v1/ip/{id}`, `/api/v1/assets`, `/api/v1/catalog`, `/api/v1/assistant/*` |
| `negotiation` | 자산별 구매자 에이전트 협상 | `/api/v1/ip/{id}/negotiate` |
| `settlement` | 단건/배치 정산, webhook, 다운로드 | `/api/v1/ip/{id}/settle`, `/api/v1/ip/batch/*`, `/api/v1/paysh/webhook`, `/files/{token}` |
| `sandbox` | 샌드박스 실행과 화면 | `/api/v1/sandbox/run`, `/sandbox` |

웹 진입점은 `/` 및 `/workspace`(창작자), `/discover`, `/discover/{asset_id}`, `/library`, `/library/{asset_id}/certificate.pdf`, `/previews/{asset_id}/{variant}`, `/previews/{asset_id}/gallery/{image_id}`이다. 공개 상세는 첫 워터마크 이미지를 메인으로 표시하고, 추가 이미지는 썸네일 선택으로 전환한다. 완전한 machine-readable 계약은 실행 중 `GET /api/v1/openapi.json`, 외부 에이전트 발견 정보는 `GET /.well-known/ai-plugin.json`에서 확인한다.

## 6. 서비스와 외부 어댑터

| 서비스 | 책임 |
|---|---|
| `GeminiService` | 이미지/자산 분석, 협상, batch quote, 창작자 대화 및 액션 계획. 공식 `google-genai`를 lazy import한다. |
| `ImageProcessor` | SHA-256, dHash, thumbnail, watermark. |
| `StorageService` | local `MEDIA_ROOT` 또는 GCS에 영구 preview/supporting 파일과 임시 원본 저장·읽기·삭제. |
| `RegistrationService`, `RegistrationDraftService`, `SubscriptionService` | 작품 매니페스트 앵커링과 다중 이미지 등록, 확인 토큰이 있는 등록 초안, 구독 잔여 등록 횟수의 원자적 차감. `MAX_WORK_IMAGES`(기본 10)가 작품당 이미지 수를 제한한다. |
| `X402Service`, `PaymentVerifier`, `SolanaService`, `KmsSigner` | x402 envelope, 실제 USDC 검증, Memo 앵커·인증서·분배 송금, 키 서명. |
| `LicenseService`, `RoyaltyService`, `Batch*Service` | 라이선스 멱등 발급, 2차 창작 로열티, 일괄 quote/settlement. |
| `CatalogService`, `SalesService`, `CashflowService` | 공개 카탈로그, 검증된 License 기반 판매 집계, 창작자 비용/현금흐름. |
| `CreatorAssistantService`, `CreatorActionService`, `ConversationAttachmentService` | 비서 대화, 제한된 서버 도구 실행 및 감사, 임시 첨부 분석. |
| `EventRecorder`, `FirestoreMirror`, `BigQuerySink`, `PubSubPublisher` | 이벤트 기록과 선택적 cloud mirror/queue. |

모든 `get_*_service()` 팩토리는 import-time I/O를 피하고 현재 Django settings로 실제 또는 비활성 어댑터를 조립한다. 테스트는 생성자 주입 fake/mock을 사용한다.

## 7. Solana 연결 상세

### 7.1 현재 활성 연결 상태

런타임 Solana 경로는 real-only다. 등록 앵커, 등록 인증서, 라이선스 인증서는 `SolanaService.submit_memo()`가 실제 Solana Memo 트랜잭션으로 제출한다. `PLATFORM_ESCROW_SECRET_KEY`가 비어 있거나 RPC/서명이 실패하면 mock 값으로 대체하지 않고 요청을 실패시킨다.

Solana 관련 설정은 `config/settings.py`에 있으며, 기본 RPC URL은 Devnet `https://api.devnet.solana.com`, 기본 mint는 Devnet USDC다.

| 설정 | 역할 | 현재 기본값 | 실체인 전환 시 |
|---|---|---|---|
| `SOLANA_RPC_URL` | Solana JSON-RPC 엔드포인트 | Devnet 공개 RPC | 신뢰 가능한 RPC 또는 GCP Blockchain RPC |
| `USDC_MINT_ADDRESS` | 검증 대상 SPL 토큰 mint | Devnet USDC | 네트워크에 맞는 정확한 USDC mint |
| `PLATFORM_ESCROW_PUBKEY` | 2차 창작물 대금 수취/분배용 공개키 | 빈 값 | 실제 escrow 공개키 |
| `PLATFORM_ESCROW_SECRET_KEY` | Devnet 로컬 서명자 keypair | 빈 값 | Secret Manager 등 비밀 관리 경로 |
| `KMS_KEY_NAME` | Cloud KMS 키 이름 | 빈 값 | KMS 어댑터가 실제 구현된 경우에만 사용 |

`get_solana_service()`와 `get_payment_verifier()`는 mock 설정 분기를 갖지 않는다. 오프라인 테스트는 런타임 설정이 아니라 `tests.fakes.FakeSolanaService`를 생성자/factory 경계에 직접 주입한다.

### 7.2 결제 수취 주소의 단일 규칙

`services/_payment.py`의 `resolve_pay_to()`가 402, 협상 ACCEPT, 정산 검증에서 공통으로 사용된다.

```text
일반 작품:      asset.creator.wallet_address 로 직접 수취
2차 창작물:     PLATFORM_ESCROW_PUBKEY 로 수취 후 로열티 분배
```

이 규칙은 응답의 `accepts.pay_to`, `X-Solana-Pay-Address`, 협상 세션의 `pay_address`, 정산 시 `expected_recipient`, AP2 mandate의 `pay_to`를 일치시킨다. 2차 창작물에 escrow 공개키가 비어 있으면 빈 주소가 생성될 수 있으므로, 운영에서는 이를 필수 설정값으로 강제해야 한다.

### 7.3 등록과 인증서 흐름

`RegistrationService.register()`는 DB 저장 전에 아래 순서로 Solana 어댑터를 호출한다.

1. `anchor_hash(image_sha256, creator_wallet)` — 단일 이미지면 원본 SHA-256, 다중 이미지면 순서 있는 모든 이미지 SHA-256 목록의 매니페스트 SHA-256을 Memo payload `veriproof:anchor:{sha256}:{creator}`로 앵커링한다.
2. `issue_registration_certificate(asset_id, creator_wallet, sha256)` — 등록 완료를 나타내는 별도 Memo 인증서를 생성한다.
3. 두 호출과 미리보기/원본 저장이 성공해야 `IpAsset`을 `anchored` 상태로 저장한다. 실패하면 등록은 503으로 실패하며 성공한 것처럼 자산을 만들지 않는다.

일반 라이선스 정산의 인증서는 `SettlementService.settle_pipeline()`에서 `License` 생성 후 `issue_certificate(asset_id, buyer_wallet, memo)`로 발행한다. 인증서 발행만 실패하면 결제 검증과 라이선스는 보존되고 `certificate_tx_sig`는 `NULL`이다. 이것은 재시도 가능한 후속 오류로 처리하지만, 사용자에게 인증서가 존재한다고 표시해서는 안 된다.

### 7.4 실제 Solana 어댑터의 구현 범위와 한계

`get_solana_service()`는 `SolanaService`를 조립한다. `solders` import와 RPC client 생성은 lazy 방식이며, 호출 시점에만 의존성이 필요하다.

- **결제 검증**: `verify_usdc_payment()`는 RPC 거래에서 recipient, mint, 정확한 USDC 6-decimal 최소 단위, commitment가 `confirmed` 또는 `finalized`인지 확인한다. float 대신 정수 최소 단위를 비교한다.
- **앵커**: `anchor_hash()`는 SHA-256 기반 Memo를 전송한다. RPC 또는 signer가 없으면 즉시 `AnchorFailed`다.
- **인증서**: `issue_registration_certificate()`와 `issue_certificate()`는 원본 URL/바이트/다운로드 토큰 없이 검증용 Memo만 작성한다.
- **로열티 송금**: `transfer_usdc()`는 인터페이스와 test seam은 있으나, 실제 `_send_spl_transfer()`는 의도적으로 `CertificateIssueError`를 발생시킨다. token account 조회와 `transfer_checked` 트랜잭션 생성은 아직 연결되어 있지 않다.
- **KMS**: 현재 Memo 제출 경로는 `PLATFORM_ESCROW_SECRET_KEY`의 Solana CLI 64-byte secret-key array를 사용한다. Cloud KMS 직접 서명은 별도 연결 대상이다.
- **RPC 파싱**: 실제 `_parse_rpc_payment()`는 token balance 증감에서 수취자/mint/금액을 뽑는 최소 구현이며, sender는 채우지 않는다. 다중 token transfer·ATA 생성·복합 거래를 운영에서 안전하게 검증하려면 instruction/계정 소유권 기반 파서를 보강해야 한다.

따라서 현재 `real` 경로는 Memo 앵커/인증서와 단순 검증의 구조는 있으나, 운영용 SPL 송금 및 Cloud KMS 연결까지 완결된 상태가 아니다. 금전이 걸린 운영 전환 전에 실제 Devnet 및 목표 네트워크에서 거래 단위 통합 시험을 해야 한다.

## 8. a2a·x402·AP2 연결 상세

### 8.1 현재 구현의 정확한 범위

현재 앱은 별도 `x402_a2a` 런타임 라이브러리나 a2a agent runtime을 호출하지 않는다. `services/x402_service.py`가 a2a-x402와 유사한 HTTP/JSON wire envelope을 직접 생성·파싱하고, Django view가 다음 네 개의 공개 경로를 제공한다.

```text
GET  /.well-known/ai-plugin.json              에이전트 발견 manifest
GET  /api/v1/openapi.json                     최소 OpenAPI 3.0.3 표면
GET  /api/v1/catalog                          공개·라이선스 가능한 자산 발견
GET  /api/v1/ip/{asset_id}                    라이선스 보유 또는 x402 조건
POST /api/v1/ip/{asset_id}/negotiate          가격/사용 범위 협상
POST /api/v1/ip/{asset_id}/settle             결제 증명 검증과 라이선스 발급
```

`AgentDiscoveryMiddleware`는 manifest 이외의 모든 응답에 `Link: </.well-known/ai-plugin.json>; rel="service-desc"; type="application/json"` 헤더를 붙인다. manifest의 `auth.type`은 `none`이다. 즉 공개 catalog/협상 경로는 별도 OAuth·agent identity 검증 없이 요청 payload의 `buyer_agent_id`를 받는다.

### 8.2 에이전트 접근 시퀀스

```text
1. agent: manifest/OpenAPI 확인 -> catalog에서 공개 자산 선택
2. agent: GET asset + X-Agent-Protocol: x402
3. server: 유효 License가 없으면 HTTP 402 + payment-required envelope
4. agent: POST negotiate(buyer_agent_id, offer_usdc, usage_type)
5. server: Gemini 협상 결과와 가격/라운드 불변식을 적용해 ACCEPT/COUNTER/REJECT
6. agent: 수취자에게 USDC를 결제한 뒤 POST settle(tx_signature, buyer_wallet, session_id?)
7. server: verifier 성공 시 License 멱등 생성, 인증서 발행 시도, 만료형 다운로드 URL 반환
```

`GET /api/v1/ip/{asset_id}`에서 `X-Solana-Tx-Sig`가 해당 asset의 저장된 `License.payment_tx_sig`와 일치하면 200 `LICENSED`와 남은 기간의 download URL을 반환한다. 그렇지 않은 공개 자산은 요청 분류에 따라 agent에는 402, browser에는 200 Solana Pay URL을 반환한다. 비공개·미앵커·등록 인증서 없는 자산은 404다.

### 8.3 HTTP 402 계약과 클라이언트 분류

`X402Service.classify_client()`는 `X-Agent-Protocol: x402` 또는 `Accept: application/json`이면 agent, `Accept: text/html`이면 browser로 판단한다. `Accept`가 없거나 `*/*`처럼 애매하면 보수적으로 agent로 본다.

agent 402 응답에는 `X-402-Payment-Required: true`, `X-Agent-Protocol: x402`, 협상 endpoint, 수취 주소, USDC mint 헤더가 있고, 본문에는 `x402_version: "1"`, `scheme: "solana-usdc"`, `network: "devnet"`, `max_amount_required`(target price), `pay_to`, negotiate/settle endpoint가 있다. browser fallback은 동일 수취 규칙으로 `solana-pay:{address}?amount=...&spl-token=...` URI를 제공한다.

정산 요청의 최소 본문은 `tx_signature`, `buyer_wallet`이며 `session_id`는 선택이다. `X402Service.parse_payment_submitted()`는 tx/buyer wallet/선택 amount를 `SubmittedPayment`으로 해석할 수 있지만, 단건 `/settle` view는 현재 이 helper를 직접 호출하지 않고 동등 필드를 자체 파싱한다. 이 중복 경계는 향후 하나의 입력 검증 경로로 합칠 후보이다.

### 8.4 협상과 AP2 mandate

협상은 `buyer_agent_id`별 `NegotiationSession`을 재사용한다. 허용 usage type은 `commercial`, `non-commercial`, `editorial`이며, Gemini가 없거나 오류면 추정 가격 fallback 없이 503이다. ACCEPT 시 final price와 `resolve_pay_to()` 결과를 세션에 보존하고 `AgentEvent`를 기록한다.

`AP2_ENABLED=false`가 기본이다. true일 때 ACCEPT 후 `ap2_cart_mandate` JSON에 Cart Mandate 모양의 데이터를 저장한다. 이 데이터에는 context, type, asset/session/buyer, usage, 금액, mint, network, pay_to가 포함된다.

중요하게도 현재 AP2 결과물은 **서명된 Verifiable Credential이 아니다**. `build_ap2_mandate()`는 unsigned JSON body만 만들고, Payment Mandate는 settlement path에서 생성·저장하지 않는다. AP2 VC 서명/검증, mandate 보관·갱신, 외부 a2a transport 호환성은 운영 전 별도 구현·상호운용 시험이 필요하다.

### 8.5 webhook 및 비동기 경로

`POST /api/v1/paysh/webhook`은 `PAYSH_WEBHOOK_SECRET`으로 raw body HMAC-SHA256을 constant-time 비교한다. secret이 비어 있으면 fail-closed로 401이다. Pub/Sub client와 `GCP_PROJECT_ID`가 준비되면 webhook은 메시지를 발행하고 200 accepted를 반환한다. 그렇지 않으면 같은 `SettlementService.settle_pipeline()`을 동기로 호출한다.

`workflows/settlement.workflow.yaml`은 목표 배포 구조의 artifact이나, 이 저장소만으로 Pub/Sub→Eventarc→Workflows를 실제 배포·구독·실행하는 IaC는 제공하지 않는다. 따라서 로컬에서 확인되는 것은 동기 fallback이며, cloud 비동기 worker의 실제 end-to-end 동작은 별도 배포 검증이 필요하다.

## 9. 테스트 fake와 비활성 경계

### 9.1 Solana 테스트 fake

`tests.fakes.FakeSolanaService`는 네트워크 I/O가 없는 테스트 전용 fake다. 서비스 계약과 실패 경로를 검증하기 위해 테스트에서 생성자 또는 factory 경계에 직접 주입하며, 런타임 factory에서 반환되지 않는다. 실제 DB에 저장되는 등록/인증서 tx signature는 Solana RPC가 반환한 트랜잭션 서명이어야 한다.

### 9.2 결제 검증

`PaymentVerifier`는 실제 `SolanaService.verify_usdc_payment()`를 사용한다. `mock:` 접두사의 거래 식별자는 런타임에서 결제 완료로 인정하지 않는다.

### 9.3 그 밖의 로컬 비활성/목업 경계

- Gemini 키가 없으면 AI는 목업 응답을 만들지 않고 unavailable 오류를 반환한다.
- Firestore/BigQuery는 플래그/설정이 없으면 no-op이며, 기준 DB `AgentEvent`/License 기록은 계속 수행한다.
- Pub/Sub가 비활성이면 webhook은 동기 settlement fallback으로 이동한다. webhook HMAC secret은 여전히 필수다.
- local storage는 `MEDIA_ROOT`에 영구 preview와 임시 원본을 저장한다. 임시 원본 정리 scheduler는 현재 별도 실행 경로가 없다.

## 10. 데이터베이스 스키마

### 10.1 관계와 삭제 정책

```text
auth.User 1--1 accounts.UserPreference; 1--* accounts.WalletConfiguration
auth.User 1--* ip.IpAsset(account_owner, nullable SET_NULL)
ip.Creator 1--* ip.IpAsset / AssistantMessage / Attachment / Directive / Action /
               Subscription / Draft / Expense
ip.IpAsset 1--* NegotiationSession / License / AssetComponent / AgentEvent /
              BatchItem; parent_asset은 self PROTECT
NegotiationSession 1--* License, AgentEvent
License 1--* RoyaltyDistribution / BatchItem
BatchOrder 1--* BatchItem
```

### 10.2 애플리케이션 테이블 상세

| 모델 | PK와 필드 | 제약·인덱스·의미 |
|---|---|---|
| `accounts.UserPreference` | OneToOne `user`; `display_name(80)`, `language(ko/en)`, `recovery_email`, `contact_phone(30)`, `creator_wallet(64)`, `updated_at` | 사용자 표시/복구/기본 창작자 지갑. 비밀키는 저장하지 않는다. |
| `accounts.WalletConfiguration` | BigAuto id; FK user; `label(40)`, `address(64)`, `accepts_deposits`, `receives_payouts`, `is_active`, `created_at` | `(user,address)` unique; active/created 역순. |
| `ip.Creator` | BigAuto id; `wallet_address(44)`, `display_name(80 nullable)`, `created_at` | wallet unique+index. |
| `ip.IpAsset` | UUID id; FK creator, nullable FK account_owner; title/description/AI description, type, visibility, MIME, tags/AI tags JSON, category, originality score, min/target USDC, SHA-256, perceptual hash, preview/original URLs·만료·purged, anchor/certificate tx, parent, royalty bps, status, created_at | SHA unique; type/visibility/status/creator/perceptual/tx indexes; public ID는 id alias. parent가 있으면 bps 1–10,000을 `save`에서도 강제. 상태: draft/anchored/listed/retired. |
| `ip.AssetComponent` | UUID id; FK asset; file name, MIME, SHA-256, storage URL, created_at | 등록 패키지 보조 파일 manifest; 공개 응답에 노출하지 않는다. |
| `ip.AssistantMessage` | BigAuto id; FK creator; conversation UUID/title, role(user/assistant), content, created_at | `(creator,created_at)` index, 시간 오름차순. |
| `ip.ConversationAttachment` | UUID id; FK creator, nullable SET_NULL source message; filename, MIME, SHA-256, perceptual hash, temporary URL, expiry, analysis JSON, created_at | `(creator,created_at)` index; 분석 실패 시 생성하지 않는 경계다. |
| `ip.AgentDirective` | BigAuto id; FK creator; title, instruction(2,000), active, created/updated | `(creator,is_active,updated_at)` index. 대화 원문과 분리. |
| `ip.AssistantAction` | BigAuto id; FK creator, nullable source message; action name, status, request/result JSON, verification flag/time, created_at | `(creator,created_at)` index. 완료/대기/거부/실패 모두 감사. |
| `ip.SubscriptionPlan` | BigAuto id; unique code, name, monthly fee, included registrations, active, created | 플랜 정의. |
| `ip.CreatorSubscription` | BigAuto id; FK creator/PROTECT plan; status, unique payment tx, period start/end, registrations used, created | `(creator,status,period_end)` index; active/expired. |
| `ip.RegistrationCharge` | BigAuto id; PROTECT subscription; OneToOne/PROTECT asset; created | 같은 asset의 이중 크레딧 차감 방지. |
| `ip.RegistrationDraft` | UUID id; FK creator; status, filename/SHA, fields JSON, unique nullable confirmation token, confirmed time, OneToOne executed asset, created/updated | `(creator,status,updated_at)` index; collecting→confirmed→executed. |
| `ip.CreatorExpense` | BigAuto id; FK creator; USDC amount, memo(200), occurred/created time | `(creator,occurred_at)` index. 수입은 별도 행으로 복제하지 않고 License에서 계산. |
| `negotiation.NegotiationSession` | UUID id; FK asset; buyer agent, usage type, initial/final price, status, rounds JSON, pay address, AP2 mandate JSON, created/updated | status/buyer agent index; negotiating/accepted/rejected/expired. |
| `settlement.License` | UUID id; PROTECT asset, nullable SET_NULL session; buyer wallet, price, usage, unique payment tx, certificate tx, download token/expiry, granted | buyer/certificate indexes; payment tx가 멱등키. |
| `settlement.RoyaltyDistribution` | UUID id; FK license; recipient, role, amount, transfer tx, status | original/secondary 및 pending/settled/failed. |
| `settlement.BatchOrder` | UUID id; buyer agent, total, status, payment tx, created | quoted/paid/settled/partial/failed. |
| `settlement.BatchItem` | UUID id; CASCADE order, PROTECT asset, price, nullable SET_NULL license, created | batch 정산 완료 시 License 연결. |
| `common.AgentEvent` | BigAuto id; nullable SET_NULL asset/session; type, payload JSON, created | type 및 `(asset,created_at)` index, 최신순. |

Django 기본 테이블도 사용한다: `auth_*`, `django_content_type`, `django_migrations`, `django_session`, `django_admin_log`. 현 DB에는 30개 테이블이 존재한다. `sandbox`는 영속 모델이 없다.

### 10.3 마이그레이션과 변경 규칙

마이그레이션은 Alembic이 아니라 Django migration이다. 현재 `accounts.0001–0003`, `common.0001`, `ip.0001–0016`, `negotiation.0001`, `settlement.0001` 및 Django 내장 migration이 적용돼 있다. IP 앱의 최신 선형 경로는 `0014_ipasset_ai_description_ipasset_ai_tags` → `0015_ipasset_account_owner` → `0016_asset_image`이며, `showmigrations`, `manage.py check`, `makemigrations --check --dry-run`에서 graph 또는 모델 변경 문제가 없음을 확인했다.

스키마를 바꾸면 반드시 새 Django migration을 만들고 `veriproof/db_reference.md`에 변경 이유, 필수값/영향 범위, 검증 및 Alembic 불필요 사유를 누적한다. 기존 log를 덮어쓰지 않는다.

## 11. 데이터 덤프 및 PostgreSQL 이관

루트에는 동일한 2026-07-25 SQLite DB를 두 형식으로 보관한다. 두 덤프에는 외부 에이전트 구매와 Gemini 등록 런타임 검증에서 생성된 구독·작품·협상·라이선스 데이터가 포함된다.

| 파일 | 생성 방식·내용 | 복원 대상 |
|---|---|---|
| `veriproof_current_db_sqlite_2026-07-25_post_runtime_e2e.sql` | SQLite `iterdump()` 결과. schema, data, index, transaction 경계 포함; 95,664 bytes, 469 lines | SQLite 전용 |
| `veriproof_current_db_django_fixture_2026-07-25_post_runtime_e2e.json` | `manage.py dumpdata --all --natural-foreign --natural-primary --indent 2`; JSON 검증 완료, 308 records | Django가 지원하는 DB, PostgreSQL 이관용 |

SQLite SQL 덤프는 PostgreSQL SQL 문법이 아니므로 PostgreSQL에 직접 실행하지 않는다. SQLite 복원은 다음과 같다.

```bash
sqlite3 restored-veriproof.sqlite < veriproof_current_db_sqlite_2026-07-25_post_runtime_e2e.sql
```

JSON fixture는 **현재 SQLite 데이터의 PostgreSQL 이관용 fixture**이지 `pg_dump`가 만든 PostgreSQL 물리/SQL 덤프가 아니다. 이 환경에는 `pg_dump`/`psql` 바이너리와 PostgreSQL 접속 설정이 없었기 때문에 PostgreSQL native dump는 만들지 않았다. PostgreSQL에는 반드시 아래 순서로 복원한다.

```bash
# 1) 빈 PostgreSQL DB를 준비하고 .env 또는 환경에 DATABASE_URL을 설정한다.
export DATABASE_URL='postgres://USER:PASSWORD@HOST:5432/veriproof'

# 2) Django 스키마를 먼저 생성한다.
cd veriproof
python manage.py migrate --noinput

# 3) 루트 fixture를 적재한다.
python manage.py loaddata ../veriproof_current_db_django_fixture_2026-07-25_post_runtime_e2e.json

# 4) 결과를 검증한다.
python manage.py check
python manage.py showmigrations --plan
```

덤프에는 Django 사용자 password hash, 세션, 거래/대화/자산 메타데이터가 들어 있으므로 비밀 데이터로 취급한다. 공개 저장소·메신저·외부 artifact 저장소에 무단 업로드하지 말고, 권한 있는 백업 위치에서 암호화해 보관한다. production 복원 전에는 대상 DB가 비어 있는지 확인해야 unique key 충돌과 세션 재사용을 피할 수 있다.

## 12. 테스트와 품질 상태

점검에서 다음을 실행했다.

```text
pytest -q                         329 passed
python manage.py check            문제 없음
python manage.py makemigrations --check --dry-run   변경 없음
```

현재 전체 `pytest -q`는 329 passed다. `pytest-asyncio`의 loop scope 미설정과 smoke test의 Pydantic `any` 타입 경고만 남아 있다. 테스트는 unit, integration, smoke로 구성되어 등록·공개 카탈로그·협상·정산·다운로드·webhook·배치·로열티·assistant·계정 설정을 포함한다.

### 12.1 외부 에이전트 구매 및 Gemini 등록 런타임 검증 (2026-07-25)

- 로컬 HTTP 서버에서 DEBUG 개발자 로그인 후 `POST /api/v1/assistant/chat`을 호출했다. 실제 설정된 Gemini가 등록 준비 액션과 한국어 응답을 반환했다.
- 당시 로컬 mock 결제 구독과 mock Solana 앵커로 multipart 등록을 검증했다. 이 방식은 현재 런타임에서 제거되었고, 등록/인증서는 실제 Solana Memo signer가 필요하다.
- 외부 에이전트 역할로 manifest·공개 catalog, HTTP 402, Gemini 협상, settle, 다운로드를 검증했다. 당시 결제 증명은 mock였으므로 실결제·실체인 검증 기록으로 간주하지 않는다.
- 검증 중 multipart `visibility=PUBLIC`이 `private`로 저장되는 문제를 발견했다. 등록 입력을 소문자로 정규화하고 회귀 테스트를 추가했으며, 수정 후 실제 HTTP 등록 결과가 `public`이고 catalog에 노출됨을 확인했다.

### 12.2 다중 이미지 작품 런타임 검증 (2026-07-24)

- AI 생성 PNG 3장(각 1448×1086)을 실제 multipart `POST /api/v1/ip/register`로 업로드했다.
- 등록 응답은 하나의 `asset_id`, 하나의 앵커, 하나의 등록 인증서를 반환했다. 당시 앵커는 mock였으며 현재 런타임은 실제 Solana Memo 서명자를 요구한다.
- 최신 서버의 `/discover/{asset_id}`는 썸네일 컨트롤 3개와 추가 워터마크 미리보기 경로 2개를 렌더링했고, 추가 미리보기 요청은 HTTP 200이었다.
- 한 번의 mock settlement는 라이선스 인증서와 단일 다운로드 토큰을 반환했고, 그 토큰의 ZIP에는 대표 이미지와 추가 이미지 2장이 모두 포함됐다.

## 13. 구현 상태와 남은 작업

### 13.1 현재 구현·검증 완료 범위

| 도메인 | 구현 상태 | 실제 검증 근거 |
|---|---|---|
| 등록·보호 | SHA-256 매니페스트, 다중 이미지, Gemini 분석, preview/임시 원본, 등록 인증서를 하나의 유스케이스로 처리 | Solana Memo 제출 단위 테스트와 multipart 등록 회귀 테스트 |
| 공개·발견 | 공개/앵커/등록 인증서 조건의 catalog, 보호 preview, 원본 비노출 | 공개 catalog와 preview 응답 확인 |
| 외부 에이전트 | manifest/OpenAPI, x402 402, Gemini 협상, settle, 라이선스/다운로드 토큰 | 외부 에이전트 HTTP E2E에서 402→ACCEPT→settle 200→download 200 확인 |
| 창작자 비서 | 실제 Gemini 대화, 등록 준비 액션, 제한 도구 allowlist, DB 사후 검증/감사 | `assistant/chat` 실제 Gemini 한국어 응답 및 `prepare_registration` 액션 확인 |
| 오류 보완 | multipart 가시성 대소문자 정규화 | `visibility=PUBLIC` 실제 등록이 `public` 저장 및 catalog 노출, 회귀 테스트 추가 |
| 품질 | Django migration, check, 테스트 | migration drift 없음, 전체 pytest 329 passed |

### 13.2 운영 전 반드시 구현/검증할 작업

| 우선순위 | 작업과 영향 범위 | 완료 기준 |
|---|---|---|
| P0 | 실 Solana 검증과 SPL 전송: `SolanaService`, `PaymentVerifier`, signer/KMS, recipient/mint/amount/commitment 검사 | Devnet의 실제 USDC 거래로 402→정산→인증서→다운로드 E2E를 반복 가능하게 통과 |
| P0 | 인증/인가: DEBUG 개발자 로그인 제거 또는 운영 차단, 지갑 서명 로그인, creator/agent 권한 검증 | 타 사용자의 자산·지침·등록 조건을 API/UI에서 읽거나 수정할 수 없음을 E2E로 증명 |
| P0 | 운영 설정 검증: secret/RPC/escrow/KMS/webhook/allowed hosts/DB의 누락을 fail-closed | production 설정 검사와 배포 파이프라인이 누락·mock 조합을 거부 |
| P1 | 저장소 수명주기: `ORIGINAL_RETENTION_DAYS` 실제 purge scheduler, 저장 실패 보상/재시도, 다운로드 복구 | 고아 앵커·고아 파일·만료 원본의 재처리 및 감사 절차 검증 |
| P1 | GCP 비동기 경로: Pub/Sub, Eventarc, Workflows, Firestore/BigQuery, DLQ와 재처리 관측성 | Cloud Run 사전 환경에서 webhook→queue→정산/감사 E2E와 장애 재처리 통과 |
| P1 | A2A/AP2: 실제 `x402_a2a` transport, 서명된 AP2 VC/mandate, 외부 agent identity | 호환 에이전트 간 manifest·mandate·settlement 상호운용 시험 통과 |
| P2 | 비서/계정 운영 UX: 지침 수정·활성 전환·삭제, 등록·결제의 사용자 승인과 멱등성 | 권한, 취소, 재시도, 감사 로그를 포함한 UI/API 시나리오 통과 |
| P2 | PostgreSQL/Cloud SQL 운영화: migration runbook, fixture/백업 복구, 성능·동시성 시험 | 빈 PostgreSQL 복원, 업그레이드 migration, rollback/restore 리허설 완료 |

### 13.3 현재 운영 불가 선언

현재 로컬에서 결제·Solana는 `mock`이며, AP2 mandate는 unsigned JSON이고 실제 a2a runtime transport는 없다. 따라서 이 코드베이스는 **실결제·실체인·강한 사용자 인증 운영에 사용할 수 없다.** Gemini가 구성되지 않은 환경에서는 분석/협상/비서도 fail-closed로 실패한다. 목표 Cloud Run/GCP 문서는 배포 설계이며 현재 기본 실행은 SQLite와 local storage다.

## 14. 자주 쓰는 운영 명령

```bash
# 로컬 서버
./start.sh
./stop.sh

# 검증
cd veriproof
python manage.py check
python manage.py showmigrations --plan
python -m pytest -q

# DEBUG 전용 데모 계정/카탈로그
python manage.py seed_developer
python manage.py seed_demo_catalog

# 새 현재 DB fixture 생성(기존 파일을 덮어쓰지 말고 날짜를 바꾼다)
python manage.py dumpdata --all --natural-foreign --natural-primary --indent 2 \\
  --output ../veriproof_current_db_django_fixture_YYYY-MM-DD.json
```
