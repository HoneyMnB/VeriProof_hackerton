# VeriProof `/live-demo` 발표 시연 시나리오

- 문서 상태: Draft
- 대상: 본선 발표자, 데모 운영자, 개발 검증 담당자
- 관련 스펙: [SPEC-009 — 발표 기준 데모 프로토콜 정합성](./specs/SPEC-009-demo-protocol-alignment.md)
- 대상 화면: `/live-demo`, 등록 화면, 외부 Buyer Agent 또는 `/sandbox`
- 원칙: 실제 PostgreSQL·Firestore·Solana Devnet 결과만 표시하며 mock, 고정 이벤트, 임의 transaction signature를 사용하지 않는다.

---

## 1. 시연 목표

창작자의 콘텐츠가 검증 가능한 지문으로 등록되고, AI 에이전트가 가격을 협상하며, 구매자가 USDC로 결제한 뒤 라이선스가 발행되는 과정을 하나의 실시간 흐름으로 보여준다.

발표자가 전달할 핵심 메시지는 다음과 같다.

> VeriProof는 콘텐츠의 등록 증명과 AI 에이전트의 자율 협상·결제를 연결합니다. 사용자는 Web3 키 관리나 SOL 가스비를 직접 다루지 않고, 검증된 USDC 거래 결과와 라이선스를 확인할 수 있습니다.

## 2. 현재 구현 경계

발표 전에 다음 차이를 반드시 인지한다.

| 항목 | 현재 런타임 | 본선 목표 |
|---|---|---|
| 로그인 | Django Passkey 지원 | 동일 |
| 지문·앵커링 | SHA-256·지각 해시·Solana Memo | 동일 |
| `/live-demo` 데이터 | 등록·거래 전체 이벤트, 소유권 필터, correlation grouping | 동일 |
| 화면 갱신 | Firestore listener → Django SSE, 장애 시 5초 snapshot fallback | 동일 |
| 협상·Sandbox | SOL offer·payment 경로 | USDC 협상·x402 경로 |
| Gasless | 완성된 E2E 아님 | Buyer SOL=0에서 성공 |
| 88/12 정산 | 실제 USDC 분배 미구현 | 실제 SPL USDC transfer tx 필요 |
| 페이지 스크롤 | `/live-demo` 범위에서 전체 스크롤 허용 | 동일 |

현재 SOL 샌드박스 결과를 USDC/Gasless 시연으로 설명해서는 안 된다. 본선 시연은 SPEC-009의 P0 인수 조건을 통과한 뒤 본 문서의 목표 시나리오를 사용한다.

## 3. 화면에 표시되는 실제 이벤트

`/live-demo`는 판매자 등록과 Buyer Agent 거래를 별도 트랙으로 표시하고, 각 트랙을
`correlation_id`로 그룹화한다. 등록 흐름은 다음 이벤트를 표시한다.

`REGISTRATION_STARTED → CONTENT_HASHED → AI_ANALYZED → ANCHORING_STARTED → ANCHORED → REGISTRATION_CERTIFICATE_ISSUED → ASSET_REGISTERED`

실패 시에는 실제 실패 지점에 `REGISTRATION_FAILED`를 표시한다. 거래 흐름은 다음 이벤트를 표시한다.

| 이벤트 | 화면 문구 | 발생 경로 |
|---|---|---|
| `ANCHORED` | Work anchored | 작품 등록 및 Solana 앵커링 성공 |
| `ASSET_DISCOVERED` | Asset discovered | Buyer Agent가 판매 자산 조회 |
| `HTTP_402` | Payment requested | Agent가 보호 자산의 결제 조건 요청 |
| `OFFER` | Offer submitted | Buyer Agent 가격 제안 |
| `COUNTER` | Counter offer | Seller Agent 역제안 |
| `ACCEPT` | Terms accepted | 가격 합의 |
| `REJECT` | Offer declined | 협상 거절 |
| `PAYMENT_SUBMITTED` | Payment submitted | Buyer가 x402 결제 서명 제출 |
| `PAYMENT_VERIFIED` | Payment verified | 온체인 결제 검증 및 License 최초 발급 |
| `LICENSE_ISSUED` | License issued | 영속 라이선스 발급 |
| `CERT_ISSUED` | License proof anchored | 라이선스 인증서 Memo 발행 |
| `ROYALTY_SPLIT` | Royalty distributed | 실제 로열티 분배 처리 |
| `BATCH_SETTLED` | Batch settled | 배치 라이선스 정산 |

`/live-demo`는 PostgreSQL의 모든 이벤트를 보여주는 화면이 아니다. `EventRecorder`가 Firestore의 `events` 컬렉션에 fan-out한 문서 중, 현재 로그인 사용자에게 `account_owner`로 연결된 자산만 반환한다.

화면 payload는 다음 안전 필드만 노출한다.

- `status`, `usage_type`, `reason`, `round`, `network`
- `offer_sol`, `counter_sol`, `price_sol`
- `offer_usdc`, `counter_usdc`, `price_usdc`

지갑 주소, 원본 URL, 결제 서명 전문은 현재 live feed payload에서 노출하지 않는다.

## 4. 시연 구성

### 4.1 권장 장비와 화면

- 발표 화면: `/live-demo`
- 운영 화면: 등록 화면과 Buyer Agent 실행 화면
- 검증 화면: Solana Explorer transaction 페이지
- 운영자는 발표 화면과 별도 브라우저 창 또는 별도 노트북을 사용한다.
- 두 화면은 동일한 VeriProof 계정을 사용해야 새 이벤트가 `/live-demo`에 나타난다.

### 4.2 권장 시간

전체 시연은 약 4분 30초를 목표로 한다.

| 구간 | 시간 | 내용 |
|---|---:|---|
| 문제와 화면 소개 | 30초 | Prove와 Pay 흐름 설명 |
| 작품 등록·증명 | 60초 | AI 분석, 지문, 앵커링 |
| Agent 협상 | 70초 | 402, offer, counter 또는 accept |
| USDC Gasless 결제 | 60초 | Buyer SOL=0, USDC 결제 |
| License·정산 | 40초 | License, certificate, 88/12 상태 |
| 검증·마무리 | 30초 | Explorer와 발표 메시지 |

## 5. 사전 준비

### 5.1 배포·환경

다음 항목을 발표 당일 배포본에서 확인한다.

- `FIRESTORE_ENABLED=true`
- 서비스 계정이 Firestore 읽기·쓰기 권한을 보유한다.
- Firestore database 설정이 배포 환경과 일치한다.
- Solana RPC가 Devnet을 가리킨다.
- USDC mint가 데모 Buyer 지갑의 토큰과 일치한다.
- x402 Facilitator verify/settle 호출이 성공한다.
- KMS/Secret Manager signer가 인증서와 fee sponsorship에 접근 가능하다.
- 운영 도메인의 Passkey RP ID와 origin이 고정되어 있다.
- `/live-demo` 페이지 전체와 이벤트 feed가 마우스 휠·트랙패드·키보드로 스크롤된다.

### 5.2 계정·자산

1. 발표 계정에 Passkey와 복구 로그인이 모두 동작하는지 확인한다.
2. 발표 계정에 실제 수취용 Devnet wallet을 연결한다.
3. 시연용 작품 파일은 발표자가 권리를 보유한 파일을 사용한다.
4. 같은 파일이 이미 등록되어 duplicate 처리되지 않는지 사전에 확인한다.
5. 신규 자산에 입력할 USDC 최저가와 목표가를 확정한다.
6. 기존 SOL 가격만 있는 자산은 본선 USDC 시연에 사용하지 않는다.

### 5.3 Buyer Agent·지갑

- Buyer Agent 지갑에는 예상 결제액보다 충분한 Devnet USDC가 있어야 한다.
- Gasless 시연을 위해 Buyer 지갑의 SOL 잔액은 0이어야 한다.
- 발표 직전 Explorer 또는 RPC로 USDC와 SOL 잔액을 다시 확인한다.
- Buyer와 Seller wallet을 분리한다.
- transaction signature를 사전에 입력해 두거나 이전 거래를 재사용하지 않는다.

### 5.4 Firestore 사전 확인

1. 테스트 자산 하나로 `ANCHORED` 이벤트를 발생시킨다.
2. `/api/v1/live-demo/stream`의 content type이 `text/event-stream`이고 초기 `snapshot`을 반환하는지 확인한다.
3. fallback `/api/v1/live-demo/events`가 `connected=true`, `source=firestore-sse`를 반환하는지 확인한다.
4. 다른 사용자의 자산 이벤트가 노출되지 않는지 확인한다.
5. 화면의 Events observed와 Registration flows가 API metrics와 일치하는지 확인한다.
6. 시연용 신규 자산은 아직 등록하지 않는다. 실제 등록 순간을 보여주기 위함이다.

## 6. 본선 목표 시나리오

### Scene 1 — Passkey 로그인과 Live 화면

**운영자 동작**

1. Passkey로 발표 계정에 로그인한다.
2. `/live-demo`를 연다.
3. 우측 상태가 `Live / Firestore · SSE`인지 확인한다.

**발표 대본**

> 창작자는 비밀번호나 복잡한 지갑 연결부터 시작하지 않습니다. Passkey로 로그인하면 되고, 이 화면에서 등록부터 결제와 라이선스 발행까지 검증된 이벤트만 실시간으로 확인합니다.

**성공 신호**

- 연결 상태가 Live이다.
- skeleton이 사라진다.
- empty 또는 기존 실제 이벤트가 표시된다.
- 페이지 전체 스크롤이 동작한다.

### Scene 2 — 작품 등록과 온체인 증명

**운영자 동작**

1. 등록 화면에서 시연 작품을 업로드한다.
2. AI가 생성한 설명·태그·카테고리·가격 제안을 확인한다.
3. USDC 최저가와 목표가를 확인한 뒤 등록한다.
4. `/live-demo`로 돌아온다.

**발표 대본**

> 작품을 올리면 AI가 메타데이터와 가격 후보를 준비합니다. 서버는 원본 SHA-256과 유사성 탐지를 위한 지각 해시를 만들고, 등록 지문을 Solana에 기록합니다. 이 기록은 콘텐츠 지문과 등록 시점을 검증하는 근거입니다.

**성공 신호**

- 등록 진행과 동시에 같은 Flow 카드에 등록 단계가 순서대로 나타난다.
- Registration flows가 증가한다.
- 자산 제목이 방금 등록한 작품과 일치한다.
- 라이브러리의 anchor tx가 Explorer에서 확인된다.

### Scene 3 — Agent discovery와 x402

**운영자 동작**

1. Buyer Agent가 신규 자산을 discovery한다.
2. 보호 자산의 라이선스 조건을 요청한다.
3. x402 Payment Required 응답을 받는다.

**발표 대본**

> 이제 사람 대신 외부 Buyer Agent가 작품을 발견합니다. 사용 권한이 없기 때문에 서버는 HTTP 402와 함께 USDC 결제 조건, 협상 진입점과 라이선스 조건을 돌려줍니다.

**성공 신호**

- `HTTP_402`가 신규 자산에 연결되어 나타난다.
- 네트워크가 Devnet이고 통화가 USDC이다.
- mint, recipient, atomic amount가 서버 결제 조건과 일치한다.

### Scene 4 — Seller/Buyer Agent 협상

**운영자 동작**

1. Buyer Agent가 최저가보다 낮지만 유효한 첫 `offer_usdc`를 제시한다.
2. Seller Agent가 `COUNTER`를 반환하도록 사전 검증된 가격 조합을 사용한다.
3. Buyer Agent가 counter 이상으로 재제안해 ACCEPT를 받는다.

**발표 대본**

> 구매자 AI와 판매자 AI가 사용 목적과 창작자가 정한 가격 하한을 기준으로 협상합니다. LLM이 어떤 응답을 생성해도 시스템의 가격 가드가 최저가 미만의 판매를 허용하지 않습니다.

**성공 신호**

- `OFFER → COUNTER → OFFER → ACCEPT` 순서가 유지된다.
- 표시 금액이 모두 USDC이다.
- Negotiation moves가 이벤트 수만큼 증가한다.
- `final_price_usdc`가 최저가 이상이다.

> 협상 결과의 비결정성을 줄이기 위해 발표 전에 동일 자산·가격·usage type 조합으로 반복 검증한다. 결과 문장을 하드코딩하거나 협상 결과를 DB에 미리 삽입하지 않는다.

### Scene 5 — USDC Gasless 결제

**운영자 동작**

1. Buyer Agent가 ACCEPT된 최종 USDC 가격으로 x402 payment를 제출한다.
2. Facilitator 또는 VeriProof sponsor가 SOL 수수료를 부담한다.
3. 서버가 verify와 settle 결과를 검증한다.

**발표 대본**

> 구매자 지갑에는 SOL이 없습니다. Buyer Agent는 합의된 USDC만 지불하고, 네트워크 수수료는 VeriProof 결제 인프라가 후원합니다. 그래서 AI가 소액 라이선스를 구매할 때 Web3 가스비를 직접 관리할 필요가 없습니다.

**성공 신호**

- Buyer의 사전 SOL 잔액이 0이다.
- payment tx가 실제 Devnet Explorer에서 확인된다.
- mint가 지정된 Devnet USDC이다.
- recipient와 atomic amount가 협상 결과와 일치한다.
- `/live-demo`에 `PAYMENT_VERIFIED`가 나타난다.
- Settlements가 1 증가한다.

### Scene 6 — License·인증서·88/12 정산

**운영자 동작**

1. License ID와 download 권한을 확인한다.
2. certificate tx를 Explorer에서 연다.
3. P1 정산이 구현된 경우에만 창작자와 플랫폼 transfer tx를 확인한다.

**발표 대본**

> 결제가 검증되는 즉시 라이선스가 발행되고, 라이선스 인증서도 Solana에 기록됩니다. 정산 기능이 활성화된 경우 창작자 몫과 플랫폼 수수료는 실제 USDC 송금 결과로 각각 확인할 수 있습니다.

**성공 신호**

- `PAYMENT_VERIFIED → CERT_ISSUED` 순서이다.
- 같은 payment tx에 License가 하나만 존재한다.
- certificate tx가 실제 Explorer에서 열린다.
- 88/12를 발표하는 경우 실제 transfer tx 합계가 총 결제액과 같다.
- 실제 분배 완료 시에만 `ROYALTY_SPLIT` 또는 분배 완료 상태가 나타난다.

### Scene 7 — 마무리

**발표 대본**

> VeriProof는 증명과 결제를 분리된 기능으로 두지 않습니다. 콘텐츠 지문이 거래를 가능하게 하고, AI 에이전트의 거래가 다시 창작자의 수익과 검증 가능한 라이선스로 연결됩니다.

## 7. 현재 런타임 확인용 시나리오

SPEC-009 완료 전에는 다음 범위만 현재 기능으로 시연할 수 있다.

1. Passkey 로그인
2. 실제 작품 등록
3. SHA-256·지각 해시 생성
4. 실제 Solana anchor tx
5. SOL 기반 Sandbox 협상과 결제 검증
6. License·certificate 발행
7. Firestore `ANCHORED`, `HTTP_402`, 협상, 결제, 인증서 이벤트 표시

이 시나리오에서는 다음 문구를 사용하지 않는다.

- `USDC 결제 완료`
- `Gasless 완료`
- `창작자 88% 즉시 정산`
- `USDC 로열티 자동 분배`

현재 `/sandbox`는 `DEBUG=true`이고 로그인 사용자가 staff일 때만 접근할 수 있다. 또한 유효한 자산 ID, 확인된 실제 Devnet payment signature, Buyer public wallet이 필요하다. 임의 signature를 넣으면 성공 시연이 아니라 실제 정산 실패 시나리오가 된다.

## 8. 장애 대응 시나리오

장애를 성공처럼 숨기거나 이전 결과를 신규 실시간 결과처럼 표현하지 않는다.

### Firestore Offline

- 화면 신호: `Offline / Firestore is unavailable` 또는 disabled
- 발표자 대응: “거래 원장과 권한은 PostgreSQL에 보존되며, 현재 실시간 미러 연결이 중단되었습니다.”라고 설명한다.
- 운영자 대응: 서비스 계정, database 설정, 네트워크를 확인한다.
- 금지: 클라이언트에서 임의 이벤트를 추가해 Live 상태로 변경

### 앵커링 실패

- 등록을 중단하고 `ANCHORED`가 나타나지 않는 사실을 그대로 보여준다.
- RPC와 signer 상태를 확인한다.
- anchor tx 없이 거래 단계로 진행하지 않는다.

### 협상 미합의

- `REJECT` 또는 실패 상태를 정상적인 가격 보호 결과로 설명한다.
- 발표 중 임의로 DB의 최저가를 수정하지 않는다.
- 성공 시나리오가 필요하면 사전 검증한 다른 실제 자산으로 처음부터 다시 시작한다.

### x402·Gasless 실패

- License와 `PAYMENT_VERIFIED`가 생성되지 않았는지 확인한다.
- Facilitator, sponsor 잔액, USDC mint와 atomic amount를 확인한다.
- 일반 SOL 결제로 자동 전환하지 않는다.

### 인증서 실패

- 검증된 결제와 License는 보존한다.
- `CERT_ISSUED`를 완료로 표시하지 않는다.
- 재시도 작업과 결제 멱등성을 확인한다.

### 88/12 일부 분배 실패

- 성공 leg와 실패 leg를 구분한다.
- 실패 leg만 동일 idempotency key로 재시도한다.
- 모든 실제 transfer tx가 확인되기 전에는 “정산 완료”라고 발표하지 않는다.

## 9. 발표 직전 체크리스트

### 30분 전

- [ ] 배포 revision과 commit을 기록했다.
- [ ] DB migration이 적용되었다.
- [ ] Firestore 연결이 Live이다.
- [ ] Solana RPC와 Devnet 상태가 정상이다.
- [ ] Facilitator verify/settle가 정상이다.
- [ ] KMS/Secret Manager 접근이 정상이다.
- [ ] Buyer USDC는 충분하고 SOL은 0이다.
- [ ] Seller와 Buyer wallet이 다르다.
- [ ] 발표 자산 파일은 미등록 상태다.
- [ ] Passkey와 복구 로그인이 모두 가능하다.
- [ ] `/live-demo` 전체 페이지 스크롤이 동작한다.

### 5분 전

- [ ] `/live-demo`의 기존 metrics를 기록했다.
- [ ] 운영 화면과 발표 화면이 같은 계정이다.
- [ ] Browser zoom은 100%이고 화면이 잘리지 않는다.
- [ ] DevTools, 비밀값, 개인키가 화면에 노출되지 않는다.
- [ ] Explorer 탭은 Devnet으로 준비되어 있다.
- [ ] 타이머와 발표 대본이 준비되어 있다.

### 시연 후

- [ ] payment tx와 certificate tx를 기록했다.
- [ ] License가 한 개만 발급되었다.
- [ ] 88/12를 시연한 경우 분배 tx 합계를 확인했다.
- [ ] Firestore와 PostgreSQL 이벤트가 같은 자산·세션을 가리킨다.
- [ ] 실패 이벤트와 운영 로그에 비밀정보가 없는지 확인했다.

## 10. 완료 판정

본선 목표 시연은 다음 조건을 모두 충족해야 완료로 판정한다.

1. 신규 자산의 실제 `ANCHORED` tx가 존재한다.
2. USDC 기반 `HTTP_402 → OFFER/COUNTER/ACCEPT`가 단일 session으로 연결된다.
3. Buyer SOL=0 상태에서 실제 USDC payment tx가 성공한다.
4. 동일 payment tx로 License가 정확히 하나 발급된다.
5. 실제 certificate tx가 발급된다.
6. `/live-demo`가 동일 자산의 이벤트를 실제 순서대로 표시한다.
7. 페이지와 feed 스크롤이 데스크톱·모바일에서 동작한다.
8. 88/12 또는 로열티를 발표한다면 모든 실제 SPL USDC transfer tx가 존재한다.
9. 다른 사용자의 이벤트와 민감 payload가 노출되지 않는다.
10. 실패 시 가짜 데이터나 통화 fallback 없이 fail-closed로 종료된다.
