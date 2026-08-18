# VeriProof AI Technical Blueprint 개정안

> 검토 대상: `VeriProof_AI_Technical_Blueprint.pdf` (4페이지)  
> 작성 목적: 실제 시스템 구조와 발표 장표의 기술 설명을 일치시키기 위한 페이지별 편집 가이드  
> 결제 전제: **구매자는 USDC만 지불하고, 시스템 또는 Facilitator가 SOL 네트워크 수수료를 대납하는 단일 Gasless 결제 구조**

## 1. 개정 원칙

- 발표의 중심은 미래 계획이 아니라 현재 시스템에 구현된 기술 구조와 데이터 흐름으로 둔다.
- Buyer Agent는 테스트 UI가 아니라, Seller Agent와 A2A로 통신하고 결제 정책을 집행하는 독립 서비스로 표현한다.
- `x402`는 모든 구매의 USDC 결제 요구, 승인, 검증 및 정산 흐름에 적용한다.
- Gasless는 수수료가 사라지는 구조가 아니다. 구매자는 USDC만 지불하고 시스템 또는 Facilitator의 Fee Payer가 SOL 네트워크 수수료를 부담한다.
- 온체인 기록은 콘텐츠 무결성, 등록 시점, 지갑 연계성을 증명한다. 법적 저작권 자체를 단독으로 확정한다고 표현하지 않는다.
- 제품명과 기술명은 장표 전체에서 동일하게 표기한다.

---

## 2. 페이지별 수정안

### Page 2. 전체 시스템 아키텍처

#### 수정 수준

전체 재구성. 현재 장표에서 가장 먼저 보강해야 할 페이지다.

#### 제목 교체

> Cloud Run에서 분리 실행되는 에이전트·권리·결제·실시간 이벤트 구조

#### 권장 구조도

```text
┌───────────────────────────────────────────────────────────────┐
│ GCP · Cloud Run                                               │
│                                                               │
│  ┌─────────────────────────┐                                  │
│  │ Buyer Agent B           │                                  │
│  │ Gemini ADK              │                                  │
│  │ Autonomous Payment      │                                  │
│  │ Policy                  │                                  │
│  └────────────┬────────────┘                                  │
│               │ A2A 1.0                                      │
│               │ Agent Card · JSON-RPC                         │
│               ▼                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │ VeriProof Web                                           │  │
│  │ Django 5.x · ASGI/Uvicorn                               │  │
│  │ Seller Agent A · Web/API · x402 V2                      │  │
│  └──────┬──────────┬───────────┬───────────┬───────────────┘  │
│         │          │           │           │                  │
│         ▼          ▼           ▼           ▼                  │
│   Vertex AI   x402 Facilitator  Solana    Relational DB       │
│   Gemini      Verify/Settle     Devnet    Django ORM SSOT     │
│               Fee Payer         SOL/USDC                      │
│                                 Memo                           │
└───────────────────────────────────────────────────────────────┘

Live Event Path
Django ORM AgentEvent SSOT
  → EventRecorder + correlation_id
  → Firestore Mirror
  → Authenticated Django ASGI SSE
  → Browser EventSource
  → 5-second polling fallback
```

#### 반드시 반영할 내용

- `ASGI`를 별도 제품처럼 표시하지 않고 Django의 실행 방식인 `Django 5 · ASGI`으로 표기한다.
- Buyer Agent B와 VeriProof Web/Seller Agent A가 각각 Cloud Run에서 분리 실행됨을 보여준다.
- 두 에이전트 사이에 `A2A 1.0 · Agent Card · JSON-RPC`를 표기한다.
- x402 Facilitator는 USDC 결제 검증과 정산을 담당하며, Gasless 경로에서는 Fee Payer 역할도 함께 표시한다.
- 관계형 DB의 `AgentEvent`를 이벤트의 SSOT로 표현하고 Firestore는 실시간 전달용 Mirror로 구분한다.
- 브라우저가 Firestore에 직접 연결되지 않으며, 인증된 Django SSE를 통해 이벤트를 수신함을 명시한다.

#### 모델명 표기

배포 환경의 모델은 다음처럼 표기한다.

> Vertex AI · Gemini

---

### Page 3. A2A 협상 및 결제 시퀀스

#### 수정 수준

전체 재구성. 구매자의 USDC 결제와 시스템의 SOL 수수료 대납을 하나의 고정 흐름으로 보여준다.

#### 제목 교체

> A2A 협상과 x402 기반 Gasless USDC 결제

#### 권장 4단계 흐름

```text
01. A2A Discovery & Negotiation
    Buyer Agent → Agent Card 조회
    Buyer Agent ↔ Seller Agent: OFFER · COUNTER · ACCEPT

02. x402 Payment Required
    HTTP 402 + PAYMENT-REQUIRED
      → 결제 금액 · USDC Mint · 수취인 · 네트워크 제시

03. Gasless USDC Settlement
    Buyer의 USDC 결제 승인/서명
      → Sponsor 또는 Facilitator가 SOL Gas 부담
      → Facilitator Verify
      → Facilitator Settle
      → PAYMENT-RESPONSE

04. License Fulfillment
    결제 검증 완료
      → License 발급
      → On-chain License Certificate 기록
      → 만료 시간이 있는 Download Token 전달
```

#### 현재 장표에서 바로잡을 점

- `Settle → Verification`으로 보이는 순서를 `Verify → Settle`로 수정한다.
- `Buyer wallet signs USDC transaction and submits`만으로 Gasless를 설명하지 않는다.
- 구매자의 USDC 승인과 SOL 수수료 대납 주체를 시각적으로 분리한다.
- 결제 방식 선택 단계 없이 모든 거래가 동일한 Gasless USDC 흐름을 통과하도록 표현한다.
- License 발급은 결제 완료 이후의 fulfillment 단계로 둔다.

#### Gasless 설명 문구

> Gasless는 수수료가 없는 구조가 아니라, Buyer 대신 시스템 또는 Facilitator가 SOL 네트워크 수수료를 부담하는 구조다. Buyer는 결제 금액인 USDC만 보유하면 된다.

#### 자율 결제 안전 정책 박스

```text
AUTONOMOUS PAYMENT POLICY
Allowed Network · Allowed Token Mint · Per-payment Limit
Approval Gate · Sponsor Balance · Fail-closed
```

#### 발표 시 강조점

- A2A는 협상 메시지와 상태 전이를 표준화한다.
- x402는 모든 구매에서 결제 조건 제시, USDC 승인, 검증 및 정산을 연결한다.
- SOL은 구매 통화가 아니라 Sponsor 또는 Facilitator가 부담하는 네트워크 수수료 통화다.

---

### Page 4. Passkey 및 온체인 증명

#### 수정 수준

용어와 실제 인증 분기 수정이 중요하다.

#### 좌측 제목 교체

기존의 일반적인 Passkey 설명 대신 다음 제목을 사용한다.

> Passkey 기반 인증서 접근 보호

#### Passkey 실제 흐름

```text
/library에서 인증서 보기
  → Asset Owner 확인
  → 계정의 Passkey 등록 여부 확인
      ├─ Passkey 있음: WebAuthn 인증 필수
      └─ Passkey 없음: 계정 비밀번호 재인증
```

#### 표현 수정

- `Zero Trust Enforcement` 대신 `Certificate Step-up Authentication`을 사용한다.
- Passkey가 등록된 계정은 인증서 열람 시 Passkey를 반드시 사용한다고 설명한다.
- 비밀번호는 Passkey가 없는 계정에만 제공되는 fallback임을 표시한다.
- Passkey를 단순 로그인 편의 기능이 아니라 고가치 자산 접근의 재인증 수단으로 설명한다.

#### 우측 제목 교체

기존:

> 온체인 소유권 증명

수정:

> 온체인 무결성·등록 사실 증명

#### 콘텐츠 앵커링 흐름

```text
Primary Image + Gallery Images
  → 파일별 SHA-256
  → 업로드 순서를 보존한 Manifest SHA-256
  → Creator Wallet + Asset ID 결합
  → Solana Memo 기록
  → Transaction Signature / Explorer 검증
```

#### 세 가지 증명 레이어

| 증명 | 온체인에 연결되는 핵심 정보 | 의미 |
|---|---|---|
| Content Anchor | Manifest Hash + Creator Wallet | 콘텐츠 무결성과 지갑 연계 |
| Registration Certificate | Asset ID + Creator Wallet + Content Hash | 등록 사실과 등록 시점 검증 |
| License Certificate | Asset ID + Buyer Wallet + License Memo Digest | 라이선스 발급 사실 검증 |

#### 필수 고지 문구

> 온체인 앵커는 콘텐츠 무결성·등록 시점·지갑 연계성을 증명하며, 법적 저작권 자체를 단독으로 확정하지 않는다.

---

## 3. 장표 전체 용어 통일

| 기존 또는 혼용 표현 | 권장 표현 |
|---|---|
| Django Web 5 | Django 5.x · ASGI/Uvicorn |
| ASGI Server | Django ASGI Application |
| Firestore SSOT | Django ORM AgentEvent SSOT + Firestore Mirror |
| Firestore SSE | Authenticated Django ASGI SSE backed by Firestore listener |
| On-chain Ownership Proof | On-chain Integrity & Registration Proof |
| Gas-free | Gasless · Sponsor-paid SOL Network Fee |
| USDC Settlement | x402 Gasless USDC Settlement |
| SOL Payment | SOL Fee Sponsorship |
| Agent Communication | A2A 1.0 · Agent Card · JSON-RPC |
| Certificate Authentication | Asset-scoped Step-up Authentication |

---

## 4. 수정 우선순위

1. **Page 3 결제 흐름**: 결제 방식 선택을 제거하고 Gasless USDC 단일 경로와 x402의 `Verify → Settle` 순서를 명확히 한다.
2. **Page 2 시스템 구조**: Buyer Agent, Seller Agent, Cloud Run, A2A, x402 Facilitator, DB/Firestore/SSE 관계를 한 장에 표현한다.
3. **Page 4 온체인 표현**: 법적 소유권 증명이 아니라 무결성·등록 사실 증명으로 수정한다.
4. **Page 4 Passkey 분기**: Passkey 보유 여부에 따른 실제 Step-up 인증 흐름을 반영한다.

---

