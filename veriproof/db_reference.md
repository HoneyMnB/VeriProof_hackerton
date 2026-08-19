# VeriProof AI DB 변경 이력

## 2026-08-17 — Agent sponsor 결제의 사용자 계정 비의존화 (migration `ip.0022`)

- 변경: `SponsoredPaymentIntent.buyer_user`를 nullable로 변경했다. Agent sponsor USDC 경로는
  bearer token과 서버 설정의 Buyer 공개키로 식별하며, Django `User`를 조회하거나 intent에
  연결하지 않는다. 브라우저 sponsor 결제는 계속 로그인한 `request.user`를 intent에 저장한다.
- 영향 범위: Agent intent와 그 정산에서 `buyer_user=None`이 전달되며, 해당 Agent 라이선스는
  계정 소유권 FK 없이 Buyer 지갑과 결제 거래 서명으로 식별된다. DB 필수 FK를 유지한 채
  조회만 제거하는 경우 intent 저장이 실패하므로 nullable 변경이 필요하다.
- 검증: Agent intent 통합 테스트가 `User` 레코드 없이 201을 반환하고 `buyer_user is None`으로
  저장되는지 확인한다.
- Alembic 필요 여부: 불필요. Django migration
  `apps/ip/migrations/0022_sponsoredpaymentintent_buyer_user_nullable.py`를 적용한다.

## 2026-07-28 — 지갑 개인키 암호화 저장 (migration `accounts.0005`)

- `WalletConfiguration.private_address` (nullable varchar 512): Solana 공개 주소와 일치함을
  서버에서 검증한 개인키의 Fernet 암호문이다. 기존 공개 주소 레코드는 키를 보유하지 않을 수
  있으므로 nullable/blank로 추가했다. 평문 개인키는 DB, 지갑 목록 API, 로그에 저장하거나
  반환하지 않는다.
- 영향 범위: `/accounts/wallets/save/`는 주소와 일치하는 base58 또는 64-byte JSON
  keypair만 수신해 암호화한다. 지갑 삭제는 계정 소유 지갑만 삭제하고, 활성 지갑이면 남은
  최신 지갑을 활성화해 `UserPreference.creator_wallet`을 동기화한다. `ip.Creator`는 작품과
  등록 기록의 식별자이므로 삭제하지 않는다.
- 운영 설정: `DEBUG=false` 환경에서는 Secret Manager가 제공하는
  `WALLET_PRIVATE_KEY_ENCRYPTION_KEY` Fernet 키가 필수다. 키를 잃으면 기존 암호문을
  복호화할 수 없다.
- 검증: 암호문 저장·목록 API 비노출·활성 지갑 삭제 시 승계·Creator 보존 통합 테스트를
  실행한다.
- Alembic 필요 여부: 불필요. Django migration
  `apps/accounts/migrations/0005_walletconfiguration_private_address.py`를 적용한다.

## 2026-07-28 — 단일 워크스페이스 지갑 UX

- 변경: 새 지갑 등록과 수정 API는 계정의 첫 지갑 레코드만 생성·갱신한다. 입금/판매대금
  수령 구분은 API와 UI에서 제거하고, 활성 지갑을 유일한 워크스페이스 지갑으로 사용한다.
  기존 복수 레코드는 운영 데이터를 임의로 삭제하지 않고 개별 수정·삭제만 허용한다.
- 영향 범위: 지갑이 있으면 등록 폼은 숨기고 수정 시에만 열며, 개인키를 비워 둔 수정은
  기존 암호문을 유지한다. 공개 주소 변경에는 해당 주소의 개인키가 필수다.
- 검증: 단일 지갑 수정 시 암호문 보존, 마지막 지갑 삭제 시 활성 포인터 초기화와 Creator
  보존 통합 테스트를 실행한다.
- Alembic 필요 여부: 불필요. 기존 필드의 의미와 런타임 요청 경로만 변경한다.

## 2026-07-28 — 로그인 지갑 기반 등록 앵커 서명

- 변경: `/api/v1/ip/register`는 요청 본문의 `creator_wallet`을 등록 권위값으로 사용하지
  않는다. 인증된 계정의 활성 `WalletConfiguration.address`를 작품의 공개 창작자 주소와
  Solana Memo 앵커·등록 인증서의 공개키로 사용하며, 같은 레코드의 복호화·검증된 개인키를
  해당 두 트랜잭션의 서명자로 전달한다.
- 영향 범위: 활성 지갑·개인키가 없거나 암호문이 손상됐거나 공개키와 일치하지 않으면 등록은
  422로 중단한다. `PLATFORM_ESCROW_SECRET_KEY`로 대체 서명하지 않는다. 플랫폼 에스크로
  정산 경로는 변경하지 않는다.
- 검증: 요청 본문의 다른 주소를 무시하고 로그인 지갑 주소·서명 바이트가 fake Solana
  어댑터에 전달되는 통합 테스트, 활성 지갑 없는 계정의 조기 거부 테스트를 실행한다.
- Alembic 필요 여부: 불필요. 기존 암호화 개인키 필드의 등록 런타임 사용 경로만 변경한다.

## 2026-07-28 — 활성 지갑 기준 등록 초안 일관성

- 변경: 등록 캔버스는 활성 `WalletConfiguration`의 공개 주소·개인키 등록 상태를 조회해
  초안 저장, 초안 확정, 최종 등록에 같은 공개 주소를 사용한다. 초안 API는 요청 본문의
  `creator_wallet`을 신뢰하지 않고 인증된 계정의 활성 지갑 주소로 강제한다.
- 영향 범위: 기존 `UserPreference.creator_wallet`가 비어 있거나 오래된 경우에도 등록
  캔버스는 정상 동작한다. 활성 지갑이 없거나 개인키가 없으면 캔버스를 열지 않고 설정을
  안내하며, 초안 저장 오류는 일반 파일 준비 오류로 감추지 않고 서버 상세 메시지를 표시한다.
- 검증: 활성 지갑과 다른 요청 주소로 초안을 저장해도 활성 주소 소유 초안이 생성되는 통합
  테스트와 등록·초안·지갑 통합 테스트를 실행한다.
- Alembic 필요 여부: 불필요. 기존 지갑 데이터의 런타임 조회·권한 경로만 변경한다.

## 2026-07-24 — 계정 단위 라이브러리 소유권 (migration `ip.0015`)

- `IpAsset.account_owner` (nullable FK to Django user, `SET_NULL`): 작품을 등록·관리하는
  로그인 계정이다. `creator`의 Solana 지갑과 역할을 분리해 한 계정이 여러 지갑으로
  등록한 모든 작품을 하나의 비공개 라이브러리에서 조회할 수 있다.
- 영향 범위: `/library`와 작품 조건 수정은 URL·요청 본문의 지갑값이 아니라 현재
  로그인 계정의 `account_owner`만 사용한다. 카드에는 각 작품의 `creator_wallet`을
  계속 표시한다. 신규 등록은 인증된 계정을 필수로 하며 해당 계정을 소유자로 저장한다.
- 운영 데이터 보완: `seed_demo_catalog` 재실행은 기존 데모 작품을 로컬 관리자 계정에
  연결한다. 현재 로컬 DB에서 관리자 소유 작품 15건으로 확인했다.
- 검증: Django migration 적용, 계정/URL 지갑 격리·익명 라이브러리 거부·등록 경로
  통합 테스트를 실행했다.
- Alembic 필요 여부: 불필요. Django migration
  `apps/ip/migrations/0015_ipasset_account_owner.py`를 적용한다.

## 2026-07-24 — 다중 지갑 수령 구성 (migration `accounts.0003`)

- `WalletConfiguration`: 계정이 보관하는 공개 Solana 주소, 표시 이름, 입금 수령 여부,
  판매대금 수령 여부, 현재 워크스페이스 활성 주소를 기록한다. 시드 구문·개인키·서명·지갑
  공급자 세션은 저장하지 않는다.
- 영향 범위: 활성 주소만 기존 `UserPreference.creator_wallet`에 동기화되어 작품 등록,
  라이브러리, 판매 집계의 기존 소유자 경로를 유지한다. 판매대금 수령 주소는 계정당 하나로
  제한하며, 입금 주소는 복수 등록할 수 있다.
- 검증: 로그인 사용자만 자신의 주소를 등록·활성화할 수 있으며, 주소 형식과 중복을 서버에서
  검증한다. 실제 외부 지갑 연결은 본 변경 범위 밖이며 연결 직전의 설정만 제공한다.
- Alembic 필요 여부: 불필요. Django migration `apps/accounts/migrations/0003_walletconfiguration.py`를 적용한다.

## 2026-07-24 — 계정 복구 연락처 (migration `accounts.0002`)

- `UserPreference.recovery_email` (nullable email): 로그인 이메일과 분리된 계정 복구
  연락처다. 형식을 서버에서 검증하며, 로그인 식별자나 비밀번호를 대체하지 않는다.
- `UserPreference.contact_phone` (nullable varchar 30): 사용자가 자발적으로 저장하는
  지원 연락처다. 판매자 정산·온체인 지갑에는 사용하지 않는다.
- 영향 범위: 인증된 계정의 설정 API만 두 값을 수정한다. 비밀번호는 DB 필드 추가 없이
  Django의 기존 password hash를 현재 비밀번호 검증 후 갱신하며 세션을 유지한다.
- 검증: 설정 저장, 복구 이메일 형식 거부, 비밀번호 현재값·확인값 검증 통합 테스트를 실행한다.
- Alembic 필요 여부: 불필요. Django migration `apps/accounts/migrations/0002_userpreference_recovery_contact.py`를 적용한다.

## 2026-07-24 — 대화 사용자 제목 (migration `ip.0013`)

- `AssistantMessage.conversation_title` (nullable, varchar 120): 대화의 첫 사용자
  메시지에만 저장하는 사용자가 지정한 표시 제목이다. 원문 `content`는 변경하지 않으며,
  기존 대화는 첫 사용자 메시지의 첫 줄을 제목으로 계속 사용한다.
- 영향 범위: 로그인 계정의 설정 지갑과 일치하는 창작자만 대화 제목을 변경하거나 대화
  메시지를 삭제할 수 있다. 삭제는 해당 `conversation_id`의 메시지에만 적용하며, 별도
  감사 기록은 기존 FK 정책에 따라 보존된다.
- 검증: 제목 변경·삭제 소유권 및 기존 대화 제목 폴백 통합 테스트를 실행한다.
- Alembic 필요 여부: 불필요. 이 프로젝트의 Django migration `apps/ip/migrations/0013_assistant_message_conversation_title.py`를 적용한다.

## 2026-07-24 — 등록 인증서와 공개 게시 경계 (migration `ip.0010`)

- `IpAsset.registration_certificate_tx_sig` (nullable, indexed, varchar 90):
  창작물 등록 후 발급되는 온체인 등록 인증서 식별자다. 구매자에게 발급되는
  `settlement.License.certificate_tx_sig`와 별개의 기록이다.
- 공개 게시 조건: `visibility=public`, 앵커 완료 상태, 그리고 이 등록 인증서가
  모두 존재해야 공개 카탈로그·상세·에이전트 결제 조건에 나타난다.
- 기존 로컬 데모는 `seed_demo_catalog`이 인증서 누락분을 보완한다. 실체인 전환 시
  동일 어댑터 메서드가 실제 Memo 거래를 반환해야 한다.

## 2026-07-24 — 대화 첨부 분석 (migration `ip.0009`)

- `ConversationAttachment.creator_id` (FK): 파일 소유 창작자. 다른 창작자 대화에
  재사용할 수 없다.
- `source_message_id` (nullable FK): 실제 사용자 메시지에 연결된 첨부 감사 관계.
- `content_sha256`, `perceptual_hash`, `analysis`: 업로드 원본 지문과 Gemini가 반환한
  분석 결과이다. 분석 실패 시 레코드를 만들지 않는다.
- `temporary_url`, `expires_at`: 등록 전 대화 첨부의 제한 보관 위치와 만료 시각이다.
  공개 API·브라우저에는 원본 URL을 반환하지 않는다.

## 2026-07-24 — 이미지 유사 검색 지문 (migration `ip.0008`)

- `IpAsset.perceptual_hash` (nullable, indexed, varchar 16): 이미지 등록 파이프라인이
  생성하는 64-bit 명암 지문이다.
- 검색 후보 선별 전용이며, 동일 원본 판정과 Solana 앵커의 권위 값은 계속
  `image_sha256`이다.
- 비이미지 자산에는 값을 만들지 않는다.

## 2026-07-24 — `ip.AssistantAction` (migration `ip.0005`)

- `creator_id` (FK to `ip.Creator`, cascade): 실행을 요청한 창작자.
- `source_message_id` (nullable FK to `ip.AssistantMessage`, set null): 자연어 요청의
  감사 연결. 원 대화 삭제 시 실행 기록은 보존한다.
- `action_name`, `request_payload`, `result_payload`: 허용 도구명과 서버가 검증한
  입력·결과. 비밀값이나 결제 자격증명은 저장하지 않는다.
- `status`: `completed`, `awaiting_input`, `rejected`, `failed` 중 하나.
- `verification_passed`, `verified_at`: DB 재조회 기반 사후 검증 결과와 시각.
- Index: `(creator_id, created_at)` for creator action audit reads.

Natural-language requests do not directly mutate business data. This record is
created before tool execution so rejected and failed attempts remain auditable.

## 2026-07-24 — `ip.AgentDirective` (migration `ip.0004`)

- `creator_id` (FK to `ip.Creator`, cascade): 행동 지침 소유자.
- `title` (varchar 120), `instruction` (text 2000): 창작자가 확인·수정하는 비서 행동 지침.
- `is_active`: 활성 지침만 Gemini 창작자 비서 컨텍스트에 전달한다.
- `created_at`, `updated_at`: 감사 및 최신 지침 정렬 기준.
- Index: `(creator_id, is_active, updated_at)` for workspace directive reads.

Conversation text remains in `ip.AssistantMessage`; directives are deliberately
separate so a chat transcript cannot silently become an agent instruction.

## 2026-07-24 — 공개 발견·대화형 창작자 워크스페이스

- 변경: `IpAsset`에 `asset_type`, `visibility`, `content_mime_type`, `description`을 추가하고, 이미지 전용 미리보기·독창성 필드를 비이미지 저작물에도 안전하도록 nullable로 변경했다.
- 변경: `AssistantMessage`를 추가해 창작자와 Gemini 기반 저작권 비서 간 대화를 감사 가능하게 저장한다.
- 영향: 공개 자산은 명시적으로 `visibility=public`이며 앵커링 완료 상태일 때만 외부 발견 API에 나타난다. 원본 URL/바이너리는 어떤 공개 응답에도 포함하지 않는다.
- 검증: Django migration drift 검사, 공개/비공개 카탈로그 통합 테스트, 전체 pytest 실행.
- 마이그레이션: Django migration `apps/ip/migrations/0002_public_catalog_and_assistant.py` 필요. Alembic은 이 Django 프로젝트에 적용하지 않는다.
# Database change log

## 2026-07-24 — 작품 다중 이미지 갤러리 (migration `ip.0016`)

- `AssetImage.asset_id` (FK to `IpAsset`, cascade): 추가 이미지는 독립 작품이 아니라 부모
  작품의 구성 이미지다. 따라서 등록 인증서·결제·라이선스는 계속 부모 `IpAsset` 하나에만
  연결된다.
- `position`, `file_name`, `content_mime_type`, `content_sha256`: 작품 내 이미지 순서와
  검증 가능한 원본 지문을 보존한다. `IpAsset.image_sha256`는 첫 이미지와 추가 이미지의
  순서 있는 SHA-256 목록을 다시 해시한 작품 매니페스트 값으로 앵커링된다.
- `watermark_url`, `original_url`: 보호 미리보기와 만료 전 원본 보관 위치다. 공개 응답은
  애플리케이션 권한 경계의 워터마크 preview URL만 제공하며, 원본 위치는 제공하지 않는다.
- 영향 범위: 공개 상세는 모든 보호 미리보기를 썸네일 갤러리로 노출하고, 하나의 라이선스
  다운로드 토큰은 작품의 모든 구성 이미지를 전달한다. `MAX_WORK_IMAGES` 운영 설정(기본 10)은
  한 작품에 허용하는 전체 이미지 수를 제한한다.
- 검증: 다중 이미지 등록, 단일 앵커/등록 인증서, 공개 상세 갤러리, 라이선스 다운로드 및
  기존 단일 이미지 회귀 테스트를 실행한다.
- Alembic 필요 여부: 불필요. 이 Django 프로젝트는 `apps/ip/migrations/0016_asset_image.py`를 적용한다.

## 2026-07-24 — 대화형 등록 초안 (migration `ip.0007`)

- `RegistrationDraft.creator_id` (FK to `ip.Creator`, cascade): 초안 소유 창작자.
- `file_name`, `file_sha256`: 등록 확정 전 첨부 파일의 이름과 SHA-256. 실제 업로드 시
  서버가 다시 계산한 해시와 일치해야 한다.
- `fields` (JSON): 사용자/에이전트가 수집한 유형·제목·설명·가격·공개 범위 초안.
- `status`, `confirmation_token`, `confirmed_at`: collecting → confirmed → executed 상태와
  사용자 최종 확인을 보장한다. 확정 토큰 없이 등록 유스케이스는 초안을 소비하지 않는다.
- `executed_asset_id` (one-to-one, protect): 성공한 실제 자산과 초안을 연결한다.
- 영향: 새 대화형 UI는 등록 전에 초안을 저장하고 확인하지만, 기존 외부/레거시 등록 API
  계약은 호환성을 위해 유지한다.
- 검증: Django migration 적용, 초안 생성·확정·해시 불일치 거부·등록 성공 흐름을 테스트한다.
- 마이그레이션: Django migration `apps/ip/migrations/0007_registrationdraft.py` 필요.
  Alembic은 이 Django 프로젝트에 적용하지 않는다.

## 2026-07-24 — 창작자 등록 구독 (migration `ip.0006`)

- `SubscriptionPlan`: 활성화 가능한 등록 구독 플랜의 코드, 월 USDC 금액, 포함 등록 횟수.
- `CreatorSubscription`: 창작자별 결제 식별자, 유효 기간, 사용한 등록 횟수. 플랜 삭제는
  보호하고 창작자 삭제 시에만 함께 삭제한다.
- `RegistrationCharge`: 등록 완료 자산과 사용한 구독 크레딧을 1:1로 연결해 동일 자산의
  중복 차감을 막는다.
- 영향: 등록 유스케이스는 활성 구독의 잔여 횟수를 트랜잭션으로 확인·차감한다.
- 검증: migration 적용 및 구독 없음/활성화/한도 소진 등록 흐름을 런타임 확인했다.
- 마이그레이션: Django migration `apps/ip/migrations/0006_subscription_billing.py` 필요.
  Alembic은 이 Django 프로젝트에 적용하지 않는다.

## 2026-07-24 — 사용자 계정 환경설정 (migration `accounts.0001`)

- `accounts.UserPreference.user` (1:1 FK to Django user, cascade): 로그인 계정의 소유자.
- `display_name`, `language`, `creator_wallet`: 좌측 하단 계정 모달에서 수정하는 표시명,
  언어, 창작자 지갑이다. 지갑 서명·비밀키는 저장하지 않는다.
- 영향: 로그인 계정은 지갑 선택을 브라우저 로컬 저장소가 아닌 계정 설정에서 복원한다.
  기존 해커톤 데모/외부 검증용 비로그인 워크스페이스 경로는 유지한다.
- 검증: Django migration 적용, 세션 로그인과 CSRF 보호 설정 저장 흐름을 확인한다.
- 마이그레이션: Django migration `apps/accounts/migrations/0001_initial.py` 필요.
  Alembic은 이 Django 프로젝트에 적용하지 않는다.

## 2026-07-24 — `ip.CreatorExpense` (migration `ip.0003`)

- `creator_id` (FK to `ip.Creator`, cascade): expense owner.
- `amount_usdc` (decimal 12,6): positive creator-entered operating expense.
- `memo` (varchar 200): required expense purpose.
- `occurred_at`, `created_at`: event and record timestamps.
- Index: `(creator_id, occurred_at)` for creator workspace cashflow queries.

Income is not copied into this table: it is calculated from verified
`settlement.License.price_usdc` records to avoid a second source of truth.
# 2026-07-24 — `ip_asset_component`

`AssetComponent` stores the private source-file manifest for a packaged work. Required fields are the parent asset, original file name, MIME type, SHA-256, and internal storage URL. Components are never exposed through public catalog or preview responses; the primary asset hash remains the on-chain registration anchor.

## 2026-07-25 — 계정 지갑의 창작자 식별자 준비 (migration `accounts.0004`)

- 변경: 기존 `accounts.WalletConfiguration.address`와
  `accounts.UserPreference.creator_wallet` 중 유효한 Solana 공개 주소에 대해
  `ip.Creator.wallet_address` row를 보정 생성한다.
- 이유: 대화형 등록 초안, 어시스턴트 이력, 첨부 분석은 `creator_wallet`을
  `ip.Creator`로 해석한다. 계정 지갑 저장만 완료되고 `Creator`가 없으면
  `/api/v1/assistant/registration-drafts`가 404를 반환했다.
- 영향 범위: 스키마는 변경하지 않는다. 이미 저장된 공개 지갑을 등록 초안과
  창작자 워크스페이스의 런타임 식별자로 사용할 수 있게 하는 데이터 보정이다.
- 검증: 지갑 저장 후 `Creator` 생성 및 등록 초안 생성 통합 테스트를 추가했다.
  로컬 호스트 Python에는 Django가 없어 pytest 실행은 수행하지 못했고, 변경 파일
  문법 검사는 통과했다.
- Alembic 필요 여부: 불필요. 이 Django 프로젝트는
  `apps/accounts/migrations/0004_prepare_creator_wallets.py`를 적용한다.

## 2026-07-26 — 자율 결제 데모 수취 지갑 보정

- 변경: `seed_demo_catalog`가 `DEMO_CREATOR_WALLET`의 제어 가능한 Solana
  Devnet 공개키를 필수로 사용한다. 기존 데모 이미지 해시와 일치하지만 다른
  창작자에 연결된 자산은 현재 데모 창작자로 재연결하고 목업 등록 인증서를
  현재 공개키 기준으로 다시 발급한다.
- 이유: 과거 시스템 프로그램 주소(`111...`)는 개인키가 있는 판매자 지갑이
  아니므로 실제 x402 USDC 수취 및 최종 정산 테스트에 사용할 수 없었다.
- 영향 범위: 명시적으로 `seed_demo_catalog`를 실행한 로컬 데모 자산에만
  적용한다. 일반 창작자 자산, 라이선스 및 결제 내역은 변경하지 않는다.
- 검증: Django system check와 migration 불변 검사는 통과했고 Buyer 자율 결제
  정책 계약을 실행 검증했다. 실제 `seed_demo_catalog` 재실행은 기존 로컬 DB를
  변경하므로 이번 작업에서는 수행하지 않았다.
- Alembic 필요 여부: 불필요. DB 스키마 변경 없이 로컬 데모 운영 데이터만
  보정한다.

## 2026-07-27 — 다운로드 권한 7일 만료 정책

- 변경: 결제 후 발급되는 `settlement.License.download_expires_at`의 기본 TTL을
  604800초(7일)로 조정하고, 라이선스 판정에서 만료된 row를 활성 권한으로
  인정하지 않는다.
- 이유: 결제 장부(`License` row, `payment_tx_sig`, `buyer_wallet`, 금액,
  발급 시각)는 보존하되, 원본 다운로드 권한은 결제 후 7일까지만 제공해야 한다.
- 영향 범위: 기존 결제 row는 삭제하지 않는다. 7일이 지난 row는 감사/정산
  장부로 남지만 `/files/<token>`과 `X-Solana-Tx-Sig` 라이선스 확인 경로에서
  다운로드 권한을 부여하지 않는다. 재구매 시 새 온체인 결제 tx로 새
  `License` row가 추가된다.
- 검증: 라이선스 서비스 단위 테스트와 asset 접근 통합 테스트로 만료 row 보존 및
  재결제 요구를 확인한다.
- Alembic 필요 여부: 불필요. 기존 `download_expires_at` 필드를 사용하는 정책
  변경이며 DB 스키마 변경은 없다.

## 2026-07-27 — 브라우저 구매자 계정 귀속 (migration `settlement.0002`)

- 변경: `settlement.License.buyer_user` nullable FK를 추가한다. 브라우저 Solana
  Pay 구매는 로그인한 계정을 이 필드에 기록하고, `buyer_wallet`은 검증된 온체인
  결제 지갑으로 계속 보존한다.
- 이유: 작품(`ip.IpAsset`)은 여러 사용자에게 판매될 수 있고, 각 결제는 별도
  `License` 행이어야 한다. 다운로드 URL만 아는 제3자가 원본을 받지 못하도록
  브라우저 라이선스의 권한 주체를 계정과 연결한다.
- 영향 범위: 기존 License와 에이전트/API 구매는 `buyer_user=NULL`로 유지되어
  기존 지갑·토큰 전달 계약을 보존한다. 새 브라우저 구매는 해당 계정으로 로그인한
  경우에만 `/files/<token>` 다운로드를 허용한다. 계정 삭제 시 법적·정산 원장은
  남기고 FK만 NULL로 만든다.
- 검증: A/B 계정 간 다운로드 차단, 다른 브라우저에서 동일 구매 계정의 권한 복구,
  결제 reference 세션 귀속, 익명 결제 확인 거부를 통합 테스트로 검증한다.
- Alembic 필요 여부: 필요. Django migration
  `apps/settlement/migrations/0002_license_buyer_user.py`를 적용한다.

## 2026-07-27 — Buyer Agent Devnet SOL 가격·결제 원장

- 변경: `ip.IpAsset.target_price_sol`(nullable decimal 16,9)을 추가했다.
  판매자가 자산별 Devnet native SOL 가격을 직접 설정할 때만 buyer agent SOL
  결제가 가능하다. 기존 `min_price_usdc`와 `target_price_usdc`는 변경하지
  않으며 환산값이나 기본값으로 사용하지 않는다.
- 변경: `settlement.License.price_sol`과 `payment_currency`를 추가하고,
  `price_usdc`를 nullable로 전환했다. SOL 결제는 `price_sol`과 `SOL`로,
  기존 USDC 결제는 기존 `price_usdc`와 기본값 `USDC`로 원장을 보존한다.
- 이유: 다른 통화의 값을 한 컬럼에 기록하면 정산·감사 데이터가 오염된다.
  SOL 가격 미설정 자산은 결제 조건을 제공하지 않아 임의 환율 또는 거짓
  fallback이 발생하지 않는다.
- 영향 범위: 새 agent SOL 결제만 native SOL 전송의 수취인·lamports·memo를
  Devnet에서 검증 후 라이선스를 발급한다. 기존 x402 USDC 결제 데이터와
  실행 경로는 유지된다.
- 검증: migration check와 buyer payment 정책·SOL 결제 API 테스트를 실행한다.
- Alembic 필요 여부: 필요. Django migration
  `ip.0017_ipasset_target_price_sol` 및
  `settlement.0003_license_sol_payment`를 적용한다.

## 2026-07-27 — 등록 캔버스 SOL 가격 저장 (migration `ip.0018`)

- 변경: `ip.IpAsset.min_price_sol`을 추가하고, 기존 USDC 가격 컬럼을 nullable로
  변경했다. `#registration-canvas`의 `min_price`와 `target_price` 입력은 이제
  각각 native Devnet SOL 최소·목표 가격으로 저장된다.
- 이유: 화면에 SOL로 표시한 값을 USDC 컬럼에 기록하면 결제 통화와 원장이
  불일치한다. 신규 등록 자산에는 USDC 가격을 만들거나 환산하지 않는다.
- 영향 범위: 기존 USDC 가격·x402 결제 자산은 값을 그대로 유지한다. 신규 SOL
  자산은 buyer agent SOL 결제 경로에서만 판매 조건을 제공한다.
- 검증: migration drift, Django check, buyer agent SOL 정책 테스트를 실행한다.
- Alembic 필요 여부: 필요. Django migration
  `apps/ip/migrations/0018_registration_prices_are_sol.py`를 적용한다.
## 2026-08-01 — A2A native SOL negotiation prices (migration `negotiation.0002`)

- Added nullable `NegotiationSession.initial_offer_sol` and `final_price_sol`
  as decimal(16,9), matching native SOL/lamport precision.
- Made legacy `initial_offer_usdc` nullable so new SOL-only sessions do not
  persist a false USDC amount. Existing USDC session values are preserved.
- Accepted A2A SOL negotiations now carry `final_price_sol` through payment
  terms and on-chain verification; `IpAsset.min_price_sol` and
  `target_price_sol` are the price-floor source of truth.
- Impact: deploy must apply Django migration
  `apps/negotiation/migrations/0002_sol_negotiation_prices.py`. Alembic is not
  used by this Django project.
## 2026-08-11 — Secret Manager 지갑 암호화 키 교체

- `WALLET_PRIVATE_KEY_ENCRYPTION_KEY`를 소스 코드의 고정 값으로 관리하지
  않도록 변경했다. 운영 환경은 Cloud Run을 통해 숫자형 버전이 고정된 Secret
  Manager 값을 전달받으며, 설정이 누락되면 애플리케이션 시작을 차단한다.
- 저장소에 포함됐던 기존 고정 암호화 키는 유출된 것으로 간주해야 한다. 해당 키로
  암호화한 `accounts.WalletConfiguration.private_address` ciphertext는 새 Fernet
  키로 복호화할 수 없다.
- 새 보안 비밀 버전을 배포한 후 운영자는 모든 활성 창작자 지갑 개인키를 다시
  입력하고 공개 주소와의 일치 여부를 검증해야 한다. 기존 ciphertext를 키 교체가
  완료된 값인 것처럼 그대로 유지하면 안 된다.
- 영향 범위: 활성 지갑이 새 키로 재암호화되기 전까지 해당 계정의 창작물 등록
  서명은 사용할 수 없다. 공개 지갑 주소와 기존 온체인 트랜잭션 서명은 변경되지
  않는다.
- 검증 결과: 운영 설정에서 Django secret 또는 wallet encryption key가 누락되면
  시작이 거부되는 것을 확인했다. KMS, Solana 트랜잭션 및 Buyer Agent 관련 집중
  테스트를 통과했다. 실제 Secret Manager/KMS/Devnet 검증은 보안 비밀, IAM 및
  잔액이 있는 KMS 공개 주소가 필요한 배포 단계의 검증 항목이다.
- Alembic/Django migration 필요 여부: 필요하지 않다. DB 스키마 변경은 없다.
## 2026-08-13 — Django-native Passkey credentials (migration `accounts.0006`)

- 변경: `accounts.PasskeyCredential`를 추가하여 한 로그인 계정에 여러 WebAuthn
  공개 자격증명을 연결한다. `credential_id`, 공개키, 불투명 user handle, 서명 카운터,
  전송 방식, 백업·기기 정보 및 사용 시각만 저장한다.
- 이유: 기존 이메일/비밀번호 복구 경로를 유지하면서 표준 WebAuthn 기반의 비밀번호 없는
  로그인을 제공하기 위함이다. 생체정보와 Passkey 개인키는 브라우저·OS 인증장치 밖으로
  나오지 않으며 DB에 저장하지 않는다.
- 영향 범위: `/accounts/passkeys/*` 등록·로그인 경로와 Django 세션 인증에 영향을 준다.
  Solana 지갑 및 온체인 서명 키와는 독립된 인증 자격증명이다.
- 검증: `accounts.0006_passkeycredential` Django migration과 인증 통합 테스트로 검증한다.
  앱 DB 스키마 변경이므로 Django migration이 필요하며 Alembic 대상은 아니다.

## 2026-08-14 — Live flow event correlation (migration `common.0002`)

- 변경: `common.AgentEvent`에 nullable `account_owner`와 indexed nullable
  `correlation_id`를 추가하고 소유자·상관 ID별 시간순 조회 인덱스를 추가했다.
- 이유: 자산 DB 행이 만들어지기 전의 등록 단계도 실제 이벤트로 보존하고, 작품 등록과
  Buyer Agent 협상·구매의 여러 이벤트를 하나의 실행 흐름으로 안전하게 그룹화하기 위함이다.
- 영향 범위: 신규 이벤트는 Firestore에도 `owner_user_id`와 `correlation_id`가 미러링된다.
  기존 이벤트는 nullable 필드이므로 그대로 보존되며, 기존 자산 FK 기반 권한 필터도 유지된다.
- 검증: migration drift, 이벤트 fan-out, 소유권 필터, SSE 직렬화 및 등록·협상 흐름 테스트로
  검증한다.
- Alembic 필요 여부: Django 앱 DB 스키마 변경이므로 Django migration
  `apps/common/migrations/0002_agentevent_live_flow.py`가 필요하다. Alembic 대상은 아니다.

## 2026-08-15 — Sponsored USDC browser payment (migration `ip.0019`)

- 변경: `ip.SponsoredPaymentIntent`를 추가했다. 인증된 구매자·지갑·자산·수취 지갑·고정 USDC 금액·고유 memo·만료·거래 서명·정산 상태를 보존한다.
- 이유: 플랫폼이 Solana 수수료를 부담하는 브라우저 USDC 결제에서, 클라이언트가 보낸 가격/수취인/성공 정보를 신뢰하지 않고 intent와 finalized 온체인 거래를 정확히 대조하기 위함이다.
- 영향 범위: 공개 작품 상세의 구매 경로가 native SOL Solana Pay에서 sponsor-paid USDC로 전환된다. 기존 License 장부와 다운로드 만료 정책은 유지한다.
- 검증: intent는 사용자·지갑·자산·금액·memo·만료에 묶이고 거래 서명은 unique다. 실제 Devnet은 sponsor KMS 키, SOL 잔액, USDC 보유 지갑이 필요한 배포 검증 항목이다.
- Alembic 필요 여부: Django 앱 DB 스키마 변경이므로 Django migration `ip.0019_sponsoredpaymentintent`가 필요하다. Alembic 대상은 아니다.

## 2026-08-15 — IP asset amount and currency (migration `ip.0020`)

- 변경: `ip_ipasset`에 nullable `target_amount`, `min_amount`(각각 `Decimal(16, 9)`)와 기본값 `USDC`의 `currency`를 추가한다.
- 데이터 이관: 기존 모든 행에서 `target_price_sol` 값을 `target_amount`에, `min_price_sol` 값을 `min_amount`에 그대로 복사하고 `currency`를 `USDC`로 설정한다. 환산은 수행하지 않는다.
- 영향 범위: 기존 가격 컬럼은 유지하므로 현재 가격·협상·결제 런타임 경로는 변경되지 않는다. 새 컬럼은 마이그레이션 적용 후부터 조회할 수 있다.
- 검증: 마이그레이션의 `RunPython` 및 `init/backfill_ipasset_amounts.py`는 동일한 무환산 갱신을 idempotent하게 수행한다.
- Alembic 필요 여부: Django 앱 DB 스키마 변경이므로 Django migration `ip.0020_ipasset_amount_currency`가 필요하다. Alembic 대상은 아니다.

## 2026-08-15 — IP asset price source migration

- 변경: 신규 작품 등록과 판매 조건 수정은 `min_price_sol`·`target_price_sol`을 기록하지 않고 `min_amount`·`target_amount`와 `currency=USDC`만 기록한다. 카탈로그, 협상, Solana Pay 가격 검증 및 라이브러리 UI도 새 금액 컬럼을 조회한다.
- 이유: 가격의 단일 저장·조회 기준을 통화가 명시된 금액 컬럼으로 통합하기 위함이다.
- 영향 범위: 새 작품의 legacy SOL 가격 컬럼은 `NULL`이다. 기존 행은 `ip.0020` 데이터 이관 및 시작 백필로 새 컬럼에 보존된 값으로 동작한다.
- 검증: `tests/unit/test_negotiation_engine.py`, `tests/unit/test_x402_service.py`의 DB 비의존 테스트 26개를 통과했다.

## 2026-08-15 — Agent sponsored USDC payment channel (migration `ip.0021`)

- 변경: `ip.SponsoredPaymentIntent.channel`에 `browser`와 `agent`를 기록한다. Agent intent는 서버 설정의 Buyer service user 및 Buyer KMS 공개키에만 연결한다.
- 이유: Phantom 브라우저 결제와 KMS 위임형 Buyer Agent 결제를 구분해, 한 경로의 intent를 다른 경로로 정산하거나 라이선스 귀속을 바꾸지 못하게 하기 위함이다.
- 영향 범위: Agent sponsor-paid USDC 결제는 `currency=USDC`인 자산의 `target_amount`만 가격 기준으로 사용한다. legacy `target_price_sol` 및 `min_price_sol`은 이 결제 경로에서 조회하지 않는다.
- 검증: Agent가 canonical ATA 생성·`transferChecked`·memo transaction만 서명하고 응답 금액 변조를 거절하는 단위 테스트를 통과했다. DB 통합 테스트는 PostgreSQL 실행 환경에서 수행한다.
- Alembic 필요 여부: Django 앱 DB 스키마 변경이므로 Django migration `ip.0021_sponsoredpaymentintent_channel`이 필요하다. Alembic 대상은 아니다.
- Alembic 필요 여부: 추가 스키마 변경은 없으며 기존 Django migration `ip.0020_ipasset_amount_currency`를 적용한다. Alembic 대상은 아니다.

## 2026-08-18 — 협상가 기반 sponsor-paid USDC 결제 (migrations `negotiation.0003`, `ip.0023`)

- 변경: `NegotiationSession.currency`로 SOL·USDC 협상 세션을 구분하고,
  `SponsoredPaymentIntent.negotiation_session`으로 sponsor-paid USDC intent를
  수락된 협상 세션에 연결한다.
- 이유: Buyer Agent가 합의한 USDC 가격을 intent 생성·온체인 검증·라이선스 발급
  전체에서 동일하게 사용하도록 보장하기 위함이다. 클라이언트가 임의 할인 금액을
  제출하는 방식은 허용하지 않는다.
- 영향 범위: `session_id`가 없는 기존 sponsor-paid USDC 결제는 정가를 유지한다.
  `session_id`가 주어진 요청은 동일 자산의 USDC·ACCEPT 세션과 양수
  `final_price_usdc`가 모두 있어야 하며, 그렇지 않으면 `409`로 거절한다.
- 검증: Buyer Agent의 canonical sponsor transaction·승인 게이트 단위 테스트 17개와
  PostgreSQL에서 수락 USDC 세션의 할인 금액 반영·미수락 세션 거절·USDC 협상 세션
  저장 테스트를 통과했다. 전체 HTTP 통합 스위트는 현재 개발 환경에 없는 `webauthn`
  패키지가 URL 설정에서 import되어 별도로 실행할 수 없다.
- Alembic 필요 여부: Django 앱 DB 스키마 변경이므로
  `apps/negotiation/migrations/0003_negotiationsession_currency.py` 및
  `apps/ip/migrations/0023_sponsoredpaymentintent_negotiation_session.py` 적용이
  필요하다. Alembic 대상은 아니다.
