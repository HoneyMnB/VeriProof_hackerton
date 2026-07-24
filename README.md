# VeriProof AI — 문서 인덱스

> **Agentic IP Protocol & Automated Licensing Marketplace**
> 창작자 IP를 대화형으로 등록하고, 외부 AI 에이전트와 HTTP 402(x402/a2a-x402/AP2) 기반으로 협상하여 Solana(USDC)로 즉시 정산하는 에이전트 전용 저작권 라이선싱 프로토콜 및 마켓플레이스. (Google Cloud × Solana 해커톤)

## 확정 스택 (한눈에)
- **App**: Django 5 + Vanilla HTML/CSS/JS, **PostgreSQL(Cloud SQL)** 시스템 오브 레코드
- **컴퓨트**: **Cloud Run** (GKE 미사용)
- **AI**: Gemini `gemini-3.1-flash-lite`(현재 모든 호출 기본 모델), Vertex AI 또는 Gemini API
- **결제**: x402 + **a2a-x402**(`x402_a2a`) + **AP2**(VDC mandate) + **pay.sh** + Solana Pay
- **체인**: Solana Devnet, **Google Cloud Blockchain RPC**, SPL USDC + Memo, **Cloud KMS/Secret Manager** 서명
- **비동기/데이터**: **Pub/Sub + Eventarc + Workflows**, **Firestore**(실시간) + **BigQuery**(감사로그)

## 문서 구성
| 문서 | 내용 |
|------|------|
| [PRD.md](./PRD.md) | 제품 요구사항, 목표, 범위, 설계결정(DD), 유스케이스, NFR, 리스크, DoD |
| [00-architecture-and-data-model.md](./00-architecture-and-data-model.md) | 기술스택, 시스템/비동기 아키텍처, 결제 프로토콜, 서비스 인터페이스, 데이터모델(PostgreSQL/Firestore/BigQuery), API 계약, 프로젝트 구조 |
| [test-plan.md](./test-plan.md) | TDD 전략, 계층, 픽스처, 실패주입 매트릭스, SPEC↔테스트 매핑, E2E |
| [policy_system_v01.md](./policy_system_v01.md) | 현재 구현의 정책, 실행 검증, 데이터 흐름, 운영 경계 |
| [docs_develop_v01.md](./docs_develop_v01.md) | 코드·설정·서비스·API·DB 스키마·PostgreSQL 이관 fixture를 기준으로 한 개발자 인수인계 문서 |

## SPEC (EARS + 인수조건 + TDD 테스트명세)
| SPEC | 제목 | 시나리오/페이지 |
|------|------|----------------|
| [SPEC-001](./specs/SPEC-001-ip-registration.md) | IP 등록 & 온체인 앵커링 | S1 / Page 1 |
| [SPEC-002](./specs/SPEC-002-x402-interceptor.md) | x402 접근 인터셉터 & 클라이언트 판별 | S1·S2 / 프로토콜 |
| [SPEC-003](./specs/SPEC-003-negotiation.md) | Gemini 자율 가격 협상 | S1 / 협상 |
| [SPEC-004](./specs/SPEC-004-settlement.md) | Solana USDC 정산 & 라이선스·인증서 | S1 / 정산 |
| [SPEC-005](./specs/SPEC-005-library-dashboard.md) | IP 라이브러리 & 온체인 증명서 | Page 1·2 |
| [SPEC-006](./specs/SPEC-006-sandbox.md) | Multi-Agent 협상 샌드박스 | Page 3 |
| [SPEC-007](./specs/SPEC-007-batch-licensing.md) | B2B 초소액 대량 라이선싱 | S2 |
| [SPEC-008](./specs/SPEC-008-royalty-split.md) | 2차 창작 로열티 자동 분배 | S3 |

## 구현 착수 순서 (권장)
1. `SPEC-000`(아키텍처/데이터모델) 기준으로 Django 프로젝트·모델·서비스 인터페이스 스캐폴딩 + 픽스처.
2. SPEC-001 → 002 → 003 → 004 (핵심 S1 플로우, RED→GREEN).
3. SPEC-005 → 006 (UI/시연).
4. SPEC-007 → 008 (S2/S3 확장, 서비스 재사용).
5. E2E-1/2/3 + GCP 배포(Cloud Run) + 파이프라인 활성화.

## 공식 참고 링크 (해커톤 리소스에서 검증)
- AP2: https://ap2-protocol.org/ · https://github.com/google-agentic-commerce/AP2
- a2a-x402: https://github.com/google-agentic-commerce/a2a-x402 (`x402_a2a`)
- x402(Solana): https://solana.com/ko/x402 · https://github.com/xpaysh/x402-agent-kit
- pay.sh: https://pay.sh/docs
- Solana Pay: https://docs.solanapay.com/
- GCP Blockchain RPC: https://cloud.google.com/blockchain-rpc/docs/quickstart
- Eventarc+Workflows: https://cloud.google.com/blog/topics/developers-practitioners/integrating-eventarc-and-workflows
- Firestore: https://cloud.google.com/firestore/docs · BigQuery: https://cloud.google.com/bigquery/docs
- Cloud KMS(EC 서명): https://cloud.google.com/kms/docs/algorithms#elliptic-curve-signing
- ADK: https://goo.gle/agent-dev-kit · Vertex AI: https://cloud.google.com/vertex-ai/docs
