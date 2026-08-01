# VeriProof AI — 문서 인덱스

> **Agentic IP Protocol & Automated Licensing Marketplace**
> 창작자 IP를 대화형으로 등록하고, 외부 AI 에이전트와 HTTP 402(x402/a2a-x402/AP2) 기반으로 협상하여 Solana(USDC)로 즉시 정산하는 에이전트 전용 저작권 라이선싱 프로토콜 및 마켓플레이스. (Google Cloud × Solana 해커톤)

## 라이브 데모 (Cloud Run)

별도의 로컬 설치 없이 아래 배포 환경에서 주요 기능을 바로 확인할 수 있습니다. Cloud Run 인스턴스가 중지된 상태라면 첫 요청은 콜드 스타트로 인해 잠시 지연될 수 있습니다.

| 구분 | 주소 | 확인 내용 |
|---|---|---|
| 구매 에이전트 테스트 (ADK UI) | [Open ADK UI](https://veriproof-adk-ui-512074822993.asia-northeast3.run.app) | Buyer Agent와 대화하며 작품 검색·협상·구매 흐름 테스트 |
| VeriProof App | [Open App](https://veriproof-web-512074822993.asia-northeast3.run.app) | 창작자 작품 등록, 공개 카탈로그 및 라이선스 관리 UI |
| Buyer Agent | [Open Buyer Agent](https://veriproof-buyer-agent-512074822993.asia-northeast3.run.app) | 독립 배포된 Buyer Agent 서비스 엔드포인트 |

라이브 데모의 결제·블록체인 기능은 Solana Devnet 테스트 자산을 사용합니다. Buyer Agent의 대화형 구매 시연은 첫 번째 ADK UI 링크를 사용하십시오.

## 심사위원용 온보딩 (Docker Quick Start)

### 1. 사전 준비

- Docker Desktop 또는 Docker Engine + Compose v2
- Git Bash(Windows) 또는 Bash(macOS/Linux)
- 전체 AI 데모 실행 시 Google Cloud CLI와 Vertex AI 사용 권한

저장소 루트에서 환경 파일을 준비합니다.

```bash
cp .env.example .env
```

`.env`의 PostgreSQL·Redis·Solana Devnet 기본값은 로컬 실행에 바로 사용할 수 있습니다. 실제 지갑 키 없이 안전하게 확인하려면 `BUYER_AUTONOMOUS_PAYMENT_ENABLED=false`를 유지하십시오. `.env`와 지갑·GCP 자격 증명은 커밋하지 않습니다.

Gemini/ADK 기능까지 확인하려면 `.env`에 아래 값을 설정하고, 호스트에서 ADC(Application Default Credentials)를 한 번 생성합니다. `ctl.sh`가 이 파일을 컨테이너에 읽기 전용으로 마운트합니다.

```dotenv
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=<GCP_PROJECT_ID>
GOOGLE_CLOUD_LOCATION=asia-northeast3
VERTEX_ENABLED=true
VERTEX_PROJECT=<GCP_PROJECT_ID>
VERTEX_LOCATION=asia-northeast3
```

```bash
gcloud auth application-default login
```

ADC 또는 프로젝트 권한이 없으면 웹 서버는 구동되지만 실제 Gemini/ADK 요청은 실패합니다.

### 2. Buyer Agent 테스트 지갑 준비

Buyer Agent의 자율 결제를 테스트할 때만 실제 자산이 없는 **테스트 전용 Phantom 계정**을 별도로 만듭니다. Phantom에서 `Settings → Developer Settings → Testnet Mode → Solana Devnet`을 선택한 뒤 다음 순서로 해당 계정의 Solana 개인키를 내보냅니다.

1. `Settings → Manage Accounts`에서 테스트 계정을 선택합니다.
2. `Show Private Key`를 누르고 Phantom 비밀번호를 입력합니다.
3. 네트워크로 `Solana`를 선택하고 표시된 개인키를 복사합니다.

복구 구문(12개 단어)이 아니라 **해당 Solana 계정의 Base58 Private Key**를 사용해야 합니다. 이 값은 Buyer Agent가 거래에 직접 서명할 수 있는 전체 권한이므로 공유하거나 저장소에 커밋하면 안 됩니다. 자세한 내보내기 절차는 [Phantom 공식 안내](https://help.phantom.com/hc/en-us/articles/25334064171795-View-or-export-your-recovery-phrase-or-private-keys-in-Phantom)를 참고하십시오.

루트 `.env`에 다음 값을 설정합니다. `BUYER_MAX_PAYMENT_USDC`에는 심사 과정에서 자동 결제를 허용할 거래당 최대 금액만 작게 지정합니다.

```dotenv
BUYER_AUTONOMOUS_PAYMENT_ENABLED=true
BUYER_MAX_PAYMENT_USDC=1.000000
BUYER_WALLET_SECRET_KEY=<PHANTOM_SOLANA_BASE58_PRIVATE_KEY>
```

Buyer 테스트 지갑에는 네트워크 수수료용 **Devnet SOL**과 결제용 **Devnet USDC**가 모두 필요합니다. USDC는 `.env`의 `USDC_MINT_ADDRESS`와 같은 Devnet 토큰이어야 합니다. [Solana Faucet](https://faucet.solana.com/)과 [Circle Testnet Faucet](https://faucet.circle.com/)에서 테스트 자산을 받은 뒤 Phantom의 공개 주소와 잔액을 확인합니다. Seller 수취 지갑은 Buyer와 분리하고, Seller의 공개 주소만 `DEMO_CREATOR_WALLET`에 설정합니다.

이미 실행 중인 Buyer Agent에 변경한 `.env`를 반영하려면 아래 명령으로 재시작합니다. 환경 변수 변경만으로는 이미지 재빌드가 필요하지 않습니다.

```bash
./ctl.sh buyer-agent reload
```

테스트가 끝나면 `BUYER_AUTONOMOUS_PAYMENT_ENABLED=false`로 되돌립니다. 이 방식은 로컬 Devnet 데모 전용이며, 실제 자산이 있는 지갑이나 운영 환경의 장기 키 관리에는 사용하지 않습니다.

### 3. 데이터 서비스 실행 및 이미지 빌드

```bash
docker compose up -d
./ctl.sh build api
./ctl.sh build buyer-agent
./db.sh migrate
```

`docker compose up -d`는 PostgreSQL과 Redis를 시작하고 `google-solana` Docker 네트워크를 생성합니다. 따라서 API/Agent 실행보다 먼저 수행해야 합니다.

### 4. 애플리케이션 실행

각 `run` 명령은 컨테이너 로그를 계속 표시하므로 별도 터미널에서 실행합니다.

```bash
# Terminal 1: Django Web + Seller Agent(A)
./ctl.sh api run

# Terminal 2: Buyer Agent(B)
./ctl.sh buyer-agent run

# Terminal 3: ADK 개발 UI(선택)
./ctl.sh adk-ui run
```

| 서비스 | 접속 주소 |
|---|---|
| VeriProof Web | http://localhost:8000 |
| Seller Agent Card | http://localhost:8000/.well-known/agent-card.json |
| Buyer Agent | http://localhost:8001 |
| ADK UI | http://localhost:8002 |

코드를 수정한 뒤에는 이미지를 다시 빌드하지 않고 `./ctl.sh api reload` 또는 `./ctl.sh buyer-agent reload`로 반영할 수 있습니다. 의존성·Dockerfile·정적 파일을 변경한 경우에는 해당 이미지를 다시 빌드한 뒤 재시작합니다.

### 5. 종료

```bash
./ctl.sh adk-ui stop
./ctl.sh buyer-agent stop
./ctl.sh api stop
docker compose down
```

포트 충돌이 발생하면 먼저 `8000`, `8001`, `8002`, `5432`, `6379` 포트를 사용하는 기존 프로세스나 컨테이너가 있는지 확인하십시오. `docker compose down`은 PostgreSQL named volume을 보존합니다.

## 목표 아키텍처 (운영 전환 시)
- **App**: Django 5 + Vanilla HTML/CSS/JS, **PostgreSQL(Cloud SQL)** 시스템 오브 레코드
- **컴퓨트**: **Cloud Run** (GKE 미사용)
- **AI**: Gemini `gemini-3.1-flash-lite`(현재 모든 호출 기본 모델), Vertex AI 또는 Gemini API
- **결제**: x402 + **a2a-x402**(`x402_a2a`) + **AP2**(VDC mandate) + **pay.sh** + Solana Pay
- **체인**: Solana Devnet, **Google Cloud Blockchain RPC**, SPL USDC + Memo, **Cloud KMS/Secret Manager** 서명
- **비동기/데이터**: **Pub/Sub + Eventarc + Workflows**, **Firestore**(실시간) + **BigQuery**(감사로그)

> 이 절은 목표 배포 구조다. 현재 로컬 런타임은 Django + SQLite + local storage, 실제 Gemini 호출, 실제 Solana RPC/Memo 제출 어댑터로 동작한다. Memo 서명자 키가 없으면 등록 앵커·등록 인증서·라이선스 인증서 발급은 실패한다. Cloud Run·Cloud SQL·GCP 이벤트 파이프라인은 아직 운영 전환 대상이다.

## 현재 구현 상태와 후속 작업

### 현재 구현·검증 완료

| 영역 | 현재 동작 | 검증 기준 |
|---|---|---|
| 창작자 등록 | 로그인한 사용자가 파일·가격·공개 여부를 입력해 작품을 등록한다. 다중 이미지는 하나의 작품/매니페스트/인증서로 처리하고 SHA-256 기반 Memo를 Solana에 제출한다. | multipart 등록, Gemini 분석, preview 저장, Solana Memo 제출 단위 테스트 |
| Gemini 비서 | 대화·등록 준비·제한된 도구 계획(`record_expense`, `update_asset_terms`, `prepare_registration`)과 감사 기록을 제공한다. | 실제 Gemini 응답과 등록 준비 액션 런타임 확인 |
| 공개 탐색 | 공개·앵커·등록 인증서 조건을 충족한 작품만 catalog/discover에 노출하고 원본은 숨긴다. | catalog HTTP 200, 워터마크 preview 경로 |
| 외부 에이전트 구매 | manifest/OpenAPI → catalog → x402 402 → Gemini 협상 → settle → 만료형 다운로드 토큰 흐름을 제공한다. | 외부 에이전트 역할 HTTP E2E: 402, ACCEPT, settle 200, 다운로드 200 |
| 정합성·회귀 방지 | 등록 API의 가시성 입력을 정규화하여 `PUBLIC`도 `public`으로 보존한다. | 전체 pytest 329 passed, 실제 HTTP 재등록 및 catalog 노출 |
| DB 이관 자료 | 최신 migration과 런타임 검증 데이터가 포함된 portable Django fixture를 제공한다. | `veriproof_current_db_django_fixture_2026-07-25_post_runtime_e2e.json` (308 records) |

### 아직 구현/완료해야 할 작업

| 우선순위 | 작업 | 완료 기준 |
|---|---|---|
| P0 — 실결제 전 필수 | Devnet/운영 지갑으로 실제 Solana 결제 검증, SPL `transfer_checked`, 실수취 ATA/amount/mint/commitment 검증 | Devnet 거래로 등록·정산·인증서·다운로드 E2E 통과 |
| P0 — 보안 | 지갑 서명 기반 로그인과 API/creator 소유권 인증, DEBUG quick login의 운영 차단 | 세션/권한 우회 없이 창작자·구매자 경계가 E2E와 침투 테스트에서 보장 |
| P0 — 키 관리 | signer·RPC·Gemini·webhook secret을 Secret Manager/KMS로 관리하고 시작 시 필수 운영 설정 fail-closed | 키가 로그/DB/응답에 노출되지 않고 누락 설정은 배포를 차단 |
| P1 — 비동기·운영 | 임시 원본 purge scheduler, Pub/Sub→Eventarc→Workflows, sink 재처리·DLQ·관측성 | Cloud Run 환경에서 재시도/실패 알림/복구를 포함한 배포 E2E 통과 |
| P1 — A2A/AP2 상호운용 | 현재의 HTTP wire 계약을 실제 `x402_a2a` transport 및 서명된 AP2 mandate/VC로 확장 | 외부 호환 에이전트와 discovery·mandate·settlement 상호운용 시험 통과 |
| P1 — 계정/비서 관리 | 행동 지침 수정·활성 전환·삭제와 사용자 확인 UX, 등록/결제 명령의 명시적 승인·멱등성 | 권한·감사·취소/실패 경로까지 UI/API 테스트 통과 |
| P2 — 운영 DB | PostgreSQL/Cloud SQL 전환, 백업·복구·migration runbook·부하 시험 | 빈 PostgreSQL 복원과 신규 배포 migration, rollback/복구 절차 검증 |

## 현재 작품 등록 동작

이미지 작품은 여러 장을 하나의 작품으로 등록할 수 있습니다. 첫 이미지는 디스커버 카드의 대표 이미지가 되고, 상세 페이지에서는 워터마크된 전체 이미지가 썸네일 갤러리로 표시됩니다. 작품 전체는 하나의 매니페스트 해시로 앵커링되므로 등록 인증서와 구매 라이선스는 각각 한 건만 발급됩니다. 라이선스 다운로드는 구성 이미지 전체를 하나의 ZIP으로 제공합니다.

DB를 새 환경에 적용할 때는 최신 Django migration(`ip.0016_asset_image`)까지 실행해야 합니다. 작품당 이미지 수는 `MAX_WORK_IMAGES` 환경 변수로 제한하며 기본값은 10입니다.

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

## 후속 구현 순서 (권장)

1. P0 보안·실체인·키 관리의 설계와 Devnet E2E를 먼저 완료한다.
2. P1 비동기 파이프라인과 A2A/AP2 상호운용을 Cloud Run 사전 환경에서 검증한다.
3. 계정/비서 관리 UX와 운영 관측성을 보완한다.
4. PostgreSQL/Cloud SQL 이관·백업/복구·부하 시험을 거쳐 운영 전환한다.

## 최신 DB 덤프와 복원

루트에는 동일한 현재 SQLite DB를 두 형식으로 보관한다.

| 파일 | 형식·용도 | 복원 대상 |
|---|---|---|
| `veriproof_current_db_sqlite_2026-07-25_post_runtime_e2e.sql` | SQLite의 schema, data, index를 포함한 native SQL dump | SQLite 전용 |
| `veriproof_current_db_django_fixture_2026-07-25_post_runtime_e2e.json` | portable Django fixture, 308 records | PostgreSQL 포함 Django 지원 DB |

SQL 덤프는 SQLite 방언이므로 PostgreSQL에 직접 실행하지 않는다.

```bash
# SQLite 복원: 새 파일에 현재 DB의 스키마와 데이터를 복원한다.
sqlite3 restored-veriproof.sqlite < veriproof_current_db_sqlite_2026-07-25_post_runtime_e2e.sql
```

PostgreSQL에는 SQL 파일이 아니라 Django fixture를 사용한다. migration으로 PostgreSQL 스키마를 먼저 만든 뒤 적재한다.

```bash
export DATABASE_URL='postgres://USER:PASSWORD@HOST:5432/veriproof'
cd veriproof
python manage.py migrate --noinput
python manage.py loaddata ../veriproof_current_db_django_fixture_2026-07-25_post_runtime_e2e.json
python manage.py check
```

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
