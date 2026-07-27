# VeriProof DB 테이블 명세서

작성 기준: Django 모델(`veriproof/apps/**/models.py`)과 적용된 앱 마이그레이션 기준.
운영 기준 DB는 PostgreSQL이며, 로컬 기본 실행은 SQLite를 사용한다. `JSONField`는
PostgreSQL에서는 JSONB, SQLite에서는 TEXT 기반 JSON으로 저장된다.

이 문서는 VeriProof 도메인 앱 테이블 중심의 명세다. `auth_user`,
`django_session`, `django_content_type`, `admin_log` 등 Django 프레임워크 기본
테이블은 참조 대상만 별도로 언급한다.

## 전체 테이블 구성

| 앱 | 테이블 | 주요 역할 |
| --- | --- | --- |
| accounts | `accounts_userpreference` | 로그인 계정의 표시명, 언어, 복구 연락처, 기본 창작자 지갑 설정 |
| accounts | `accounts_walletconfiguration` | 계정별 공개 Solana 지갑 주소와 입금/정산/활성 상태 설정 |
| common | `common_agentevent` | 자산·협상·결제 흐름에서 발생한 이벤트 감사 로그 |
| ip | `ip_creator` | 창작자 지갑 주소와 표시명 |
| ip | `ip_ipasset` | 등록된 IP 자산의 원장, 가격, 공개 상태, 온체인 앵커/인증서 정보 |
| ip | `ip_assetcomponent` | 패키지형 작품의 보조 소스 파일 매니페스트 |
| ip | `ip_assetimage` | 한 작품에 추가로 포함되는 이미지 갤러리 항목 |
| ip | `ip_assistantmessage` | 창작자 비서 대화 메시지 이력 |
| ip | `ip_conversationattachment` | 대화에 첨부된 임시 파일과 AI 분석 결과 |
| ip | `ip_agentdirective` | 창작자가 관리하는 비서 행동 지침 |
| ip | `ip_assistantaction` | 비서 도구 호출과 검증 결과 감사 기록 |
| ip | `ip_subscriptionplan` | 등록·인증서 발급용 선불 구독 플랜 |
| ip | `ip_creatorsubscription` | 창작자별 구독 기간과 등록권 사용량 |
| ip | `ip_registrationcharge` | 자산 등록 시 구독 등록권 차감 기록 |
| ip | `ip_registrationdraft` | 대화형 등록 과정의 초안과 사용자 확인 상태 |
| ip | `ip_creatorexpense` | 창작자가 직접 기록한 운영 지출 |
| negotiation | `negotiation_negotiationsession` | 구매 에이전트와 자산 간 가격 협상 세션 |
| settlement | `settlement_license` | 결제 검증 후 발급된 라이선스와 다운로드 권한 |
| settlement | `settlement_royaltydistribution` | 라이선스별 원작자/2차 창작자 로열티 분배 항목 |
| settlement | `settlement_batchorder` | 여러 자산을 한 번에 구매하는 배치 주문 |
| settlement | `settlement_batchitem` | 배치 주문의 개별 자산 라인 |
| sandbox | 없음 | 현재 영속 모델 없음. IP/협상/이벤트 테이블을 재사용 |

## 앱별 상세 명세

### accounts

#### `accounts_userpreference`

- 설명: Django 로그인 사용자(`auth_user`)에 1:1로 연결되는 VeriProof 사용자 환경설정 테이블.
- 사용 용도:
  - 계정 화면의 표시명, 언어, 복구 이메일, 연락처를 저장한다.
  - 기본 창작자 지갑(`creator_wallet`)을 계정 단위로 복원해 워크스페이스와 등록 흐름에서 사용한다.
  - 지갑의 개인키, 시드 구문, 서명 세션은 저장하지 않는다.
- 주요 필드:
  - `user_id`: `auth_user` 1:1 FK. 계정 삭제 시 함께 삭제된다.
  - `display_name`, `language`: UI 표시와 다국어 기본값.
  - `recovery_email`, `contact_phone`: 계정 복구/지원 연락처.
  - `creator_wallet`: 기본 창작자 Solana 공개 주소.
  - `updated_at`: 마지막 설정 변경 시각.

#### `accounts_walletconfiguration`

- 설명: 한 계정이 관리하는 공개 Solana 지갑 주소 목록과 사용 목적을 저장하는 테이블.
- 사용 용도:
  - 입금 수령 지갑, 판매대금 수령 지갑, 현재 활성 워크스페이스 지갑을 분리한다.
  - 활성 지갑은 기존 `UserPreference.creator_wallet` 경로와 동기화되어 기존 등록/라이브러리 흐름을 유지한다.
  - 외부 지갑 연결 전 단계의 공개 주소 설정만 저장하며 비밀값은 저장하지 않는다.
- 주요 필드:
  - `user_id`: `auth_user` FK.
  - `label`, `address`: 사용자 표시명과 공개 Solana 주소.
  - `accepts_deposits`, `receives_payouts`, `is_active`: 지갑 사용 목적 플래그.
  - `created_at`: 등록 시각.
- 제약:
  - `(user_id, address)` 유니크. 같은 계정에 같은 주소를 중복 등록할 수 없다.

### common

#### `common_agentevent`

- 설명: 자산, 협상, 결제, 인증서 발급 등 시스템 주요 이벤트를 남기는 공통 감사 로그.
- 사용 용도:
  - `EventRecorder`의 PostgreSQL 저장소 역할을 한다.
  - Firestore 실시간 타임라인, BigQuery 감사 원장과 함께 이벤트 팬아웃의 기준 데이터가 된다.
  - 참조 자산 또는 협상 세션이 삭제되어도 이벤트 자체는 보존된다.
- 주요 필드:
  - `asset_id`: `ip_ipasset` nullable FK, `SET_NULL`.
  - `session_id`: `negotiation_negotiationsession` nullable FK, `SET_NULL`.
  - `type`: 이벤트 유형. 예: `OFFER`, `ACCEPT`, `PAYMENT_VERIFIED`, `CERT_ISSUED`, `ANCHORED`.
  - `payload`: 이벤트 상세 JSON.
  - `created_at`: 발생 시각.
- 주요 인덱스:
  - `type`, `created_at`, `(asset_id, created_at)`.

### ip

#### `ip_creator`

- 설명: 창작자를 Solana 공개 지갑 주소 기준으로 식별하는 테이블.
- 사용 용도:
  - 작품 등록, 비서 대화, 구독, 지출, 로열티 정산의 창작자 기준점이다.
  - 로그인 계정과 직접 1:1로 묶지 않는다. 한 계정이 여러 창작자 지갑의 작품을 관리할 수 있다.
- 주요 필드:
  - `wallet_address`: 창작자 Solana 공개 주소. 유니크 및 인덱스 적용.
  - `display_name`: 선택 표시명.
  - `created_at`: 생성 시각.

#### `ip_ipasset`

- 설명: VeriProof에 등록된 IP 자산의 핵심 원장 테이블. UUID PK가 API의 `asset_id`로 사용된다.
- 사용 용도:
  - 창작물의 제목, 설명, 유형, 가격, 공개 여부, 해시, 미리보기 URL, 원본 보관 URL, 온체인 앵커/등록 인증서 정보를 저장한다.
  - 공개 카탈로그, 라이브러리, 협상, 결제, 라이선스, 로열티 분배의 중심 엔티티다.
  - 2차 창작물의 부모 자산과 로열티 분담률을 저장해 정산 경로를 보존한다.
- 주요 필드:
  - `id`: UUID PK. 외부 API의 `asset_id`.
  - `creator_id`: `ip_creator` FK, `PROTECT`.
  - `account_owner_id`: `auth_user` nullable FK, `SET_NULL`. 작품을 관리하는 로그인 계정.
  - `title`, `description`, `ai_description`: 사용자 설명과 AI 분석 설명.
  - `asset_type`: `image`, `document`, `audio`, `video`, `software`, `product`, `other`.
  - `visibility`: `private` 또는 `public`.
  - `tags`, `ai_tags`, `category`: 검색/발견용 메타데이터.
  - `min_price_usdc`, `target_price_usdc`: 협상 가격 기준.
  - `image_sha256`: 원본 또는 작품 매니페스트의 권위 해시. 유니크.
  - `perceptual_hash`: 이미지 유사 검색용 64-bit 지각 해시.
  - `thumbnail_url`, `watermark_url`: 공개/보호 미리보기 아티팩트.
  - `original_url`, `original_expires_at`, `original_purged`: 원본 임시 보관 상태.
  - `anchor_tx_sig`: 온체인 앵커 거래 서명.
  - `registration_certificate_tx_sig`: 등록 완료 인증서 거래 서명.
  - `parent_asset_id`, `royalty_share_bps`: 2차 창작 로열티 계보.
  - `status`: `draft`, `anchored`, `listed`, `retired`.
  - `created_at`: 등록 시각.
- 주요 제약/정책:
  - `image_sha256`는 유니크.
  - `parent_asset_id`가 있으면 `royalty_share_bps`는 1~10000 bps여야 한다.
  - 공개 카탈로그 노출은 공개 상태, 앵커 완료, 등록 인증서 존재 조건을 모두 만족해야 한다.

#### `ip_assetcomponent`

- 설명: 한 작품 인증서에 포함된 보조 소스 파일 매니페스트 테이블.
- 사용 용도:
  - 패키지형 작품의 소스 파일명, MIME 타입, SHA-256, 내부 저장 위치를 검증 가능하게 보존한다.
  - 공개 카탈로그나 미리보기 응답에는 직접 노출하지 않는다.
  - 주 자산 해시는 계속 `ip_ipasset.image_sha256`가 온체인 앵커 기준이다.
- 주요 필드:
  - `asset_id`: `ip_ipasset` FK, `CASCADE`.
  - `file_name`, `content_mime_type`, `content_sha256`, `storage_url`.
  - `created_at`.

#### `ip_assetimage`

- 설명: 한 작품에 속한 추가 이미지 갤러리 항목 테이블.
- 사용 용도:
  - 첫 이미지는 `ip_ipasset`의 기존 미리보기/원본 필드를 사용하고, 두 번째 이후 이미지를 이 테이블에 저장한다.
  - 라이선스와 인증서는 이미지별이 아니라 부모 `ip_ipasset` 하나에 연결된다.
  - 공개 상세 화면은 워터마크 미리보기 갤러리를 제공하고 원본 URL은 권한 경계 안에서만 사용한다.
- 주요 필드:
  - `asset_id`: `ip_ipasset` FK, `CASCADE`.
  - `position`: 작품 내 이미지 순서.
  - `file_name`, `content_mime_type`, `content_sha256`.
  - `watermark_url`, `original_url`.
  - `created_at`.
- 제약:
  - `(asset_id, position)` 유니크.

#### `ip_assistantmessage`

- 설명: 창작자와 AI 비서 간 대화 메시지를 감사 가능하게 저장하는 테이블.
- 사용 용도:
  - 워크스페이스 대화 이력, 대화 재개, 사이드바 대화 제목 표시의 원천이다.
  - 라이선스/결제 흐름과 분리되어 있으며 대화 원문을 비즈니스 실행 결과로 직접 간주하지 않는다.
- 주요 필드:
  - `creator_id`: `ip_creator` FK, `CASCADE`.
  - `conversation_id`: 한 대화 세션을 묶는 UUID.
  - `conversation_title`: 첫 사용자 메시지에 저장되는 사용자 지정 제목.
  - `role`: `user` 또는 `assistant`.
  - `content`: 메시지 본문.
  - `created_at`.
- 주요 인덱스:
  - `(creator_id, created_at)`.

#### `ip_conversationattachment`

- 설명: 대화 분석용 임시 첨부 파일과 AI 분석 결과를 저장하는 테이블.
- 사용 용도:
  - 등록 전 대화에 첨부된 파일의 소유자, 지문, 임시 URL, 만료 시각, 분석 결과를 기록한다.
  - 분석 실패 시에는 레코드를 만들지 않아 거짓 분석 결과가 런타임에 남지 않도록 한다.
  - 원본 임시 URL은 공개 API/브라우저 응답에 직접 노출하지 않는다.
- 주요 필드:
  - `creator_id`: `ip_creator` FK, `CASCADE`.
  - `source_message_id`: `ip_assistantmessage` nullable FK, `SET_NULL`.
  - `file_name`, `content_mime_type`, `content_sha256`, `perceptual_hash`.
  - `temporary_url`, `expires_at`.
  - `analysis`: AI 분석 결과 JSON.
  - `created_at`.

#### `ip_agentdirective`

- 설명: 창작자가 명시적으로 확인·수정한 AI 비서 행동 지침 테이블.
- 사용 용도:
  - 대화 원문과 별도로 저장해 채팅 내용이 묵시적으로 에이전트 지침이 되는 것을 방지한다.
  - 활성 지침만 Gemini 창작자 비서 컨텍스트에 전달한다.
- 주요 필드:
  - `creator_id`: `ip_creator` FK, `CASCADE`.
  - `title`, `instruction`.
  - `is_active`.
  - `created_at`, `updated_at`.
- 주요 인덱스:
  - `(creator_id, is_active, updated_at)`.

#### `ip_assistantaction`

- 설명: AI 비서가 실행한 도구 호출과 서버 검증 결과를 남기는 감사 테이블.
- 사용 용도:
  - 자연어 요청이 실제 비즈니스 데이터를 변경하기 전후의 요청/결과/검증 상태를 기록한다.
  - 거절, 입력 대기, 실패 시도도 감사 가능하게 보존한다.
  - 비밀값이나 결제 자격 증명은 저장하지 않는다.
- 주요 필드:
  - `creator_id`: `ip_creator` FK, `CASCADE`.
  - `source_message_id`: `ip_assistantmessage` nullable FK, `SET_NULL`.
  - `action_name`.
  - `status`: `completed`, `awaiting_input`, `rejected`, `failed`.
  - `request_payload`, `result_payload`.
  - `verification_passed`, `verified_at`.
  - `created_at`.

#### `ip_subscriptionplan`

- 설명: 등록·인증서 발급 비용을 선납하는 운영 구독 플랜 정의 테이블.
- 사용 용도:
  - 월 요금, 포함 등록 횟수, 활성 여부를 플랜 코드 기준으로 관리한다.
  - 창작자 구독(`ip_creatorsubscription`)에서 참조한다.
- 주요 필드:
  - `code`: 플랜 식별자. 유니크.
  - `name`.
  - `monthly_fee_usdc`.
  - `included_registrations`.
  - `is_active`.
  - `created_at`.

#### `ip_creatorsubscription`

- 설명: 창작자별 활성 구독과 기간 내 등록권 사용량을 저장하는 테이블.
- 사용 용도:
  - 등록 시 활성 구독, 기간, 잔여 등록 횟수를 확인한다.
  - 결제 식별자(`payment_tx_sig`)로 동일 구독 결제의 중복 반영을 방지한다.
- 주요 필드:
  - `creator_id`: `ip_creator` FK, `CASCADE`.
  - `plan_id`: `ip_subscriptionplan` FK, `PROTECT`.
  - `status`: `active` 또는 `expired`.
  - `payment_tx_sig`: 구독 결제 거래 식별자. 유니크.
  - `period_start`, `period_end`.
  - `registrations_used`.
  - `created_at`.

#### `ip_registrationcharge`

- 설명: 구독 등록권을 실제 자산 등록에 사용한 차감 기록 테이블.
- 사용 용도:
  - 한 자산에 대해 등록권이 중복 차감되지 않도록 `asset_id`를 1:1로 연결한다.
  - 구독 삭제나 자산 삭제로 감사 관계가 유실되지 않도록 보호 삭제 정책을 사용한다.
- 주요 필드:
  - `subscription_id`: `ip_creatorsubscription` FK, `PROTECT`.
  - `asset_id`: `ip_ipasset` 1:1 FK, `PROTECT`.
  - `created_at`.

#### `ip_registrationdraft`

- 설명: 대화형 등록 과정에서 수집한 초안과 사용자의 최종 확인 상태를 저장하는 테이블.
- 사용 용도:
  - 첨부 파일명/해시와 사용자가 입력·확인한 등록 필드를 저장한다.
  - `collecting → confirmed → executed` 상태를 통해 최종 확인 없이 실제 등록이 실행되지 않도록 한다.
  - 등록 성공 후 실제 자산과 1:1로 연결해 추적한다.
- 주요 필드:
  - `creator_id`: `ip_creator` FK, `CASCADE`.
  - `status`: `collecting`, `confirmed`, `executed`.
  - `file_name`, `file_sha256`.
  - `fields`: 등록 후보 필드 JSON.
  - `confirmation_token`: 사용자 확인 토큰. 유니크 nullable.
  - `confirmed_at`.
  - `executed_asset_id`: `ip_ipasset` nullable 1:1 FK, `PROTECT`.
  - `created_at`, `updated_at`.

#### `ip_creatorexpense`

- 설명: 창작자가 직접 기록한 운영 지출 테이블.
- 사용 용도:
  - 워크스페이스 현금흐름 화면에서 비용 항목으로 사용한다.
  - 수입은 이 테이블에 복제하지 않고 검증된 `settlement_license.price_usdc`에서 계산한다.
- 주요 필드:
  - `creator_id`: `ip_creator` FK, `CASCADE`.
  - `amount_usdc`.
  - `memo`.
  - `occurred_at`: 비용 발생일.
  - `created_at`: 기록 생성일.

### negotiation

#### `negotiation_negotiationsession`

- 설명: 특정 IP 자산에 대한 구매 에이전트의 협상 세션 테이블.
- 사용 용도:
  - 최초 제안가, 최종 합의가, 사용 유형, 라운드별 제안/카운터 로그를 저장한다.
  - 수락 시 결제 수취 주소와 선택적 AP2 Cart Mandate를 저장한다.
  - 결제 후 발급되는 라이선스의 협상 근거로 연결된다.
- 주요 필드:
  - `id`: UUID PK.
  - `asset_id`: `ip_ipasset` FK, `CASCADE`.
  - `buyer_agent_id`.
  - `usage_type`.
  - `initial_offer_usdc`, `final_price_usdc`.
  - `status`: `negotiating`, `accepted`, `rejected`, `expired`.
  - `rounds`: 협상 라운드 JSON 로그.
  - `pay_address`: 수락 시 결제 수취 주소.
  - `ap2_cart_mandate`: AP2 활성 시 Cart Mandate VDC.
  - `created_at`, `updated_at`.

### settlement

#### `settlement_license`

- 설명: 온체인 결제 검증 후 구매자에게 발급되는 라이선스 원장 테이블.
- 사용 용도:
  - 결제 거래 서명을 idempotency key로 사용해 동일 결제의 중복 라이선스 발급을 방지한다.
  - 구매자 지갑, 가격, 사용 유형, 인증서 거래, 원본 다운로드 토큰과 만료 시각을 저장한다.
  - 7일 다운로드 권한 만료 정책의 기준 필드가 `download_expires_at`이다.
- 주요 필드:
  - `id`: UUID PK.
  - `asset_id`: `ip_ipasset` FK, `PROTECT`.
  - `session_id`: `negotiation_negotiationsession` nullable FK, `SET_NULL`.
  - `buyer_wallet`.
  - `price_usdc`.
  - `usage_type`.
  - `payment_tx_sig`: 검증된 결제 거래 서명. 유니크.
  - `certificate_tx_sig`: 라이선스 인증서 거래 서명.
  - `download_token`, `download_expires_at`.
  - `granted_at`.
- 주요 정책:
  - 라이선스 행은 결제 감사/정산 장부로 보존한다.
  - 다운로드 권한은 만료 시각이 지난 경우 활성 권한으로 인정하지 않는다.

#### `settlement_royaltydistribution`

- 설명: 라이선스 매출을 원작자와 2차 창작자에게 나누는 분배 라인 테이블.
- 사용 용도:
  - 라이선스별 수취 지갑, 역할, 금액, 전송 거래 서명, 정산 상태를 저장한다.
  - 로열티 분배 실패/대기/완료 상태를 추적한다.
- 주요 필드:
  - `license_id`: `settlement_license` FK, `CASCADE`.
  - `recipient_wallet`.
  - `role`: `original` 또는 `secondary`.
  - `amount_usdc`.
  - `transfer_tx_sig`.
  - `status`: `pending`, `settled`, `failed`.

#### `settlement_batchorder`

- 설명: 구매 에이전트가 여러 자산을 한 번에 구매하는 배치 주문 헤더 테이블.
- 사용 용도:
  - 전체 주문 금액, 결제 거래, 주문 상태를 관리한다.
  - 개별 자산 라인은 `settlement_batchitem`에 저장된다.
- 주요 필드:
  - `id`: UUID PK.
  - `buyer_agent_id`.
  - `total_usdc`.
  - `status`: `quoted`, `paid`, `settled`, `partial`, `failed`.
  - `payment_tx_sig`.
  - `created_at`.

#### `settlement_batchitem`

- 설명: 배치 주문에 포함된 개별 자산 라인 테이블.
- 사용 용도:
  - 주문 내 자산별 단가를 저장하고, 정산 후 발급된 라이선스와 연결한다.
  - 자산 삭제로 결제/주문 감사 기록이 유실되지 않도록 자산 FK는 보호한다.
- 주요 필드:
  - `order_id`: `settlement_batchorder` FK, `CASCADE`.
  - `asset_id`: `ip_ipasset` FK, `PROTECT`.
  - `unit_price_usdc`.
  - `license_id`: `settlement_license` nullable FK, `SET_NULL`.
  - `created_at`.

## 주요 관계 요약

- `auth_user` 1 ─ 1 `accounts_userpreference`
- `auth_user` 1 ─ N `accounts_walletconfiguration`
- `auth_user` 1 ─ N `ip_ipasset.account_owner`
- `ip_creator` 1 ─ N `ip_ipasset`
- `ip_creator` 1 ─ N `ip_assistantmessage`, `ip_conversationattachment`, `ip_agentdirective`, `ip_assistantaction`
- `ip_creator` 1 ─ N `ip_creatorsubscription`, `ip_creatorexpense`
- `ip_ipasset` 1 ─ N `ip_assetcomponent`, `ip_assetimage`, `negotiation_negotiationsession`, `settlement_license`, `settlement_batchitem`
- `ip_ipasset` 1 ─ N `ip_ipasset` via `parent_asset_id` for derivative lineage
- `negotiation_negotiationsession` 1 ─ N `settlement_license`
- `settlement_license` 1 ─ N `settlement_royaltydistribution`
- `settlement_batchorder` 1 ─ N `settlement_batchitem`
- `common_agentevent`는 `ip_ipasset`, `negotiation_negotiationsession`를 선택적으로 참조한다.

## 운영상 주의사항

- 원본 파일 URL(`original_url`, `temporary_url`, `storage_url`)은 공개 카탈로그나 브라우저 응답에 직접 노출하지 않는다.
- 결제와 라이선스 중복 방지는 `settlement_license.payment_tx_sig` 유니크 제약을 기준으로 한다.
- 자산의 권위 해시는 `ip_ipasset.image_sha256`이며, `perceptual_hash`는 유사 검색 후보 선별용 보조 지표다.
- 공개 자산은 `visibility=public`, 앵커 완료 상태, 등록 인증서 존재 조건을 모두 만족해야 한다.
- 수입 데이터는 `ip_creatorexpense`에 복제하지 않는다. 검증된 라이선스 금액을 기준으로 계산한다.
- 개인키, 시드 구문, 지갑 공급자 세션, 결제 자격 증명은 어느 도메인 테이블에도 저장하지 않는다.
