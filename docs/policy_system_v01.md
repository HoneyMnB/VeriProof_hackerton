# VeriProof AI 정책 시스템 v0.1

> 상태: 로컬 구현 기준 / 2026-07-24
>
> 이 문서는 현재 코드의 실제 동작, 책임 경계, 데이터 흐름 및 운영 정책을
> 기록한다. 계획이나 희망 사항을 구현 완료로 표현하지 않는다.

## 목차

1. [목적과 적용 범위](#1-목적과-적용-범위)
2. [사용자·에이전트·공개 시장 역할](#2-사용자에이전트공개-시장-역할)
3. [제품 표면과 주요 URL](#3-제품-표면과-주요-url)
4. [아키텍처와 모듈 경계](#4-아키텍처와-모듈-경계)
5. [핵심 데이터 모델 및 기록 정책](#5-핵심-데이터-모델-및-기록-정책)
6. [창작자 비서 정책](#6-창작자-비서-정책)
7. [저작물 등록 파이프라인](#7-저작물-등록-파이프라인)
8. [공개 배포·외부 에이전트·A2A 정책](#8-공개-배포외부-에이전트a2a-정책)
9. [협상·결제·정산 정책](#9-협상결제정산-정책)
10. [저작권·자산·보안·데이터 노출 정책](#10-저작권자산보안데이터-노출-정책)
11. [AI·Gemini 정책](#11-aigemini-정책)
12. [운영·로그·오류 정책](#12-운영로그오류-정책)
13. [실행 및 로컬 검증 방법](#13-실행-및-로컬-검증-방법)
14. [주요 코드와 함수 안내](#14-주요-코드와-함수-안내)
15. [구현 완료 범위와 운영 전환 로드맵](#15-구현-완료-범위와-운영-전환-로드맵)

---

## 1. 목적과 적용 범위

VeriProof AI는 창작자가 저작물·개발 상품·문서·미디어를 등록하고, 권리와
라이선스 조건을 관리하며, 외부 에이전트가 공개 승인된 자산을 발견·협상·정산할
수 있게 하는 시스템이다.

핵심 목적은 다음과 같다.

- 창작자의 저작물 등록과 권리 관리 과정을 대화형 비서로 안내한다.
- 등록된 자산의 해시와 증빙을 추적하고, 원본·미리보기·공개 메타데이터를 분리한다.
- 창작자가 명시적으로 공개한 자산만 외부 에이전트 카탈로그에 노출한다.
- x402 기반 접근 조건, 협상, 정산, 라이선스 발급을 모듈로 분리한다.
- 로컬 데모에서는 명시적 결제 목업을 사용하되, 실체인 검증 모듈로 교체 가능하게 한다.

이 문서는 Django 프로젝트 `veriproof/`와 루트의 `start.sh`, `stop.sh`에
적용한다. Docker는 로컬 실행 경로에 사용하지 않는다.

## 2. 사용자·에이전트·공개 시장 역할

| 주체 | 책임 | 사용할 표면 |
|---|---|---|
| 창작자 | 저작물 등록, 공개 여부·가격 설정, 지출 기록, 행동 지침 확인 | `/`, `/workspace`, `/library` |
| 창작자 비서 | 실제 저장된 자산·수입·지출·행동 지침을 근거로 다음 단계를 안내 | `/api/v1/assistant/*` |
| 외부 구매자/에이전트 | 공개 카탈로그 탐색, x402 조건 확인, 협상·정산 요청 | `/discover`, `/.well-known/ai-plugin.json`, `/api/v1/*` |
| 결제 검증기 | 로컬 목업 또는 Solana 거래 검증 | `services.payment_verifier` |
| 운영자 | 자격증명·외부 인프라·실체인 전환 관리 | 환경 변수, 시작 스크립트 |

### 2.1 창작자 비서의 업무 범위

비서는 다음 업무를 대화로 안내한다.

1. 저작물 또는 상품 등록에 필요한 입력을 안내한다.
2. 공개 공유, x402 접근, 협상, 정산, 인증서 발급 흐름을 설명한다.
3. 저장된 자산 수, 공개 자산 수, 앵커 상태, 라이선스 수입, 기록 지출을 확인한다.
4. 창작자가 입력한 행동 지침을 준수하도록 Gemini 컨텍스트에 전달한다.
5. 실제 데이터에 없는 결제·온체인 처리·라이선스 발급을 완료됐다고 말하지 않는다.

명시적인 자연어 명령은 구조화된 허용 도구 계획으로 해석할 수 있다. 현재 도구는
`record_expense`, `update_asset_terms`, `prepare_registration`으로 제한한다.
서버는 모델 계획의 스키마·허용 목록·소유권·입력 불변식을 다시 검사하고, 변경 후
DB를 재조회해 검증한다. 파일이 없는 등록 요청은 등록된 것처럼 처리하지 않고
`awaiting_input(file_upload)`으로 기록한다. 결제 실행은 이 대화 도구 범위에 없으며
명시적 정산 API와 결제 검증기를 통해서만 처리한다.

## 3. 제품 표면과 주요 URL

### 3.1 웹 화면

| URL | 대상 | 기능 |
|---|---|---|
| `/` | 창작자 | ChatGPT 스타일 비서, 대화 이력, 행동 지침, 자산 등록, 수입·지출 요약 |
| `/workspace` | 창작자 | `/`의 호환 별칭 |
| `/discover` | 외부 사용자/에이전트 | 공개·앵커 완료 자산 탐색, 검색, 유형 필터, x402 접근 동선 |
| `/library?creator=<wallet>` | 창작자 | 개인 자산, 보호 미리보기, 인증서, 거래 이력 |
| `/sandbox` | 개발자 | A2A/x402 정산 흐름 점검 화면 |
| `/files/<token>` | 라이선스 보유자 | 만료·권한 검사를 거친 원본 다운로드 |

### 3.2 에이전트 발견 및 API

| URL | 용도 |
|---|---|
| `/.well-known/ai-plugin.json` | 외부 에이전트가 공개 API 계약을 발견하는 매니페스트 |
| `/api/v1/openapi.json` | 호출 가능한 공개 x402 API의 OpenAPI 문서 |
| `/api/v1/catalog` | 공개·앵커 완료 자산의 안전한 카탈로그 |
| `/api/v1/ip/<asset_id>` | 라이선스 보유 여부를 확인하거나 x402 결제 조건을 받는 경로 |
| `/api/v1/ip/<asset_id>/negotiate` | 라이선스 협상 |
| `/api/v1/ip/<asset_id>/settle` | 결제 증빙 정산 |
| `/api/v1/assistant/*` | 창작자 비서의 요약·대화·이력·지침·실행 감사·판매·지출 API |

## 4. 아키텍처와 모듈 경계

### 4.1 계층 원칙

```text
Browser / External agent
        │
        ▼
Django views (HTTP 입력·응답·상태 코드)
        │
        ▼
Application services (유스케이스·정책 실행)
        │
        ├── Domain models (Creator, IpAsset, License, 메시지, 지침)
        └── Adapters (Gemini, Solana, storage, payment verifier, GCP sinks)
```

- `apps/*/views_*.py`: HTTP 파싱, 검증 오류를 HTTP 상태로 변환한다.
- `services/*.py`: 등록·협상·정산·현금흐름·비서 같은 유스케이스를 실행한다.
- `apps/*/models.py`: 영속 데이터와 DB 제약을 보관한다.
- 외부 I/O는 생성자 주입 또는 팩토리를 통해 교체한다. 테스트는 `tests/fakes.py`의
  fake를 사용한다.
- 런타임 경로는 임의의 분석 결과·가격·결제 결과를 생성하지 않는다. 외부 의존성이
  없으면 명확히 실패한다.

### 4.2 앱별 책임

| 모듈 | 책임 |
|---|---|
| `apps.ip` | 창작자·자산·공개 카탈로그·비서 HTTP API·웹 화면 |
| `apps.negotiation` | AI 기반 라이선스 협상 HTTP 경계 |
| `apps.settlement` | 거래 검증, 라이선스 발급, 인증서, 배치 정산, webhook |
| `apps.sandbox` | 로컬 A2A 흐름 점검 |
| `services.registration_service` | 업로드 분석·미리보기·앵커·저장 유스케이스 |
| `services.registration_draft_service` | 대화형 등록 초안, 사용자 확정 토큰, 첨부 해시 검증 |
| `services.creator_assistant_service` | 비서 요약·이력·행동 지침·Gemini 계획·실행 결과 조율 |
| `services.creator_action_service` | 허용 도구 실행, 소유권/입력/DB 사후 검증, 감사 기록 |
| `services.sales_service` | 검증된 License 기반 판매 결과와 0% 수수료 정책 조회 |
| `services.payment_verifier` | 로컬 목업/실제 Solana 검증의 교체 지점 |
| `services.gemini_service` | Gemini API/Vertex AI 호출 및 구조화 응답 파싱 |
| `services.solana_service` | Solana 앵커·거래 검증·인증서·전송 경계 |

## 5. 핵심 데이터 모델 및 기록 정책

### 5.1 주요 모델

| 모델 | 핵심 데이터 | 정책 |
|---|---|---|
| `Creator` | Solana 지갑 주소, 표시명 | 자산·대화·지침·지출·실행 감사의 소유자 |
| `IpAsset` | 해시, 자산 유형, 공개 여부, 가격, 미리보기, 앵커 서명 | 자산의 단일 진실 원천 |
| `AssistantMessage` | 창작자, 역할(user/assistant), 원문, 생성 시각 | 감사 가능한 대화 이력 |
| `AgentDirective` | 창작자, 제목, 지침, 활성 여부, 갱신 시각 | 대화와 분리된 비서 행동 지침 |
| `AssistantAction` | 원 요청, 도구명, 입력/결과, 상태, 검증 시각 | 자연어 도구 실행의 감사·검증 기록 |
| `RegistrationDraft` | 첨부 해시, 등록 필드 초안, 확정 토큰, 상태 | 대화형 등록의 사용자 확인 게이트 |
| `CreatorExpense` | 금액, 메모, 발생 시각 | 창작자가 입력한 실제 지출 |
| `License` | 결제 거래, 구매자, 가격, 사용 목적, 다운로드 토큰 | 검증 후 발급된 라이선스 |
| `NegotiationSession` | 오퍼·라운드·최종 가격·상태 | 협상 이력 |
| `AgentEvent` | 자산/세션 이벤트와 payload | 관측성·감사 이벤트 |

### 5.2 기록의 분리 원칙

1. 대화 원문은 `AssistantMessage`에만 저장한다.
2. 행동 지침은 `AgentDirective`에만 저장한다.
3. 대화 문장이 자동으로 행동 지침으로 승격되지 않는다.
4. 활성(`is_active=true`) 행동 지침만 Gemini 컨텍스트에 전달한다.
5. 수입은 `License.price_usdc`의 검증된 라이선스에서 계산하며 별도 복사하지 않는다.
6. 지출은 `CreatorExpense`에만 기록한다.
7. 실행 요청과 검증 결과는 `AssistantAction`에만 기록한다.
8. 첨부 파일과 등록 의도는 분리한다. `RegistrationDraft`가 confirmed 상태이고 업로드
   해시가 일치할 때만 대화형 UI의 실제 등록을 허용한다.

### 5.3 DB 변경 관리

- 신규 필수 필드나 모델은 Django migration으로 추가한다.
- DB 변경 사항은 `veriproof/db_reference.md`에 누적 기록한다.
- 현재 행동 지침·실행 감사 모델은 `ip.0004_agent_directive`,
  `ip.0005_assistant_action` 마이그레이션이다.

## 6. 창작자 비서 정책

### 6.1 대화 처리 흐름

```text
사용자 메시지
  → 지갑 소유자 확인
  → AssistantMessage(user) 즉시 저장
  → 자산·수입·지출·활성 행동 지침 컨텍스트 구성
  → Gemini 구조화 응답·도구 계획 호출
  → 허용 목록·입력·소유권 검사
  → 변경 실행 후 DB 재조회 검증 → AssistantAction 기록
  → AssistantMessage(assistant) 저장
  → 화면에 응답·검증 상태·판매 결과 표시
```

Gemini 호출이 실패하면 사용자 메시지는 남고, assistant 메시지는 만들지 않는다.
HTTP API는 `503 assistant_unavailable`을 반환한다. 따라서 요청의 존재와 AI 응답의
부재가 감사 이력에서 구분된다.

### 6.2 사용자 행동 지침

창작자는 계정/관리 표면에서 행동 지침을 관리한다. 사이드바에는 상시 노출하지 않는다.
예시:

- 공개 공유 전에 사용자에게 최종 확인을 요청한다.
- 상업 이용 협상에서는 최소 가격을 우선 설명한다.
- 등록 전 원본 보관 기간을 알려 준다.

지침은 사용자 확인용 목록으로 표시되며, 활성 지침만 비서 프롬프트의
`behavior_instructions`에 포함된다. 현재 UI는 추가·조회 기능을 제공한다. 활성 전환·
수정·삭제는 후속 관리 API가 필요하다.

### 6.3 비서가 근거로 사용하는 데이터

- 등록 자산 수, 공개 자산 수, 앵커 완료 자산 수
- 검증된 라이선스 수입, 기록된 지출, 순액
- 등록 → x402 접근 → 협상 → 온체인 정산 파이프라인 상태
- 활성 행동 지침

비서는 확인되지 않은 지갑 잔액, 거래 완료, 권리 소유, 외부 웹 정보는 사실로
말하지 않아야 한다.

### 6.4 자연어 실행 검증 규칙

1. Gemini는 `none`, `record_expense`, `update_asset_terms`,
   `prepare_registration` 외의 도구를 제안할 수 없다.
2. 정보 질문이나 필수값이 빠진 요청은 실행하지 않고 필요한 입력을 답한다.
3. 지출은 양수 금액·메모 검증 후 생성하며, 생성된 행의 소유자·금액·메모를 확인한다.
4. 가격/공개 조건 변경은 해당 창작자 소유 자산만 대상으로 하며, 가격 순서와 공개값을
   검증한 뒤 저장값을 재조회한다.
5. 실패·거부·추가 입력 대기 모두 감사 기록에 남으며, 완료로 표현하지 않는다.

### 6.5 판매 결과와 해커톤 수수료

- 판매 결과는 추정값이 아니라 `settlement.License.price_usdc` 합계만 사용한다.
- 창작자는 판매 건수, 총매출, 플랫폼 수수료, 순수령액을 워크스페이스에서 본다.
- 해커톤 로컬 정책은 `PLATFORM_FEE_BPS=0`이다. 실제 수수료 송금·분배 모듈이 없으므로
  0 이외 값은 안전하게 거부하며, 수수료가 수금됐다고 표시하지 않는다.

## 7. 저작물 등록 파이프라인

### 7.1 입력 정책

등록 경로는 `POST /api/v1/ip/register`이다.

- 유효한 Solana 창작자 지갑이 필요하다.
- 최대 업로드 크기는 `MAX_UPLOAD_BYTES`이며 기본값은 10MB다.
- 자산 유형: image, document, audio, video, software, product, other.
- `min_price`는 0 이상, `target_price`는 최소 가격 이상이어야 한다.
- 공개 여부는 창작자가 명시적으로 `visibility=public`을 선택해야 한다.
- 동일 SHA-256 콘텐츠는 중복 등록할 수 없다.

### 7.2 처리 순서

```text
파일 수신
  → SHA-256 계산 및 중복 확인
  → 이미지: Gemini 분석 + thumbnail/watermark 생성
     비이미지: 사용자 제공 메타데이터 보존
  → Solana Memo 앵커
  → 미리보기 영구 저장 + 원본 임시 저장
  → DB에 Creator/IpAsset/ANCHORED 이벤트를 트랜잭션으로 저장
```

### 7.3 실패 정책

- Gemini 분석 실패: `503 analysis_unavailable`; 임의 태그·가격·카테고리를 만들지 않는다.
- Solana 앵커 실패: `503 anchor_unavailable`; 공개 가능한 완료 자산을 만들지 않는다.
- 저장소 실패: `503 storage_unavailable`; DB 자산을 만들지 않는다.
- 이미지 디코딩 실패: `400 invalid_image`.

앵커가 먼저 성공하고 저장소가 실패하면 외부 체인에 고아 앵커가 남을 수 있다. 이는
실체인 운영 전 보상/재시도 정책으로 보완해야 하는 알려진 리스크다.

## 8. 공개 배포·외부 에이전트·A2A 정책

### 8.1 공개 자산 조건

외부 카탈로그에는 다음을 모두 만족하는 자산만 나타난다.

```text
visibility = public
AND status IN (anchored, listed)
AND registration_certificate_tx_sig IS NOT NULL
```

공개 카탈로그에는 다음만 포함한다.

- asset_id, 제목, 설명, 자산 유형, 창작자 제공 태그/카테고리
- watermark/thumbnail URL, 최소·목표 가격
- 해당 자산의 x402 접근 경로

공개 카탈로그에서는 원본 URL/바이너리, 창작자 지갑, 앵커 거래 서명을 노출하지 않는다.

### 8.2 외부 에이전트 흐름

```text
1. GET /.well-known/ai-plugin.json
2. GET /api/v1/openapi.json
3. GET /api/v1/catalog
4. GET /api/v1/ip/<asset_id> (agent header)
5. 402 결제 조건 확인 → 협상 → 결제 증빙 제출 → 정산
```

라이선스가 없는 에이전트 요청은 x402 `402 Payment Required` 응답을 받는다. 브라우저
요청은 Solana Pay fallback을 받는다. 이미 DB에 유효한 라이선스가 있고 만료되지 않은
다운로드 토큰이 있을 때만 `LICENSED`와 다운로드 경로를 받는다.

### 8.3 공개 홈 UI 정책

`/discover`는 외부 구매자·에이전트용 화면이다. 검색·자산 유형 필터·보호 프리뷰·
최소 가격·실제 x402 접근 링크만 제공하며, 개인용 라이브러리와 창작자 지갑을 섞지 않는다.

## 9. 협상·결제·정산 정책

### 9.1 협상

- `NegotiationEngine`과 `GeminiService.negotiate`가 구조화 JSON 응답을 사용한다.
- 허용 결과는 ACCEPT, COUNTER_OFFER, REJECT다.
- ACCEPT/COUNTER_OFFER 가격은 창작자의 최소 가격보다 낮아질 수 없다.
- Gemini 호출·응답 파싱 실패 시 가격이나 수락 결과를 추정하지 않고 실패한다.
- 최대 협상 라운드는 기본 5회(`MAX_NEGOTIATION_ROUNDS`)다.

### 9.2 결제 수취인 단일 규칙

`services._payment.resolve_pay_to`가 수취인 규칙의 단일 진실 원천이다.

| 자산 | 수취인 |
|---|---|
| 원저작물 | 창작자 지갑 |
| 2차 저작물(`parent_asset` 존재) | 플랫폼 escrow 지갑 |

동일 규칙은 402 응답, 협상 ACCEPT, 정산 검증에 공통으로 사용한다.

### 9.3 로컬 결제 목업

`PAYMENT_VERIFIER=mock`이 기본값이다.

- `LocalMockPaymentVerifier`는 `mock:`으로 시작하는 거래 식별자만 유효한 결제로 인정한다.
- 목업은 실제 Solana 거래를 만들거나 실제 체인 거래처럼 주장하지 않는다.
- 실체인 전환 시 `PAYMENT_VERIFIER=solana`로 설정하고 RPC·USDC mint·서명자·SPL
  transfer adapter를 구성해야 한다.

### 9.4 로컬 Solana 앵커·인증서 목업

`SOLANA_ADAPTER=mock`이 로컬 기본값이다. `LocalMockSolanaService`는 실제 RPC
거래를 제출하지 않고 다음과 같이 명시적으로 구분되는 신호만 생성한다.

- 등록 앵커: `mock:solana:anchor:*`
- 라이선스 인증서: `mock:solana:certificate:*`
- 로열티 전송: `mock:solana:transfer:*`

따라서 로컬 데모는 등록부터 인증서 화면까지 완결된 흐름을 시연할 수 있지만, 이
식별자를 실제 Solana 거래나 결제 완료로 표현하지 않는다. Devnet/운영 전환은
`SOLANA_ADAPTER=real`과 실제 RPC·서명자·SPL 전송 어댑터를 제공해 수행한다.

### 9.5 정산 흐름

```text
결제 검증
  → License idempotent grant
  → certificate Memo 발급 시도
  → Firestore mirror / BigQuery audit
  → 2차 저작물 royalty distribution
  → CERT_ISSUED event
  → 만료형 다운로드 토큰 반환
```

- 거래 서명은 라이선스 idempotency key다.
- 결제 검증 실패 시 라이선스를 발급하지 않는다.
- 인증서 발급 실패는 라이선스를 되돌리지 않으며 인증서 서명만 비어 있는 상태로 남긴다.
- 현재 실제 SPL-USDC 전송 어댑터는 구성돼 있지 않다. 빈 거래를 전송하지 않고 명시적
  실패로 처리한다.

## 10. 저작권·자산·보안·데이터 노출 정책

1. 저작권 대상은 이미지에 한정되지 않는다. document, audio, video, software, product,
   other 유형을 등록할 수 있다.
2. 원본 콘텐츠는 임시 보관하며 기본 보관 기간은 7일이다.
3. 공개 화면과 공개 API는 원본 URL·원본 바이트를 반환하지 않는다.
4. 라이선스가 있는 요청만 저장된 만료형 다운로드 토큰으로 원본을 내려받을 수 있다.
5. 공개 공유는 창작자의 명시적 선택이며, 앵커 완료 전에는 공개 카탈로그에 나타나지 않는다.
6. 지갑 기반 접근은 해커톤 최소 구현이다. 현재 웹 화면의 `wallet`/`creator` query
   parameter는 완전한 지갑 서명 인증을 대체하지 않는다. 운영 전 인증·권한 모델이 필요하다.
7. 사용자 지침과 대화 이력은 해당 creator 관계로 조회한다. API 수준의 강한 인증은
   아직 운영 전제다.

## 11. AI·Gemini 정책

### 11.1 모델과 연결 방식

모든 기본 Gemini 모델 값은 `gemini-3.1-flash-lite`다.

`GeminiService`는 공식 `google-genai` SDK를 지연 생성으로 사용한다.

지원 인증 방식:

```bash
# Gemini Developer API
export GEMINI_API_KEYS='your-key'

# 또는 Vertex AI ADC
export VERTEX_ENABLED=true
export VERTEX_PROJECT='your-project'
export VERTEX_LOCATION='global'
gcloud auth application-default login
```

로컬 실행에서는 저장소 루트 `.env`를 읽되, 파일을 수정하거나 비밀값을 로그·API에
노출하지 않는다. 이미 설정된 운영 환경변수는 `.env` 값으로 덮어쓰지 않는다.
Vertex가 활성화됐지만 지정된 자격증명 파일이 없고 Gemini API 키가 있으면, 실제
Developer API 인증으로 전환한다. `GET /api/v1/assistant/status`는 비밀값을 노출하지
않고 설정 여부와 모델만 반환한다.

### 11.2 실패 폐쇄 원칙

- 자격증명 또는 SDK가 없으면 Gemini client를 만들지 않는다.
- 빈 응답·구조화 JSON 오류·전송 오류는 재시도 후 명시적으로 실패한다.
- 규칙 기반 태그, 가격, 카테고리, 협상 결과로 대체하지 않는다.
- 창작자 비서 응답이 불가능하면 `503 assistant_unavailable`을 반환한다.

## 12. 운영·로그·오류 정책

### 12.1 로그

- 주요 등록·공개 카탈로그·정산·비서 단계에서 구조화 가능한 로그를 남긴다.
- 사용자 관련 오류 로그에는 가능한 경우 `creator_wallet`을 포함한다.
- 비밀값(API 키, private key, 원본 콘텐츠)은 로그에 기록하지 않는다.

### 12.2 HTTP 오류 원칙

| 상태 | 의미 |
|---|---|
| 400/422 | 사용자 입력 또는 형식 오류 |
| 402 | x402 결제 조건이 필요한 에이전트 접근 |
| 404 | 자산·창작자·토큰 없음 |
| 409 | 중복 콘텐츠 또는 상태 충돌 |
| 503 | Gemini, 앵커, 저장소 등 필수 외부 의존성 미가용 |

오류 상황에서 허위 성공 응답이나 임의 데이터 fallback을 사용하지 않는다.

## 13. 실행 및 로컬 검증 방법

### 13.1 가상환경과 시작

기본 Python은 Conda `agent01`이다.

```bash
cd /Volumes/KevinData/Office/00.\ HoneyMnB/05.\ 업무진행/02.\ 내부\ 프로젝트\ 수행/12.\ Hackerton/GoogleSolana
./start.sh
```

기본 주소는 `http://127.0.0.1:55000`이다.

`start.sh`는 다음 순서로 실행한다.

1. 이 프로젝트가 기록한 web PID만 종료한다.
2. Django check와 migration drift check를 실행한다.
3. 최신 migration을 적용하고 migration plan을 표시한다.
4. 추적되지 않은 다른 프로세스가 55000 포트를 사용하면 종료하지 않고 실패한다.
5. Django 서버를 시작한다.

종료:

```bash
./stop.sh
```

`stop.sh`는 PID 재사용을 막기 위해 대상 프로세스의 cwd가 프로젝트 앱 경로와 일치할
때만 종료한다.

### 13.2 권장 검증 명령

```bash
cd veriproof
/opt/anaconda3/envs/agent01/bin/python manage.py check
/opt/anaconda3/envs/agent01/bin/python manage.py makemigrations --check --dry-run
/opt/anaconda3/envs/agent01/bin/python -m ruff check .
/opt/anaconda3/envs/agent01/bin/python -m pytest -q
```

### 13.3 A2A 로컬 점검

1. 창작자로 등록 후 공개 공유를 선택하고 앵커를 완료한다.
2. 외부 클라이언트로 manifest와 OpenAPI를 요청한다.
3. 카탈로그에서 asset_id를 얻는다.
4. 에이전트 Accept/header로 자산 접근을 요청해 402 계약을 확인한다.
5. 로컬 목업 정산은 `mock:<test-id>` 결제 식별자를 사용한다.

## 14. 주요 코드와 함수 안내

| 파일 | 주요 함수/클래스 | 책임 |
|---|---|---|
| `services/registration_service.py` | `RegistrationService.register` | 분석·미리보기·앵커·저장·자산 영속화 |
| `services/creator_assistant_service.py` | `overview`, `ask`, `history`, `directives`, `actions`, `sales` | 창작자 비서 상태·기록·실행 결과 관리 |
| `services/creator_action_service.py` | `execute` | 허용 자연어 도구의 실행·사후 검증·감사 기록 |
| `services/gemini_service.py` | `analyze_asset`, `negotiate`, `quote_batch`, `plan_creator_action` | 실제 Gemini 호출·구조화 계획·응답 검증 |
| `services/payment_verifier.py` | `PaymentVerifier`, `LocalMockPaymentVerifier`, `get_payment_verifier` | 결제 검증 어댑터 선택 |
| `services/_payment.py` | `resolve_pay_to` | 결제 수취인 단일 규칙 |
| `services/license_service.py` | `LicenseService.grant` | idempotent 라이선스 발급 |
| `apps/settlement/services.py` | `SettlementService.settle_pipeline` | 검증→라이선스→인증서→감사 파이프라인 |
| `services/catalog_service.py` | `CatalogService.search`, `serialize` | 공개 안전 메타데이터 projection |
| `apps/ip/views_assistant.py` | `history`, `directives`, `chat` | 비서 HTTP 경계 |
| `apps/ip/views_api.py` | `register`, `get_asset`, `catalog`, `ai_plugin`, `openapi` | 등록·x402·공개 API |
| `templates/workspace.html` / `static/js/workspace.js` | 비서 UI와 브라우저 API 어댑터 | 이력·지침·등록·지출 화면 |
| `templates/discover.html` | 공개 마켓플레이스 | 외부 탐색 UI |

## 15. 구현 완료 범위와 운영 전환 로드맵

### 15.1 현재 구현·검증 완료 정책

| 영역 | 현재 보장하는 정책 | 검증 상태 |
|---|---|---|
| 작품 등록 | 인증된 Django 사용자의 등록 요청만 받고, hash/형식/가격/용량을 검사한다. 다중 이미지는 하나의 작품으로 원자 처리한다. | 실제 Gemini 분석·mock 앵커/등록 인증서·구독 차감 런타임 확인 |
| 공개 여부 | 공개는 명시적 `visibility=public`이고, 대소문자 입력은 정규화한다. 공개 catalog는 공개·앵커·등록 인증서 조건을 함께 적용한다. | `PUBLIC` 실제 HTTP 등록이 `public`으로 저장되고 catalog에 노출됨 확인 |
| 외부 에이전트 | 공개 API는 manifest/OpenAPI/x402 HTTP 계약을 제공하고, 결제 전 원본을 공개하지 않는다. | 외부 에이전트 역할 E2E에서 402·Gemini ACCEPT·정산·다운로드 성공 |
| Gemini | 분석·협상·비서는 실제 구성된 Gemini를 사용하며, 실패 시 생성형 fallback 없이 503으로 종료한다. | 등록 대화와 협상에서 실제 응답 확인 |
| 결제·Solana | 로컬 `mock:` 식별자만 명시적으로 허용하고, mock 결과를 실체인 거래라고 표시하지 않는다. | mock settlement와 라이선스/토큰 발급 확인 |
| 품질 | migration drift와 Django 시스템 오류를 차단하고, 등록 가시성 회귀를 테스트한다. | `pytest -q` 329 passed, `check`, `makemigrations --check` 통과 |

### 15.2 운영 전 P0: 차단 조건

다음 항목은 하나라도 미완료이면 실결제·실체인 운영을 시작하지 않는 차단 조건이다.

1. `SOLANA_ADAPTER=real`, `PAYMENT_VERIFIER=solana`에서 실제 Devnet USDC 거래의 수취자, mint, amount, commitment를 검증하고 SPL `transfer_checked` 경로를 구현한다.
2. KMS/Secret Manager signer, RPC URL, escrow, webhook secret, Django secret/hosts를 운영용으로 구성하고 누락·mock 설정을 startup/deploy 단계에서 fail-closed 한다.
3. DEBUG 개발자 로그인은 운영에서 제거/차단하고, 지갑 서명 기반 인증 및 API의 creator/agent 권한 검증을 도입한다.
4. 실제 Devnet E2E에서 등록→공개→402→협상→실결제→라이선스/인증서→다운로드와 실패·재시도·중복 정산을 검증한다.

### 15.3 운영 전 P1: 신뢰성·상호운용성

1. 원본 purge scheduler와 등록 앵커/저장소 실패의 보상·재시도·감사 절차를 구현한다.
2. Pub/Sub, Eventarc, Workflows, Firestore, BigQuery의 배포 IaC, DLQ, 재처리와 관측성을 완성한다.
3. 현재 자체 HTTP envelope을 실제 `x402_a2a` transport와 서명된 AP2 VC/mandate로 확장하고 외부 호환 agent와 시험한다.
4. PostgreSQL/Cloud SQL 이관, 백업·복원, migration/rollback과 동시성·부하 시험을 완료한다.

### 15.4 후속 제품 기능 P2

1. 행동 지침의 수정·활성 전환·삭제 API/UI를 추가한다.
2. 파일 등록과 결제 설정 같은 위험 행동에는 명시적 사용자 승인, 멱등성 키, 취소/재시도 및 감사 이벤트를 추가한다.
3. 운영자용 결제/라이선스/보상 작업 관찰 화면과 알림을 추가한다.

### 15.5 금지되는 운영 표현

P0이 완료되기 전에는 실결제, 실체인 지급, 서명된 AP2 mandate, 완전한 a2a runtime, 강한 사용자 인증이 구현됐다고 표현하지 않는다. 현재 Google Cloud/Cloud Run 아키텍처 문서는 목표 설계이고, 실제 기본 런타임은 SQLite·local storage·명시적 mock 결제/Solana 어댑터다.
