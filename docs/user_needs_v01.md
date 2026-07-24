# 📄 [기획서 개정판 v0.2] VeriProof AI
> **"Agentic IP Protocol & Automated Licensing Marketplace"**
> *Google Cloud Gemini(3.6 Flash) + Solana 온체인 정산 + x402/AP2 에이전트 결제를 결합한 AI 간 자율 저작권 라이선싱 프로토콜*
> Google Cloud × Solana 해커톤 출품작

---

## 📌 개정 요약 (v0.1 → v0.2)

본 문서는 초기 기획(v0.1)을 **논리 검증 + 대회 공식 권장 아키텍처 + 사용자 확정 스택**에 맞춰 개정한 것입니다.
세부 구현 명세는 권위 문서인 **`docs/`(PRD·SPEC·테스트계획)**에 있으며, 본 문서는 비전·문제·UX·시나리오 중심의 상위 기획서입니다.

| 구분 | v0.1 (초기) | v0.2 (확정) | 근거 |
|------|-------------|-------------|------|
| 백엔드 | Python FastAPI | **Django 5 + PostgreSQL** | 사용자 확정 |
| 프론트 | Next.js (React) | **Vanilla HTML/CSS/JS** (Django Templates) | 사용자 확정 |
| AI 모델 | "Gemini 3.5/3.6" (오기) | **`gemini-3.6-flash`**(멀티모달·협상), **`gemini-3.5-flash-lite`**(구조화·배치) | 실존 모델 검증 |
| 컴퓨트 | Cloud Run | **Cloud Run** (GKE 미사용) | 대회 권장 |
| 데이터 | (메모리 DB) | **PostgreSQL(SoR) + Firestore(실시간) + BigQuery(감사)** | 대회 권장 |
| 비동기 | 없음(동기) | **Pub/Sub + Eventarc + Workflows** 정산 파이프라인 | 대회 권장 |
| 결제 | x402(자체) | **x402 + a2a-x402(`x402_a2a`) + AP2 + pay.sh + Solana Pay** | 대회 공식 프로토콜 |
| 체인 접근 | public devnet RPC | **Google Cloud Blockchain RPC** (public devnet 폴백) | 대회 권장 |
| 키 관리 | env 개인키 | **Cloud KMS(EC 서명) + Secret Manager** (로컬 env 폴백) | 대회 권장 |
| 온체인 코드 | 전부 Mock | **Devnet 실연동**(USDC 검증·Memo 앵커링·인증서·로열티 분배) | 심사 신뢰도 |
| 이미지 저장 | (원본 저장 가정) | **하이브리드**: 썸네일·워터마크 영구 + 원본 임시(purge) + 해시 영구 앵커링 | 저장비용·원본전달 양립 |
| 개발 일정 | 14일 로드맵 | (문서에서 제외) | 사용자 지시 |

> 📂 **권위 문서**: [`docs/README.md`](./docs/README.md) · [`docs/PRD.md`](./docs/PRD.md) · [`docs/00-architecture-and-data-model.md`](./docs/00-architecture-and-data-model.md) · [`docs/specs/`](./docs/specs/) · [`docs/test-plan.md`](./docs/test-plan.md)

---

## Ⅰ. 프로젝트 개요 (Executive Summary)

* **프로젝트명**: **VeriProof AI (베리프루프 AI)**
* **한 줄 요약**: 창작자의 IP를 대화형 UI로 자율 등록하고, 외부 AI 에이전트와 HTTP 402(`x402`) 기반의 JSON 협상을 거쳐 솔라나(USDC)로 즉시 결제·정산하는 **에이전트 전용 저작권 프로토콜 및 마켓플레이스**.
* **핵심 기술 스택 (확정)**:
  * **AI/Cloud**: Google Cloud Run, Vertex AI, Gemini `gemini-3.6-flash` / `gemini-3.5-flash-lite` (Vision & Reasoning)
  * **Blockchain/Payment**: Solana Devnet, USDC(SPL), Solana Pay, `pay.sh`, `x402` + `a2a-x402` + AP2, Google Cloud Blockchain RPC
  * **Backend/Frontend**: **Django 5 (M2M REST API)** + **Vanilla HTML/CSS/JS**
  * **Data/Async**: PostgreSQL(Cloud SQL), Firestore, BigQuery, Pub/Sub + Eventarc + Workflows
  * **Security**: Cloud KMS(EC 서명), Secret Manager

---

## Ⅱ. 배경 및 문제 정의 (Problem Statement)

### 1. 현황 및 문제점
1. **AI 시대의 창작권 침해**: 무분별한 웹 크롤링과 캡처로 인해 창작자의 IP가 보호받지 못함.
2. **전통 결제 시스템의 한계**:
   * 신용카드는 회원가입, 본인인증, CVC 입력이 필요하여 **AI 에이전트가 스스로 결제 불가능**.
   * 기존 카드 결제 수수료(건당 ~300원) 구조로 인해 **1원~100원 단위의 초소액 결제(Micropayment) 불가능**.
3. **M2M(Machine-to-Machine) 거래 인프라 부재**: AI가 스스로 유료 라이선스를 구매하고 싶어도 표준화된 협상 및 정산 프로토콜이 없음.

### 2. 솔루션: VeriProof AI
* **x402 기반 접근 제어**: AI 크롤러/에이전트 접근 시 `HTTP 402 Payment Required` 헤더와 **a2a-x402 협상 스펙**을 반환하여 무단 도용 방지.
* **Gemini 자율 협상**: 판매자 AI가 창작자의 조건(최소 가격, 용도)을 반영하여 구매자 AI와 REST API로 즉시 가격 협상.
* **Solana 초소액 정산**: 저수수료·고속 솔라나 결제를 통해 건당 0.05 USDC 수준의 마이크로 라이선싱 실현.

### 3. 4대 핵심 목표
1. **Zero-Friction IP 등록**: 서식 없이 챗봇 대화 한 번으로 이미지 분석·메타데이터 생성·타임스탬프 온체인 등록.
2. **Multi-Agent 자율 협상**: 판매자 AI와 구매자 AI가 창작자 조건에 맞춰 자율 가격 협상.
3. **HTTP 402 Micro-Licensing**: 무단 크롤링 차단 + `x402`로 초소액 라이선스를 솔라나(USDC)로 즉시 결제.
4. **위변조 불가 온체인 인증**: 결제 즉시 솔라나 장부에 라이선스 발급·해시 기록을 영구 보관.

---

## Ⅲ. 페이지별 UX/UI 상세 설계 (UX/UI Specification)

> 프론트엔드는 **Vanilla HTML/CSS/JS** (Django Templates)로 구현. 실시간 상태는 Firestore `onSnapshot`(폴백: `/api/v1/events` 폴링).

### 1. [Page 1] 창작자 메인 대화형 워크스페이스 (Main Chat Workspace)
* **목적**: 창작자가 양식 입력 없이 Gemini와 대화하듯 저작권을 등록하는 화면.
* **주요 구성 요소**:
  * **드래그&드롭 업로드 존**: 이미지를 던져 넣으면 반응하는 대화창.
  * **Gemini 분석 피드**: 업로드 즉시 시각 요소를 분석해 태그·카테고리·독창성 점수를 자동 도출.
  * **라이선스 제약조건 슬라이더**: 최소 허용가(예: 1.5 USDC)·목표가(예: 3.0 USDC) 설정.
  * **등록 완료 카드**: 솔라나 앵커링 트랜잭션(Tx Hash) 및 발급된 `x402` 엔드포인트 표시.

### 2. [Page 2] IP 라이브러리 및 온체인 증명서 대시보드 (Library & Certificate)
* **목적**: 등록 자산 관리 및 솔라나 온체인 소유권 검증.
* **주요 구성 요소**:
  * **보호 자산 그리드**: 썸네일/워터마크 프리뷰 토글(원본 미노출).
  * **Solana Explorer 검증 버튼**: 앵커링 트랜잭션의 devnet Explorer로 이동.
  * **디지털 라이선스 증명서 (QR 모달)**: 온체인 소유권을 증명하는 모바일 겸용 인증서.
  * **에이전트 거래 타임라인**: 어떤 외부 AI가 언제 몇 USDC에 사갔는지 실시간 타임라인.

### 3. [Page 3] 에이전트 협상 샌드박스 (Multi-Agent Simulator — 심사위원/시연용)
* **목적**: 외부 AI가 실제로 대화하고 온체인 결제하는 과정을 시각적으로 증명.
* **주요 구성 요소**:
  * **좌측 창 (판매자 AI - Gemini)**: 창작자 지침에 따라 가격을 방어하는 백엔드 협상 로그.
  * **우측 창 (구매자 AI 터미널)**: `x402_a2a`를 장착한 외부 AI가 제안(Offer)을 날리는 터미널.
  * **하단 네트워크 로그**: `HTTP 402` 수신 ➔ JSON 협상 ➔ Solana USDC 송금 라이브 스트림.

---

## Ⅳ. 유스케이스 시나리오 (Detailed Scenarios)

> 3개 시나리오 전부 MVP 범위. 상세 요구·인수조건은 SPEC-004/007/008 참조.

### 시나리오 1 — [B2C] 라이선스 매매 (SPEC-001~004)
창작자 이미지 등록(최소 1.5 USDC) ➔ 마케팅 AI가 1.0 USDC 제안 ➔ 판매자 AI(Gemini)가 1.8 USDC 역제안 ➔ 마케팅 AI 승인 ➔ **구매자→창작자 직접** 1.8 USDC 송금(Solana) ➔ 서버 tx 검증 ➔ 워터마크 제거 원본(임시 서명URL) + 온체인 인증서 자동 전달.

### 시나리오 2 — [B2B] 언론사 AI 스톡 이미지 대량 구매 (SPEC-007)
뉴스 AI가 배치 견적 요청 ➔ 이미지당 0.05 USDC 산정 ➔ 일괄 결제 ➔ 배치 정산 ➔ 각 건 라이선스·다운로드 토큰 반환. (카드 수수료 0원)

### 시나리오 3 — [Multi-Agent] 2차 창작물 로열티 자동 분배 (SPEC-008)
2차 창작물(부모 링크·분배율 70/30) 10 USDC 판매 발생 ➔ 구매자→플랫폼 에스크로 송금 ➔ 에스크로가 원작자 3 USDC / 2차창작자 7 USDC로 **실제 온체인 분할 송금** ➔ 두 송금 tx 기록. (Rust 스마트컨트랙트 대체: 서버 오케스트레이션)

---

## Ⅴ. 시스템 아키텍처 및 클라이언트 판별 로직

### 1. 시스템 구조도 (확정)

```text
[Browser: 창작자/관람자]                       [외부 구매자 AI / x402_a2a / pay.sh]
   │ Django Templates + Vanilla JS (Firestore onSnapshot)   │ M2M REST (X-Agent-Protocol: x402)
   ▼                                                         ▼
┌──────────────────────── Cloud Run: Django 5 ─────────────────────────┐
│ X402InterceptorMiddleware (클라이언트 판별)                            │
│  ├─ (일반 브라우저/Non-x402) ──► Solana Pay 고정가(Buy-It-Now) 안내      │
│  └─ (x402 지원 AI) ──► Gemini 협상(/negotiate) ──► /settle 또는 pay.sh   │
│ Service Layer: Gemini · Solana(KMS) · Storage · X402/AP2 · License ...  │
└───────┬──────────────┬───────────────┬───────────────┬─────────────────┘
        ▼              ▼               ▼               ▼
 [Gemini/Vertex] [Blockchain RPC]  [Cloud SQL]     [Pub/Sub]
                  USDC/Memo/KMS     PostgreSQL(SoR)    │
                                                       ▼
                                          [Eventarc]→[Workflows] 정산 후속처리
                                          ├ PostgreSQL 갱신  ├ 인증서 Memo 발행
                                          ├ Firestore 미러(UNPAID→LICENSED)
                                          └ BigQuery 거래로그 적재
```

### 2. 클라이언트 판별 및 예외 처리 (Fallback Strategy)
* **HTTP Header 판별**: `Accept: application/json` 또는 `X-Agent-Protocol: x402` 유무 확인.
* **x402 지원 AI**: 자율 JSON 협상 모듈(`/negotiate`)로 연결(a2a-x402 payment-required→submitted→completed).
* **Non-x402 클라이언트**: 402 대신, 고정가(Buy-It-Now)로 즉시 구매 가능한 **Solana Pay QR** 제공.

### 3. 결제 정산 비동기 파이프라인 (대회 권장 정석)
```text
[1] 구매자 AI 결제완료(pay.sh/USDC 온체인)
      │ webhook (서명검증)
[2] Cloud Run /api/v1/paysh/webhook → Pub/Sub 발행 (즉시 200, 논블로킹)
[3] Pub/Sub ─감지→ Eventarc → [4] Workflows 순차 실행:
      검증 → 라이선스 발급 → 인증서 Memo → Firestore 미러 → BigQuery 로그
```
> 로컬/TDD는 GCP 없이 `/settle` 동기 폴백이 동일 서비스 메서드를 호출(로직 SSOT).

---

## Ⅵ. 핵심 백엔드 명세 (Django 서비스 레이어)

> ⚠️ v0.1의 FastAPI 예시 코드는 **폐기**되었습니다. 실제 구현은 **Django + 서비스 레이어**이며, 함수 단위 EARS 요구사항·인수조건·TDD 테스트는 **[`docs/specs/`](./docs/specs/)** 가 권위 문서입니다. 아래는 개념 요약입니다.

| 기능 | 엔드포인트 | 서비스 | SPEC |
|------|-----------|--------|------|
| IP 등록 & 앵커링 | `POST /api/v1/ip/register` | GeminiService, ImageProcessor, SolanaService, StorageService | SPEC-001 |
| x402 인터셉트 | `GET /api/v1/ip/{id}` | X402Service, LicenseService | SPEC-002 |
| 자율 협상 | `POST /api/v1/ip/{id}/negotiate` | GeminiService, NegotiationEngine | SPEC-003 |
| 정산·인증서 | `POST /api/v1/ip/{id}/settle`, `POST /api/v1/paysh/webhook` | SolanaService(KMS), LicenseService, PubSubPublisher | SPEC-004 |
| 배치 라이선싱 | `POST /api/v1/ip/batch/*` | GeminiService(flash-lite), LicenseService | SPEC-007 |
| 로열티 분배 | (에스크로 정산 경로) | RoyaltyService, SolanaService.transfer_usdc | SPEC-008 |

**핵심 설계 원칙**:
- 모든 외부 I/O(Gemini/Solana/GCP/pay.sh)는 **서비스 레이어**로 격리 → TDD에서 mock.
- 협상 응답은 Gemini `response_schema`로 구조화(`{status, price_usdc, reason}`), 실패 시 규칙기반 폴백.
- 결제 검증은 recipient·mint·amount 전부 일치 + `confirmed` 커밋먼트. `payment_tx_sig` unique로 멱등.
- 창작자 보호 불변식: 협상가는 `min_price` 미만으로 절대 확정되지 않음.
- 하이브리드 저장: 응답에 원본을 절대 포함하지 않음(워터마크 프리뷰만 노출).

**데이터 모델(요약)**: Creator / IpAsset / NegotiationSession / License / RoyaltyDistribution / BatchOrder·BatchItem / AgentEvent — 상세는 [`docs/00-architecture-and-data-model.md`](./docs/00-architecture-and-data-model.md).

---

## Ⅶ. 외부 에이전트에게 스펙이 전달되는 2가지 방식

### 1. 런타임 동적 전달 (a2a-x402 payment-required)
외부 AI가 이미지 URL(`GET /api/v1/ip/{id}`)에 접근하면, 서버가 `HTTP 402`와 함께 협상·결제 스펙(JSON)을 즉시 실어 보냅니다:
```json
{
  "error": "Payment or License Required",
  "asset_id": "<uuid>",
  "preview_url": "<watermark_url>",
  "x402_version": "1",
  "accepts": [{ "scheme":"solana-usdc","network":"devnet","mint":"<USDC_MINT>","pay_to":"<creator_wallet>","max_amount_required":"<target_price>" }],
  "how_to_negotiate": {
    "endpoint": "/api/v1/ip/{id}/negotiate", "method": "POST",
    "required_payload": {"buyer_agent_id":"string","offer_usdc":"float","usage_type":"string"},
    "settle_endpoint": "/api/v1/ip/{id}/settle"
  }
}
```
외부 에이전트는 이 응답을 읽고 즉시 다음 협상을 이어갑니다.

### 2. 에이전트 레지스트리 / 플러그인 등록 (생태계 공개)
`/.well-known/ai-plugin.json` 규격을 공개하여, x402 생태계(awesome-x402, x402-agent-kit 등)나 AI 도구 레지스트리에서 외부 개발자가 가져다 쓸 수 있게 합니다.

---

## Ⅷ. 프로젝트 구조 (Django)

```text
veriproof/
├── manage.py  Dockerfile(Cloud Run)  .env.example
├── workflows/settlement.workflow.yaml   # GCP Workflows 정산 오케스트레이션
├── config/                              # settings, urls, wsgi
├── apps/{ip, negotiation, settlement, sandbox, common}/
├── services/                           # gemini, solana, kms_signer, storage, image_processor,
│                                       #   x402(a2a-x402/AP2), license, royalty, negotiation_engine,
│                                       #   event_recorder, firestore_mirror, bigquery_sink, pubsub_publisher
├── templates/  static/(css,js + firebase onSnapshot)
├── scripts/buyer_agent_sim.py          # 구매자 AI 시뮬레이터(x402_a2a)
└── tests/{unit, integration, e2e}/  conftest.py  factories.py
```

---

## Ⅸ. 개발 방법론 (TDD)

* **RED → GREEN → REFACTOR**: 각 SPEC 인수조건마다 실패 테스트를 먼저 작성.
* **커버리지**: 서비스 레이어 ≥ 85%. 외부 I/O 전부 mockable → 네트워크 없이 전 스위트 실행.
* **로컬 우선**: `FIRESTORE_ENABLED=false`, `STORAGE_BACKEND=local`, `AP2_ENABLED=false`, env 서명키로 GCP 없이 전 기능 동작. 클라우드 배포 시 플래그로 정석 파이프라인 활성화.
* 상세: [`docs/test-plan.md`](./docs/test-plan.md).

---

## Ⅹ. 3분 데모 영상 시나리오 (Submission Video)

* **[0:00 ~ 0:30] 문제 제기**: AI의 무단 스크랩 + 카드 수수료로 초소액 라이선스가 불가능했던 문제 제시.
* **[0:30 ~ 1:15] 창작자 경험**: 이미지 하나를 챗봇에 올리고 "저작권 등록해줘" → Gemini가 수초 만에 분석하고 솔라나 온체인에 등록.
* **[1:15 ~ 2:15] 하이라이트 (Multi-Agent Commerce)**: 화면 분할(좌 판매자 AI / 우 구매자 AI). 구매자 AI가 `x402` 헤더를 읽고 진입 → 1.0 USDC 제안 → 판매자 AI 1.8 USDC 역제안 → 타협 후 Solana Devnet에서 USDC 전송 라이브 연출(Explorer 링크).
* **[2:15 ~ 3:00] 요약 및 비전**: Cloud Run + Pub/Sub/Eventarc/Workflows + Firestore/BigQuery 기반의 확장성과, 솔라나가 만들 AI 에이전트 커머스 생태계 비전 제시.

---

## 📎 부록: 공식 참고 링크 (해커톤 리소스에서 검증)

- AP2: https://ap2-protocol.org/ · https://github.com/google-agentic-commerce/AP2
- a2a-x402(`x402_a2a`): https://github.com/google-agentic-commerce/a2a-x402
- x402(Solana): https://solana.com/ko/x402 · https://github.com/xpaysh/x402-agent-kit
- pay.sh: https://pay.sh/docs · Solana Pay: https://docs.solanapay.com/
- GCP Blockchain RPC: https://cloud.google.com/blockchain-rpc/docs/quickstart
- Eventarc+Workflows: https://cloud.google.com/blog/topics/developers-practitioners/integrating-eventarc-and-workflows
- Firestore: https://cloud.google.com/firestore/docs · BigQuery: https://cloud.google.com/bigquery/docs
- Cloud KMS(EC 서명): https://cloud.google.com/kms/docs/algorithms#elliptic-curve-signing
- ADK: https://goo.gle/agent-dev-kit · Vertex AI: https://cloud.google.com/vertex-ai/docs

---
*원본 초기 기획서는 `user_needs_v01.original.bak.md`로 보존됨.*
