# SPEC-001 — IP 등록 & 온체인 앵커링

- 상위: [PRD](../PRD.md) · [아키텍처](../00-architecture-and-data-model.md)
- 시나리오/페이지: S1 / Page 1 (창작자 워크스페이스)
- 관련 SPEC: SPEC-005(라이브러리), SPEC-002(접근제어)
- 개발방법: TDD (RED→GREEN→REFACTOR)

---

## 1. 목적
창작자가 이미지를 업로드하면 (1) Gemini가 메타데이터·독창성을 분석하고, (2) 원본 SHA-256 해시를 Solana에 앵커링하며, (3) 하이브리드 저장 정책(썸네일·워터마크 영구 / 원본 임시)에 따라 저장하고, (4) `IpAsset`을 생성하여 x402 엔드포인트를 발급한다.

## 2. 범위
- IN: 이미지 업로드 검증, Gemini Vision 분석, 해시·썸네일·워터마크 생성, 온체인 앵커링, DB 저장, 응답.
- OUT: 협상/결제(SPEC-003/004), 라이브러리 UI(SPEC-005).

---

## 3. EARS 요구사항

### 정상 흐름
- **R1** WHEN 창작자가 `POST /api/v1/ip/register`로 이미지 파일·`creator_wallet`·`min_price`(+선택 `parent_asset_id`·`royalty_share_bps`)를 전송하면, the system SHALL 파일을 검증하고 신규 `IpAsset`을 생성한 뒤 `asset_id`, `anchor_tx`, `analysis`, `x402_endpoint`를 201로 반환한다. (선택 파라미터 `parent_asset_id`/`royalty_share_bps`를 통한 2차 창작물 등록 규칙은 **SPEC-008 R1~R3** 참조)
- **R2** WHEN 이미지가 수신되면, the system SHALL `ImageProcessor.sha256()`로 원본 해시를 계산하고 `IpAsset.image_sha256`에 영구 저장한다.
- **R3** WHEN 분석 가능한 파일이 수신되면, the system SHALL `GeminiService.analyze_asset(file_bytes, mime_type)`를 호출하여 `tags`, `category`, `originality_score`, `recommended_min_price_usdc`를 도출·저장한다.
- **R4** WHEN 해시가 계산되면, the system SHALL `SolanaService.anchor_hash(image_sha256, creator_wallet)`로 Memo 앵커링 트랜잭션을 생성하고 `anchor_tx_sig`를 저장한다.
- **R5** WHEN 자산이 저장되면, the system SHALL 썸네일과 워터마크 프리뷰를 생성하여 `StorageService.save_permanent()`로 영구 저장하고, 원본은 `save_temporary()`로 `ORIGINAL_RETENTION_DAYS` 만료 시각과 함께 임시 저장한다.
- **R6** WHEN 등록이 완료되면, the system SHALL `min_price` 미지정 시 `target_price_usdc = min_price × 1.5`를 기본 산정한다.
- **R7** WHEN 등록 상태가 전이되면, the system SHALL `EventRecorder.record("ANCHORED", ...)`를 호출하여 이벤트를 팬아웃한다.

### 예외/검증
- **R8** IF 업로드 파일의 MIME이 허용 목록(`image/png`, `image/jpeg`, `image/webp`)이 아니면 THEN the system SHALL 415를 반환한다.
- **R9** IF 파일 크기가 `MAX_UPLOAD_BYTES`(기본 10MB)를 초과하면 THEN the system SHALL 413을 반환한다.
- **R10** IF `creator_wallet`이 유효한 Solana pubkey(base58, 32bytes) 형식이 아니면 THEN the system SHALL 400을 반환한다.
- **R11** IF `min_price` < 0 이거나 숫자가 아니면 THEN the system SHALL 400을 반환한다.
- **R12** IF 동일 `image_sha256`가 이미 존재하면 THEN the system SHALL 409(중복 등록)와 기존 `asset_id`를 반환한다.
- **R13** IF `GeminiService.analyze_asset()`가 3회 재시도 후에도 실패하면 THEN the system SHALL 등록을 실패 처리하고 필수 외부 분석 의존성 오류를 반환한다. 임의 메타데이터나 `analysis.degraded` fallback을 만들지 않는다.
- **R14** IF `SolanaService.anchor_hash()`가 3회 재시도 후 실패하면 THEN the system SHALL `status="draft"`로 저장하고 `anchor_tx_sig=null`로 두며 202(등록됨, 앵커링 보류)를 반환한다.

### 저장 정책
- **R15** WHERE 하이브리드 저장이 적용되는 경우, the system SHALL 원본을 응답 본문에 절대 포함하지 않으며 워터마크 URL만 프리뷰로 노출한다.

---

## 4. 인수조건 (Acceptance Criteria)
| AC | 조건 | 기대결과 |
|----|------|---------|
| AC-1 | 유효 PNG + 지갑 + min_price=1.5 | 201, `asset_id` 발급, `anchor_tx` 존재, `analysis.tags` 비어있지 않음 |
| AC-2 | 저장 후 DB 조회 | `image_sha256` 64자, `thumbnail_url`/`watermark_url` 존재, `original_expires_at` 설정 |
| AC-3 | 응답 본문 검사 | 원본 바이트/URL이 응답에 포함되지 않음(R15) |
| AC-4 | GIF 업로드 | 415 |
| AC-5 | 11MB 업로드 | 413 |
| AC-6 | 잘못된 지갑 문자열 | 400 |
| AC-7 | 동일 이미지 재등록 | 409 + 기존 asset_id |
| AC-8 | Gemini 강제 실패 주입 | 저장 성공 + `analysis.degraded=true` |
| AC-9 | 앵커링 강제 실패 주입 | 202 + `status="draft"` + `anchor_tx_sig=null` |
| AC-10 | target_price 미지정 | `target_price_usdc == min_price*1.5` |

---

## 5. TDD 테스트 명세 (RED 우선 목록)

### 단위 — `services/`
- `test_image_processor_sha256_is_deterministic` — 동일 바이트→동일 해시(64 hex).
- `test_image_processor_thumbnail_resizes_within_bounds` — 썸네일 최대변 ≤ 512.
- `test_image_processor_watermark_differs_from_original` — 워터마크 바이트 ≠ 원본.
- `test_gemini_analyze_returns_schema` (mock) — 반환에 tags/category/originality_score/recommended_min_price_usdc 키.
- `test_gemini_analyze_retries_then_degrades` (mock 3 실패) — degraded 폴백 반환.
- `test_solana_anchor_hash_returns_signature` (mock) — 유효 서명 문자열.
- `test_storage_saves_permanent_and_temporary` (fake backend) — thumbnail/watermark 영구 + 원본 만료시각.

### 통합 — `POST /register`
- `test_register_happy_path_returns_201_with_asset` (외부서비스 mock).
- `test_register_persists_asset_fields` — DB 필드 검증(AC-2).
- `test_register_response_excludes_original` (AC-3 / R15).
- `test_register_rejects_non_image_mime` (AC-4).
- `test_register_rejects_oversize` (AC-5).
- `test_register_rejects_invalid_wallet` (AC-6).
- `test_register_duplicate_hash_returns_409` (AC-7).
- `test_register_gemini_failure_degrades_gracefully` (AC-8).
- `test_register_anchor_failure_returns_202_draft` (AC-9).
- `test_register_default_target_price` (AC-10).
- `test_register_records_anchored_event` — EventRecorder 호출 검증(R7).

---

## 6. 엣지 케이스 / 가정
- 손상된(디코드 불가) 이미지 → Pillow 예외 → 400.
- 투명 PNG 워터마크 합성 시 알파채널 처리.
- 매우 큰 해상도 → 썸네일 생성 메모리 상한.
- 가정: `creator_wallet`은 창작자가 직접 보유(자기수탁), 서버는 검증만.
