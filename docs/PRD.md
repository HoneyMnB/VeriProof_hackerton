# PRD — VeriProof AI

> **Agentic IP Protocol & Automated Licensing Marketplace**
> 창작자 IP를 대화형으로 등록하고, 외부 AI 에이전트와 HTTP 402(`x402`) 기반으로 협상하여 Solana(USDC)로 즉시 정산하는 에이전트 전용 저작권 라이선싱 프로토콜 및 마켓플레이스.

- 문서 버전: v1.0
- 작성일: 2026-07-23
- 상태: Approved (설계 결정 확정 완료)
- 관련 문서: [아키텍처·데이터모델](./00-architecture-and-data-model.md), [테스트 계획](./test-plan.md), [SPEC 목록](./specs/)

---

## 1. 배경 및 문제 정의 (Problem Statement)

| # | 문제 | 근거 |
|---|------|------|
| P1 | AI 시대 창작권 침해 | 무분별한 크롤링·캡처로 창작자 IP가 보호되지 않음 |
| P2 | 전통 결제의 한계 | 카드 결제는 회원가입·본인인증·CVC가 필요해 **AI 에이전트가 자율 결제 불가**, 건당 수수료 구조로 **초소액(1~100원) 결제 불가** |
| P3 | M2M 거래 인프라 부재 | AI가 스스로 라이선스를 구매할 표준 협상·정산 프로토콜이 없음 |

### 솔루션 요약
- **x402 접근 제어**: AI 접근 시 `HTTP 402 Payment Required` + 협상 스펙(JSON)을 런타임에 즉시 전달.
- **Gemini 자율 협상**: 판매자 AI가 창작자 조건(최소가/목표가/용도)에 따라 구매자 AI와 REST로 가격 협상.
- **Solana 초소액 정산**: Devnet USDC로 건당 0.05 USDC 수준의 마이크로 라이선싱 실현.

---

## 2. 제품 목표 (Goals) 및 성공 지표 (Success Metrics)

### 2.1 4대 핵심 목표
1. **Zero-Friction 등록**: 이미지 업로드 → 대화 → 온체인 등록까지 서식 입력 없이 완료.
2. **Multi-Agent 자율 협상**: 판매자·구매자 AI가 창작자 조건에 맞춰 자율 가격 협상.
3. **HTTP 402 Micro-Licensing**: 무단 접근 차단 + 초소액 USDC 즉시 결제.
4. **위변조 불가 온체인 인증**: 결제 즉시 Solana 장부에 라이선스·해시 기록 영구 보관.

### 2.2 성공 지표 (해커톤 심사 기준 정렬)
| 지표 | 목표값 |
|------|--------|
| E2E 데모 성공률 (등록→402→협상→정산→인증서) | 100% 재현 |
| Solana Devnet 실제 트랜잭션 증빙 | 앵커링/결제/인증서 각 1건 이상 실 트랜잭션 |
| 협상 1회 왕복 응답시간 | ≤ 5초 (Gemini 추론 포함) |
| 테스트 커버리지 (서비스 레이어) | ≥ 85% |
| 3개 페이지 데모 동작 | Page 1/2/3 전부 |

---

## 3. 사용자 및 액터 (Actors)

| 액터 | 설명 | 인터페이스 |
|------|------|-----------|
| **창작자 (Creator)** | 이미지 IP를 등록·관리하는 인간 사용자 | Web (Page 1, 2) |
| **판매자 AI (Seller Agent)** | 창작자 조건을 대변해 협상하는 Gemini 에이전트 | 서버 내부 (`gemini-3.6-flash`) |
| **구매자 AI (Buyer Agent)** | 외부에서 x402로 접근해 협상·결제하는 자율 에이전트 | M2M REST API / 샌드박스 시뮬레이터 |
| **심사위원/관람자** | 협상·정산 과정을 관람 | Web (Page 3) |
| **플랫폼 (Platform)** | 에스크로 지갑으로 로열티 분배·인증서 발행 | 서버 내부 (Devnet keypair) |

---

## 4. 범위 (Scope)

### 4.1 In-Scope (MVP — 확정)
- **핵심 플로우 (시나리오 1, B2C)**: 등록 → x402 인터셉트 → 자율 협상 → USDC 결제검증 → 라이선스·인증서 발급.
- **시나리오 2 (B2B 초소액 대량 라이선싱)**: 다중 이미지 배치 협상·정산, 이미지당 초소액 결제.
- **시나리오 3 (2차 창작 로열티 자동 분배)**: 플랫폼 에스크로 지갑을 통한 원작자/2차창작자 실제 분배 송금.
- **3개 웹 페이지**: 대화형 워크스페이스 / IP 라이브러리·증명서 / 협상 샌드박스.
- **에이전트 디스커버리**: `/.well-known/ai-plugin.json` 정적 스펙 공개.

### 4.2 Out-of-Scope (본 MVP 제외)
- 온체인 Rust 스마트컨트랙트(Anchor) 배포 — 로열티는 서버 오케스트레이션 + 실제 SPL 송금으로 대체.
- Metaplex 기반 정식 NFT 인증서 — 인증서는 Memo 트랜잭션 + DB 기록으로 구현.
- Solana Mainnet 운영 — Devnet 한정.
- 창작자 지갑 커스터디/키관리 — 창작자는 pubkey만 제공(자기수탁).
- 이미지 원본의 영구 보관 — 하이브리드 정책(전달 후/기간만료 시 purge).

---

## 5. 핵심 설계 결정 (Design Decisions)

| ID | 결정 | 근거 |
|----|------|------|
| DD-1 | **하이브리드 이미지 저장** | 썸네일+워터마크 프리뷰는 영구 보관, 원본은 임시 보관 후 purge, 원본 SHA-256 해시는 영구 보관·온체인 앵커링. 저장비용 절감과 "원본 전달" 요구를 동시 충족. |
| DD-2 | **Solana Devnet 실연동** | USDC 입금검증 + Memo 해시앵커링 + 인증서 Memo 발행을 실제 Devnet에서 수행. 심사 신뢰도 확보. |
| DD-3 | **P2P 직접 결제 + 서버 검증** | 일반 판매는 구매자→창작자 직접 송금 후 tx signature 검증. 플랫폼이 자금을 보유하지 않음(리스크↓). |
| DD-4 | **에스크로 분배(S3 한정)** | 로열티 분배 시에만 플랫폼 에스크로 지갑이 수취 후 원작자/2차창작자에게 실제 분할 송금. |
| DD-5 | **실제 모델 ID 사용** | `gemini-3.6-flash`(멀티모달 Vision + 협상 추론), `gemini-3.5-flash-lite`(고속·구조화 JSON·배치 견적). env로 교체 가능. |
| DD-6 | **Django 동기 뷰 + 서비스 레이어** | 외부 I/O(Gemini/Solana/GCP)를 서비스 클래스로 분리해 TDD에서 mocking 용이. |
| DD-7 | **협상 JSON 구조화 출력** | Gemini `response_schema`로 협상 응답을 강제 스키마화하여 파싱 안정성 확보. |
| DD-8 | **샌드박스 실시간은 Firestore 리스너** | 결제상태·협상 이벤트를 Firestore에 미러링하고 프론트가 `onSnapshot`으로 실시간 반영(0.1초). 로컬/오프라인은 `/api/v1/events` 폴링(2초) 폴백. |
| DD-9 | **Cloud Run + PostgreSQL(SoR) + Firestore(실시간) + BigQuery(로그)** | 컴퓨트는 Cloud Run(GKE 지양). PostgreSQL(Cloud SQL)=관계형 시스템 오브 레코드, Firestore=실시간 상태 현황판, BigQuery=거래·이벤트 감사로그. 대회 권장 정석 조합 채택. |
| DD-10 | **공식 결제 프로토콜 채택** | x402 인터셉터를 **a2a-x402**(`x402_a2a`) 흐름(payment-required→submitted→completed)과 **AP2** mandate(VDC) 개념에 정렬. **pay.sh** webhook 수신 + Solana Pay 지원. |
| DD-11 | **Cloud KMS + Secret Manager 서명** | 플랫폼 에스크로 서명키를 Cloud KMS(EC 서명)/Secret Manager로 관리(원시 개인키 미노출). 로컬 개발은 env base58 폴백. |
| DD-12 | **비동기 정산 파이프라인** | 결제 정산은 Pub/Sub+Eventarc+Workflows 비동기 파이프라인(pay.sh webhook→검증→PostgreSQL 갱신→인증서·영수증→BigQuery). 로컬/TDD는 동기 폴백 경로 제공. |
| DD-13 | **Google Cloud Blockchain RPC** | Solana 접근은 GCP Blockchain RPC 우선, public devnet RPC 폴백. `SOLANA_RPC_URL`로 전환. |
| DD-14 | **일정/로드맵 문서 제외** | 개발 일정은 산출물 문서 범위에서 제외(운영 판단 영역). SPEC은 일정 비의존적으로 작성. |

---

## 6. 기능 요구사항 요약 (SPEC 매핑)

| SPEC | 제목 | 시나리오/페이지 |
|------|------|----------------|
| [SPEC-001](./specs/SPEC-001-ip-registration.md) | IP 등록 & 온체인 앵커링 (Gemini Vision + 하이브리드 저장) | Page 1 / S1 |
| [SPEC-002](./specs/SPEC-002-x402-interceptor.md) | x402 접근 인터셉터 & 클라이언트 판별 | 프로토콜 / S1·S2 |
| [SPEC-003](./specs/SPEC-003-negotiation.md) | Gemini 자율 가격 협상 | 협상 / S1 |
| [SPEC-004](./specs/SPEC-004-settlement.md) | Solana USDC 정산 & 라이선스·인증서 | 정산 / S1 |
| [SPEC-005](./specs/SPEC-005-library-dashboard.md) | IP 라이브러리 & 온체인 증명서 대시보드 | Page 2 |
| [SPEC-006](./specs/SPEC-006-sandbox.md) | Multi-Agent 협상 샌드박스 시뮬레이터 | Page 3 |
| [SPEC-007](./specs/SPEC-007-batch-licensing.md) | B2B 초소액 대량 라이선싱 | S2 |
| [SPEC-008](./specs/SPEC-008-royalty-split.md) | 2차 창작 로열티 자동 분배 | S3 |

---

## 7. 사용자 스토리 (User Stories)

- **US-1**: 창작자로서, 이미지를 드래그&드롭하고 대화하듯 등록하여 저작권을 온체인에 남기고 싶다.
- **US-2**: 창작자로서, 최소가·목표가만 설정하면 이후 협상을 AI가 대신 처리해주길 원한다.
- **US-3**: 구매자 AI로서, 이미지 URL 접근 시 협상·결제 방법을 응답에서 즉시 파악하고 싶다.
- **US-4**: 구매자 AI로서, 사람 개입 없이 가격을 협상하고 USDC로 즉시 결제하고 싶다.
- **US-5**: 창작자로서, 누가 언제 얼마에 사갔는지 온체인 증빙과 함께 확인하고 싶다.
- **US-6**: 뉴스 AI로서, 수백 장을 건당 초소액으로 한 번에 라이선싱하고 싶다. (S2)
- **US-7**: 2차 창작자로서, 판매 수익이 원작자와 자동으로 분배되길 원한다. (S3)
- **US-8**: 심사위원으로서, 협상→온체인 결제 전 과정을 화면에서 실시간으로 보고 싶다.

---

## 8. 유스케이스 시나리오 (Detailed)

### 시나리오 1 — B2C 라이선스 매매
등록(최소 1.5 USDC) → 마케팅 AI가 1.0 USDC 제안 → 판매자 AI가 1.8 USDC 역제안 → 승인 → 구매자→창작자 1.8 USDC 송금 → 서버가 tx 검증 → 워터마크 제거 원본(임시 서명URL) + 온체인 인증서 발급.

### 시나리오 2 — B2B 대량 라이선싱
뉴스 AI가 배치 협상 요청(100개 asset) → 이미지당 0.05 USDC 산정 → 일괄/개별 결제 → 배치 정산 → 각 건 라이선스 발급 및 다운로드 토큰 반환.

### 시나리오 3 — 2차 창작 로열티 분배
2차 창작물(부모 asset 링크, 분배율 70/30) 10 USDC 판매 → 구매자→플랫폼 에스크로 송금 → 에스크로가 2차창작자 7 USDC / 원작자 3 USDC로 실제 분할 송금 → 두 송금 tx 기록.

---

## 9. 비기능 요구사항 (NFR)

| 범주 | 요구사항 |
|------|---------|
| 성능 | 협상 1왕복 ≤ 5초, 정산 검증 ≤ 10초(Devnet confirm 포함) |
| 보안 | 지갑 개인키는 Devnet 전용·env 보관, 원본 다운로드는 만료형 서명 토큰, 입력 검증(pydantic/Django forms) |
| 가용성 | 외부 API(Gemini/Solana) 실패 시 재시도 3회 + 명확한 오류 응답 |
| 저장 | 원본 purge 정책(전달 완료 또는 N일 경과), 해시는 영구 보관 |
| 관측성 | 모든 에이전트 이벤트를 `AgentEvent`로 기록(타임라인·샌드박스 공용) |
| 테스트 | 서비스 레이어 커버리지 ≥ 85%, 외부 I/O는 전부 mockable |

---

## 10. 리스크 및 완화 (Risks)

| 리스크 | 영향 | 완화 |
|--------|------|------|
| Gemini JSON 파싱 실패 | 협상 중단 | `response_schema` 강제 + 재시도 + 폴백 규칙기반 협상 |
| Devnet 혼잡/RPC 실패 | 정산 지연 | 재시도 3회, 커밋먼트 `confirmed`, 실패 시 명확 오류 |
| 원본 purge 후 재요청 | 재다운로드 불가 | 라이선스 보유자에겐 재요청 시 원본 복원 불가 → 최초 전달 시 구매자 보관 책임 명시 |
| USDC devnet mint 불일치 | 검증 오탐 | `USDC_MINT_ADDRESS` 검증을 필수 조건에 포함 |
| 로열티 분배 부분 실패 | 자금 정합성 | 분배는 원자적 처리 시도 + 부분실패 시 보상 로그·재시도 큐 |

---

## 11. 완료 정의 (Definition of Done)

- 8개 SPEC의 모든 인수조건(AC) 통과.
- 서비스 레이어 테스트 커버리지 ≥ 85%.
- Devnet에서 앵커링/결제/인증서/로열티 분배 각 실 트랜잭션 증빙 확보.
- Page 1/2/3 데모 시나리오 3종 무중단 재현.
- `README`에 실행·데모 절차 문서화.
