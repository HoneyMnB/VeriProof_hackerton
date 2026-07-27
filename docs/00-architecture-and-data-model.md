# 00. 아키텍처 & 데이터 모델 — VeriProof AI

> 모든 SPEC이 공유하는 **단일 진실 원천(SSOT)**. 기술스택, 시스템 구조, 데이터 모델, API 계약, 결제 프로토콜, GCP 비동기 파이프라인, 외부 서비스 인터페이스를 정의한다.
>
> 본 아키텍처는 **주최측(Google Cloud × Solana) 권장 정석 조합**을 채택한다: Cloud Run + Pub/Sub + Eventarc + Workflows + Firestore + BigQuery + Cloud KMS/Secret Manager + Google Cloud Blockchain RPC + AP2/a2a-x402/pay.sh.

- 관련: [PRD](./PRD.md) · [테스트 계획](./test-plan.md) · [SPEC 목록](./specs/)

---

## 1. 기술 스택 (확정)

### 1.1 애플리케이션 코어
| 계층 | 기술 | 비고 |
|------|------|------|
| Backend | **Django 5.x** (동기 뷰 + 서비스 레이어) | M2M API는 순수 `JsonResponse`로 402 정밀 제어 |
| 시스템 오브 레코드 | **PostgreSQL 16 (Cloud SQL)** | 관계형 트랜잭션 데이터. Django ORM |
| Frontend | **Vanilla HTML + CSS + JS** (Django Templates) | 프레임워크 미사용. `fetch` + Firebase JS SDK(`onSnapshot`) |
| AI | **Google Gemini** (`google-genai` / Vertex AI) | `gemini-3.6-flash`(멀티모달·협상추론), `gemini-3.5-flash-lite`(구조화 JSON·배치) |
| 이미지 처리 | **Pillow** | 썸네일·워터마크 생성, SHA-256 해시 |
| 테스트 | **pytest + pytest-django + pytest-cov** | 외부 I/O는 전부 mock, 커버리지 ≥ 85% |

### 1.2 결제·블록체인
| 영역 | 기술 | 비고 |
|------|------|------|
| 에이전트 결제 프로토콜 | **a2a-x402** (`x402_a2a` Python) | payment-required → payment-submitted → payment-completed 3단계 |
| 상위 커머스 프로토콜 | **AP2 (Agent Payments Protocol)** | Intent/Cart/Payment Mandate = Verifiable Digital Credential(VDC) |
| 결제 게이트/서명 | **pay.sh** | HTTP 402 챌린지 감지 + 로컬 지갑 서명 + webhook |
| 결제 UX(사람용) | **Solana Pay** | Non-agent 클라이언트 대상 고정가 QR |
| 체인 접근 | **Google Cloud Blockchain RPC** (Solana) | public devnet RPC 폴백 (`SOLANA_RPC_URL`) |
| 토큰/정산 | **SPL Token (USDC devnet)** + **Memo Program** | 입금검증·해시앵커링·인증서 Memo |
| Solana SDK | `solana-py` + `solders` + `spl-token` | |
| 키 관리 | **Cloud KMS(EC 서명)** + **Secret Manager** | 에스크로 서명키. 로컬은 env base58 폴백 |

### 1.3 GCP 서버리스 (비동기 파이프라인 + 데이터)
| GCP 제품 | 역할 |
|----------|------|
| **Cloud Run** | Django 백엔드 컨테이너 실행(메인 서버). GKE 미사용 |
| **Pub/Sub** | 결제 이벤트 메시지 큐(유실 방지) |
| **Eventarc** | 이벤트 감지 → Workflows 트리거 |
| **Workflows** | 정산 후속처리 오케스트레이션(순차 실행) |
| **Firestore** | 실시간 상태 현황판(`onSnapshot`) — 결제상태·샌드박스 라이브 피드 |
| **BigQuery** | 거래·이벤트 감사로그 장부(분석) |
| **Cloud KMS / Secret Manager** | 서명키·시크릿 관리 |
| **Cloud Storage(GCS)** | 하이브리드 이미지 저장(썸네일·워터마크·임시원본) |
| **Cloud Scheduler** | 원본 purge 배치 트리거 |

### 1.4 환경변수 (.env / Secret Manager)
```
# --- AI ---
GEMINI_API_KEYS=...                 # Gemini 키 (Vertex Express AQ. 포맷 지원)
GEMINI_VISION_MODEL=gemini-3.6-flash
GEMINI_REASONING_MODEL=gemini-3.6-flash
GEMINI_BATCH_MODEL=gemini-3.5-flash-lite
VERTEX_ENABLED=true|false
VERTEX_PROJECT=...  VERTEX_LOCATION=...
GOOGLE_APPLICATION_CREDENTIALS=...  # Vertex/GCP SA

# --- Solana / 결제 ---
SOLANA_RPC_URL=https://api.devnet.solana.com   # 또는 GCP Blockchain RPC 엔드포인트
USDC_MINT_ADDRESS=4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU  # Devnet USDC
PLATFORM_ESCROW_PUBKEY=...
PLATFORM_ESCROW_SECRET_KEY=...      # 로컬 폴백(Devnet 전용). 클라우드는 KMS_KEY 사용
KMS_KEY_NAME=projects/.../cryptoKeys/...   # Cloud KMS EC 서명키(선택)
PAYSH_WEBHOOK_SECRET=...            # pay.sh webhook 서명 검증
AP2_ENABLED=true|false

# --- GCP 파이프라인/데이터 ---
GCP_PROJECT_ID=...
PUBSUB_PAYMENTS_TOPIC=veriproof-payments
FIRESTORE_ENABLED=true|false        # false면 프론트 폴링 폴백
FIRESTORE_DATABASE=(default)
BIGQUERY_DATASET=veriproof_analytics
STORAGE_BACKEND=gcs|local           # local: dev
GCS_BUCKET=...

# --- App ---
DATABASE_URL=postgres://user:pass@localhost:5432/veriproof
ORIGINAL_RETENTION_DAYS=7
DOWNLOAD_TOKEN_TTL_SECONDS=3600

# --- 튜닝 상수 (settings 기본값, env override 가능) ---
MAX_UPLOAD_BYTES=10485760           # 10MB 업로드 상한 (SPEC-001)
MAX_NEGOTIATION_ROUNDS=5            # COUNTER 최대 라운드 (SPEC-003)
MICRO_FLOOR_USDC=0.05              # 초소액 최저 단가 (SPEC-007)
BATCH_MAX_ITEMS=200                 # 배치 항목 상한 (SPEC-007)
SANDBOX_MODE=live|mock             # 샌드박스 온체인 실연동/모의 (SPEC-006)
```

> **로컬 개발 원칙**: `FIRESTORE_ENABLED=false`, `STORAGE_BACKEND=local`, `AP2_ENABLED=false`, env 서명키만으로 GCP 의존 없이 전체 기능이 동작해야 한다(TDD 가능). 클라우드 배포 시 플래그를 켜서 정석 파이프라인을 활성화한다.

---

## 2. 시스템 아키텍처

```text
[Browser: 창작자/관람자]                       [외부 구매자 AI / a2a-x402 클라이언트 / pay.sh]
   │ Django Templates + Vanilla JS               │ M2M REST (X-Agent-Protocol: x402)
   │ Firebase JS SDK(onSnapshot, 실시간)           │
   ▼                                             ▼
┌──────────────────────────── Cloud Run: Django 5 ─────────────────────────────┐
│ Web Views            M2M API Views              Middleware                     │
│  / /library /sandbox  register/get(402)/         X402InterceptorMiddleware      │
│                       negotiate/settle           (클라이언트 판별)               │
│                       batch/*  paysh/webhook                                    │
│ ───────────────────────── Service Layer (mockable) ──────────────────────────│
│  GeminiService  SolanaService  StorageService   NegotiationEngine              │
│  X402Service(a2a-x402/AP2)  KmsSigner  LicenseService  RoyaltyService          │
│  EventRecorder  FirestoreMirror  BigQuerySink  PubSubPublisher                 │
└───────┬───────────────┬───────────────┬──────────────┬───────────────┬────────┘
        ▼               ▼               ▼              ▼               ▼
 [Gemini/Vertex]  [Blockchain RPC]  [Cloud SQL]   [Firestore]     [Pub/Sub]
  Vision/Reason    USDC/Memo/KMS     PostgreSQL    실시간 상태        결제이벤트
                                     (SoR)                            │
                                                                      ▼
                                                        [Eventarc] → [Workflows]
                                                          정산 후속처리 오케스트레이션
                                                          ├─ PostgreSQL 상태 갱신
                                                          ├─ Firestore 상태 미러(UNPAID→LICENSED)
                                                          ├─ 인증서 Memo 발행 + 다운로드 토큰
                                                          └─ BigQuery 거래로그 적재
```

### 2.1 결제 정산 비동기 파이프라인 (핵심)
```text
[1] 구매자 AI 결제완료 (pay.sh / Solana USDC 온체인)
        │  webhook (서명검증 PAYSH_WEBHOOK_SECRET)
        ▼
[2] Cloud Run: POST /api/v1/paysh/webhook
        │  최소 검증 후 이벤트를 Pub/Sub에 발행 (즉시 200 반환 → 서버 블로킹 없음)
        ▼
[3] Pub/Sub(veriproof-payments)  ──감지──►  Eventarc
        ▼
[4] Cloud Workflows (순차 오케스트레이션)
        ├─ A. SolanaService.verify_usdc_payment (recipient/mint/amount)
        ├─ B. LicenseService.grant → PostgreSQL(License) 기록 (payment_tx_sig unique 멱등)
        ├─ C. SolanaService.issue_certificate (Memo, KMS 서명) → certificate_tx
        ├─ D. FirestoreMirror.set(asset/session: status=LICENSED, download_url)
        └─ E. BigQuerySink.insert(거래로그: amount, ts, buyer_agent_id, tx_sigs)
        ▼
[5] 구매자/판매자 AI: Firestore 실시간 반영 or settle 응답으로 다운로드 URL·인증서 수신
```

> **동기 폴백**: GCP 파이프라인 미가용(로컬/TDD) 시 `POST /settle`가 A~E 단계를 **동기적으로** 직접 수행한다. Workflows는 동일 서비스 메서드를 호출할 뿐이므로 로직 중복이 없다(서비스 레이어 재사용).

---

## 3. 결제 프로토콜 스택 (x402 / a2a-x402 / AP2 / pay.sh)

| 계층 | 표준 | VeriProof 구현 위치 |
|------|------|---------------------|
| 접근 제어 | **HTTP 402 + x402 V2 헤더** | `GET /api/v1/ip/{id}` |
| 결제 메시지 흐름 | **x402 V2** (`PAYMENT-REQUIRED` → `PAYMENT-SIGNATURE` → `PAYMENT-RESPONSE`) | `X402ProtocolService`와 동일 GET 재요청 |
| 결제 의도·권한 | **AP2 Mandate(VDC)** | 협상 결과를 Cart Mandate, 결제조건을 Payment Mandate로 서명 기록(`AP2_ENABLED`) |
| 결제 실행/서명 | **pay.sh** | 구매자측 402 감지·지갑 서명; 서버는 `/paysh/webhook` 수신 |
| 사람용 결제 | **Solana Pay** | Non-agent 폴백 고정가 QR |

### 3.1 x402 402 응답 계약
```json
// 표준 헤더
PAYMENT-REQUIRED: <base64 PaymentRequired>
// VeriProof 확장 헤더
X-402-Negotiation-Endpoint: /api/v1/ip/{asset_id}/negotiate
X-Solana-Pay-Address: <creator_wallet>
X-Payment-Mint: <USDC_MINT_ADDRESS>
// Body(PAYMENT-REQUIRED를 디코딩한 PaymentRequired와 동일)
{
  "x402Version": 2,
  "error": "Payment required",
  "resource": {"url": "https://<host>/api/v1/ip/<uuid>", "mimeType": "application/json"},
  "accepts": [{
    "scheme": "exact",
    "network": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
    "asset": "<USDC_MINT_ADDRESS>",
    "amount": "<USDC 최소 단위 정수 문자열>",
    "payTo": "<creator_wallet>",
    "maxTimeoutSeconds": 300,
    "extra": {"feePayer": "<facilitator_wallet>", "memo": "veriproof:<uuid>"}
  }]
}
```

구매자는 외부 지갑에서 서명한 Base64 `PAYMENT-SIGNATURE`를 동일 GET에 보내며,
성공 응답은 Base64 `PAYMENT-RESPONSE`를 포함한다. 기존 `POST /settle`은 호환
경로로만 유지한다.

---

## 4. 서비스 레이어 인터페이스 (계약)
모든 외부 I/O는 아래 서비스로 격리한다. 테스트는 이 인터페이스를 mock한다. Workflows/동기 폴백 모두 동일 메서드를 호출한다.

```
GeminiService
  analyze_asset(file_bytes, mime_type) -> AnalysisResult(tags, category, originality_score, recommended_min_price_usdc)
  negotiate(min_price, target_price, offer_usdc, usage_type, history) -> NegotiationResult(status, price_usdc, reason)
  quote_batch(items, usage_type) -> [BatchQuote(asset_id, unit_price_usdc)]   # 3.5-flash-lite

SolanaService                       # Blockchain RPC 경유, KmsSigner 사용
  anchor_hash(image_sha256, creator_pubkey) -> tx_signature            # Memo Program
  verify_usdc_payment(tx_sig, expected_recipient, expected_amount, mint) -> PaymentVerification(is_valid, amount, sender, slot)
  issue_certificate(asset_id, buyer_pubkey, memo) -> cert_tx_signature # Memo(플랫폼 서명)
  transfer_usdc(to_pubkey, amount) -> tx_signature                     # 에스크로 분배(S3)

KmsSigner
  sign(message_bytes) -> signature      # Cloud KMS EC 서명 / 로컬 env keypair 폴백
  public_key() -> pubkey

StorageService                      # GCS / 로컬
  save_permanent(kind, asset_id, bytes) -> url        # kind ∈ {thumbnail, watermark}
  save_temporary(asset_id, bytes, ttl) -> url         # 원본 임시
  purge_original(asset_id) -> None
  signed_download_url(asset_id, ttl) -> url | None

ImageProcessor
  sha256(image_bytes) -> hex
  make_thumbnail(image_bytes, size) -> bytes
  make_watermark(image_bytes, text) -> bytes

NegotiationEngine                   # GeminiService 실패 시 규칙기반 폴백 포함
  run_round(asset, session, offer_usdc, usage_type) -> NegotiationResult

X402Service                         # a2a-x402 / AP2 매핑
  build_payment_required(asset) -> (headers, body)
  parse_payment_submitted(payload) -> SubmittedPayment
  build_ap2_mandate(session, kind) -> VDC | None      # AP2_ENABLED 시

LicenseService
  grant(asset, buyer_wallet, price, usage_type, payment_tx, session=None) -> License   # payment_tx_sig unique 멱등
  is_licensed(asset, tx_sig) -> bool

RoyaltyService
  distribute(license) -> [RoyaltyDistribution]        # 에스크로 → 분배 송금(transfer_usdc)

EventRecorder
  record(type, payload, asset=None, session=None) -> AgentEvent   # PostgreSQL + Firestore + BigQuery 팬아웃

FirestoreMirror                     # FIRESTORE_ENABLED 시 활성, 아니면 no-op
  set(collection, doc_id, data) -> None

BigQuerySink                        # BIGQUERY_DATASET 설정 시 활성, 아니면 no-op
  insert(table, row) -> None

PubSubPublisher                     # 결제 webhook → 파이프라인 진입
  publish(topic, message) -> message_id
```

---

## 5. 데이터 모델

### 5.1 PostgreSQL (시스템 오브 레코드, Django ORM)

#### ERD
```text
Creator 1──* IpAsset 1──* NegotiationSession
                │  1──* License 1──* RoyaltyDistribution
                │  *──1 IpAsset (parent_asset, 2차창작 self-FK)
                └─ 1──* AgentEvent
BatchOrder 1──* BatchItem *──1 IpAsset
```

#### Creator
| 필드 | 타입 | 제약 | 설명 |
|------|------|------|------|
| id | BigAuto | PK | |
| wallet_address | varchar(44) | unique, indexed | Solana pubkey(자기수탁) |
| display_name | varchar(80) | null | |
| created_at | datetime | auto | |

#### IpAsset
| 필드 | 타입 | 제약 | 설명 |
|------|------|------|------|
| id | UUID | PK | asset_id로 노출 |
| creator | FK(Creator) | PROTECT | |
| title | varchar(120) | null | |
| tags | JSONB | default=[] | Gemini 태그 |
| category | varchar(60) | null | |
| originality_score | int | 0~100 | Gemini 독창성 점수 |
| min_price_usdc | decimal(12,6) | ≥0 | 최소 허용가 |
| target_price_usdc | decimal(12,6) | ≥0 | 목표가 |
| image_sha256 | char(64) | unique, indexed | **원본 해시(영구)** |
| thumbnail_url | varchar | | 영구 |
| watermark_url | varchar | | 영구 프리뷰 |
| original_url | varchar | null | **임시 원본** |
| original_expires_at | datetime | null | purge 예정 시각 |
| original_purged | bool | default=False | |
| anchor_tx_sig | varchar(90) | null, indexed | 온체인 앵커링 tx |
| parent_asset | FK(self) | null | 2차 창작 부모(S3) |
| royalty_share_bps | int | null | 부모 몫 비율(basis points, 3000=30%) |
| status | varchar(20) | enum | draft/anchored/listed/retired |
| created_at | datetime | auto | |

> **제약(S3)**: `parent_asset`가 있으면 `royalty_share_bps`는 1~10000 필수.

#### NegotiationSession
| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| asset | FK(IpAsset) | |
| buyer_agent_id | varchar(80) | |
| usage_type | varchar(30) | commercial/non-commercial/editorial |
| initial_offer_usdc | decimal(12,6) | |
| final_price_usdc | decimal(12,6) null | |
| status | varchar(20) | negotiating/accepted/rejected/expired |
| rounds | JSONB | `[{offer, counter, status, reason, ts}]` |
| pay_address | varchar(44) null | ACCEPT 시 수취(=creator wallet) |
| ap2_cart_mandate | JSONB null | AP2 Cart Mandate(VDC) |
| created_at / updated_at | datetime | |

#### License
| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| asset | FK(IpAsset) | |
| session | FK(NegotiationSession) null | 배치는 null 가능 |
| buyer_wallet | varchar(44) indexed | |
| price_usdc | decimal(12,6) | |
| usage_type | varchar(30) | |
| payment_tx_sig | varchar(90) unique | **검증된 결제 tx(멱등키)** |
| certificate_tx_sig | varchar(90) null | 인증서 Memo tx |
| download_token | varchar(64) null | 만료형 서명 토큰 |
| download_expires_at | datetime null | |
| granted_at | datetime | |

#### RoyaltyDistribution (S3)
| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| license | FK(License) | |
| recipient_wallet | varchar(44) | |
| role | varchar(20) | original / secondary |
| amount_usdc | decimal(12,6) | |
| transfer_tx_sig | varchar(90) null | 분배 송금 tx |
| status | varchar(20) | pending/settled/failed |

#### BatchOrder / BatchItem (S2)
| BatchOrder | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| buyer_agent_id | varchar(80) | |
| total_usdc | decimal(12,6) | |
| status | varchar(20) | quoted/paid/settled/failed |
| payment_tx_sig | varchar(90) null | 일괄 결제 시 |

| BatchItem | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| order | FK(BatchOrder) | |
| asset | FK(IpAsset) | |
| unit_price_usdc | decimal(12,6) | 건당 가격(예: 0.05) |
| license | FK(License) null | 정산 완료 시 연결 |

#### AgentEvent (타임라인·감사 공용)
| 필드 | 타입 | 설명 |
|------|------|------|
| id | BigAuto PK | |
| asset | FK(IpAsset) null | |
| session | FK(NegotiationSession) null | |
| type | varchar(40) | HTTP_402/OFFER/COUNTER/ACCEPT/PAYMENT_VERIFIED/CERT_ISSUED/ANCHORED/ROYALTY_SPLIT |
| payload | JSONB | 표시용 상세 |
| created_at | datetime indexed | |

### 5.2 Firestore (실시간 현황판) — `FIRESTORE_ENABLED=true`
| 컬렉션 | 문서ID | 필드 | 용도 |
|--------|--------|------|------|
| `asset_status` | asset_id | `{status, price_usdc, buyer_agent_id, updated_at}` | 결제상태 실시간(UNPAID→NEGOTIATING→LICENSED) |
| `sessions/{id}/events` | auto | `{type, payload, ts}` | 샌드박스 라이브 협상·네트워크 로그 |
| `sandbox_feed` | auto | `{asset_id, type, message, ts}` | Page 3 실시간 스트림 |

> 프론트는 Firebase JS SDK `onSnapshot`으로 구독. `FIRESTORE_ENABLED=false`면 `/api/v1/events` 폴링 폴백.
> **주의**: `asset_status`(asset_id 키)는 **최신/진행 중 세션**의 표시용 상태 미러다(자산은 다수 구매자를 가질 수 있음). 구매자별 정확한 상태는 `sessions/{id}` 및 PostgreSQL `License`가 SSOT이며, `asset_status`의 status값(UNPAID/NEGOTIATING/LICENSED)은 PostgreSQL의 `IpAsset.status`(draft/anchored/listed/retired, 자산 생애주기)와는 별개 축이다.

### 5.3 BigQuery (감사 장부) — `BIGQUERY_DATASET` 설정 시
| 테이블 | 주요 컬럼 |
|--------|-----------|
| `transactions` | tx_time, asset_id, buyer_agent_id, price_usdc, payment_tx_sig, certificate_tx_sig, usage_type |
| `events` | event_time, asset_id, session_id, type, payload(JSON) |
| `royalties` | tx_time, license_id, recipient_wallet, role, amount_usdc, transfer_tx_sig |

---

## 6. API 계약 (M2M + Web)

### 6.1 M2M REST API
| 메서드 | 경로 | 설명 | 성공 | 실패 |
|--------|------|------|------|------|
| POST | `/api/v1/ip/register` | 이미지 등록(멀티파트) | 201 asset_id, anchor_tx | 400/415 |
| GET | `/api/v1/ip/{asset_id}` | 자산 접근(x402 인터셉터) | 200(라이선스 보유) | **402**(a2a-x402 payment-required) / 404 |
| POST | `/api/v1/ip/{asset_id}/negotiate` | 자율 협상 | 200 NegotiateResponse | 404/422 |
| POST | `/api/v1/ip/{asset_id}/settle` | 결제검증·라이선스 발급(동기 폴백) | 200 (download_token, cert_tx) | 400 |
| POST | `/api/v1/paysh/webhook` | pay.sh 결제완료 webhook → Pub/Sub 발행 | 200 | 401(서명불일치) |
| POST | `/api/v1/ip/batch/negotiate` | 배치 견적/협상(S2) | 200 견적 목록 | 422 |
| POST | `/api/v1/ip/batch/settle` | 배치 정산(S2) | 200 결과 목록 | 400 |
| GET | `/api/v1/ip/{asset_id}/certificate/{cert_id}` | 인증서 조회 | 200 | 404 |
| GET | `/api/v1/ip/{asset_id}/transactions` | 자산 거래내역(License+이벤트, SPEC-005) | 200 | 404 |
| GET | `/api/v1/assets?creator=` | 창작자 자산 목록(SPEC-005) | 200 | |
| POST | `/api/v1/sandbox/run` | 구매자 AI 시뮬레이션 실행(SPEC-006) | 200/202 | 404 |
| GET | `/.well-known/ai-plugin.json` | 에이전트 디스커버리 | 200 | |
| GET | `/api/v1/events?asset_id=&since=` | 이벤트 폴링(Firestore 폴백) | 200 | |

### 6.2 협상 응답 계약
```json
{ "status": "ACCEPT|COUNTER_OFFER|REJECT", "price_usdc": 1.8, "reason": "string",
  "pay_address": "<creator_wallet or null>", "session_id": "<uuid>" }
```

### 6.3 정산 요청/응답 계약
```json
// Request
{ "session_id":"<uuid>", "tx_signature":"<solana_sig>", "buyer_wallet":"<pubkey>" }
// Response 200
{ "status":"SUCCESS", "certificate_tx":"<sig>",
  "download_url":"/files/{token}", "download_expires_at":"ISO8601" }
```

### 6.4 pay.sh webhook 계약
```json
// Headers: X-PaySh-Signature: <hmac>
{ "event":"payment.completed", "tx_signature":"<sig>", "asset_id":"<uuid>",
  "session_id":"<uuid>", "buyer_wallet":"<pubkey>", "amount_usdc": 1.8 }
```

### 6.5 웹 페이지 라우트
| 경로 | 페이지 | SPEC |
|------|--------|------|
| `/` | 창작자 워크스페이스 | SPEC-001 / SPEC-005 |
| `/library` | IP 라이브러리·증명서 | SPEC-005 |
| `/sandbox` | 협상 샌드박스 | SPEC-006 |
| `/files/{token}` | 원본 서명 다운로드 | SPEC-004 |

---

## 7. 프로젝트 구조 (Django)

```text
veriproof/
├── manage.py
├── pyproject.toml / requirements.txt
├── Dockerfile                  # Cloud Run 배포
├── .env.example
├── workflows/                  # GCP Workflows YAML (정산 오케스트레이션)
│   └── settlement.workflow.yaml
├── config/                     # Django settings
│   ├── settings.py  urls.py  wsgi.py
├── apps/
│   ├── ip/                     # IpAsset, 등록, 라이브러리
│   │   ├── models.py  views_web.py  views_api.py  urls.py
│   ├── negotiation/            # NegotiationSession, 협상 API
│   ├── settlement/             # License, RoyaltyDistribution, settle/webhook
│   ├── sandbox/                # Page 3, events
│   └── common/                 # AgentEvent, mixins
├── services/                   # 외부 I/O 격리(테스트 mock 대상)
│   ├── gemini_service.py  solana_service.py  kms_signer.py
│   ├── storage_service.py  image_processor.py  negotiation_engine.py
│   ├── x402_service.py  license_service.py  royalty_service.py
│   ├── event_recorder.py  firestore_mirror.py  bigquery_sink.py  pubsub_publisher.py
├── templates/                  # workspace.html library.html sandbox.html
├── static/                     # css/ js/ (vanilla + firebase onSnapshot)
├── scripts/                    # buyer_agent_sim.py (구매자 AI 시뮬레이터, x402_a2a)
└── tests/
    ├── unit/  integration/  e2e/  conftest.py  factories.py
```

---

## 8. 공통 규칙

- **결제 수취주소 해석 (Payment Recipient Resolution)** — 단일 규칙, 전 SPEC 공통:
  `pay_to = PLATFORM_ESCROW_PUBKEY  (IF asset.parent_asset 존재; 2차 창작물 → 로열티 분배 위해 에스크로 경유)`
  `pay_to = asset.creator.wallet_address  (ELSE; 일반 자산 → 구매자→창작자 P2P 직접)`
  이 규칙은 402 응답(`accepts.pay_to`/`X-Solana-Pay-Address`, SPEC-002), 협상 ACCEPT(`pay_address`, SPEC-003), 정산 검증(`expected_recipient`, SPEC-004/008)에서 **동일하게** 적용된다.
- **금액 단위**: 내부 저장·검증은 `decimal`, USDC 온체인 검증은 최소단위(6 decimals) 정수 변환 후 비교.
- **트랜잭션 커밋먼트**: `confirmed` 이상. 검증 시 recipient·mint·amount(허용오차 0) 모두 일치해야 유효.
- **멱등성**: `payment_tx_sig` unique로 이중 정산 차단. 동일 tx 재제출 시 기존 라이선스 반환. webhook 재전송도 멱등.
- **이벤트 팬아웃**: 상태 전이마다 `EventRecorder.record()` → PostgreSQL(AgentEvent) + Firestore(실시간) + BigQuery(감사) 동시 기록.
- **동기/비동기 이중 경로**: 정산 후속처리는 Workflows(클라우드) 또는 `/settle` 동기(로컬) 어느 쪽이든 **동일 서비스 메서드**를 호출(로직 SSOT).
- **키 보안**: 서명은 `KmsSigner` 경유. 원시 개인키는 로컬 Devnet 전용 폴백에서만 사용.
- **오류 응답 포맷**: `{ "error": "<code>", "detail": "<message>" }`.
- **GCP 미가용 시**: Firestore/BigQuery/PubSub 서비스는 no-op으로 degrade하여 코어 기능(등록·협상·정산) 유지.
