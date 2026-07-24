# SPEC-003 — Gemini 자율 가격 협상

- 상위: [PRD](../PRD.md) · [아키텍처](../00-architecture-and-data-model.md)
- 시나리오/페이지: S1 / 협상 계층
- 관련 SPEC: SPEC-002(진입), SPEC-004(정산)
- 개발방법: TDD

---

## 1. 목적
구매자 AI가 `POST /api/v1/ip/{asset_id}/negotiate`로 제안을 보내면, 판매자 AI(Gemini `gemini-3.6-flash`)가 창작자 조건(최소가/목표가/용도)에 따라 ACCEPT / COUNTER_OFFER / REJECT를 구조화 JSON으로 응답한다. Gemini 실패 시 규칙기반 폴백으로 협상을 지속한다.

## 2. 범위
- IN: 협상 세션 생성/갱신, Gemini 추론(structured output), 규칙기반 폴백, 협상 라운드 기록, AP2 Cart Mandate(선택).
- OUT: 결제 검증(SPEC-004), 402 진입(SPEC-002).

---

## 3. EARS 요구사항

### 협상 로직
- **R1** WHEN `negotiate` 요청(`buyer_agent_id`, `offer_usdc`, `usage_type`)이 오면, the system SHALL 신규 `NegotiationSession`을 생성(또는 기존 세션 이어받기)하고 라운드를 기록한다.
- **R2** IF `offer_usdc ≥ min_price` THEN the system SHALL `status="ACCEPT"`, `price_usdc=offer_usdc`, `pay_address=<수취주소 해석 규칙>`을 반환한다. (**결제 수취주소 해석**, 아키텍처 §8: 2차 창작물이면 `PLATFORM_ESCROW_PUBKEY`, 아니면 `creator.wallet_address`)
- **R3** IF `offer_usdc < min_price` THEN the system SHALL `min_price`와 `target_price` 사이의 합리적 `COUNTER_OFFER` 가격을 산정하여 반환한다(`pay_address=null`).
- **R4** WHEN 협상 추론이 필요하면, the system SHALL `GeminiService.negotiate(min_price, target_price, offer_usdc, usage_type, history)`를 `response_schema` 강제로 호출한다.
- **R5** WHEN Gemini 응답을 받으면, the system SHALL `{status, price_usdc, reason}` 스키마로 파싱하고 `NegotiationResponse`(+`session_id`)로 반환한다.
- **R6** WHEN 세션이 갱신되면, the system SHALL `rounds`에 `{offer, counter, status, reason, ts}`를 append하고 `EventRecorder.record("OFFER"|"COUNTER"|"ACCEPT", ...)`를 기록한다.
- **R7** WHEN `status="ACCEPT"`이면, the system SHALL `NegotiationSession.status="accepted"`, `final_price_usdc`, `pay_address`를 확정한다.

### 폴백/견고성
- **R8** IF Gemini 호출이 3회 재시도 후 실패하거나 JSON 파싱이 실패하면 THEN the system SHALL `NegotiationEngine` 규칙기반 폴백(R2/R3 로직)으로 응답한다.
- **R9** IF COUNTER_OFFER 라운드가 `MAX_ROUNDS`(기본 5)를 초과하면 THEN the system SHALL `status="REJECT"`, `reason="max rounds exceeded"`를 반환한다.
- **R10** IF Gemini가 `price_usdc < min_price`인 ACCEPT를 반환하면 THEN the system SHALL 이를 무시하고 `min_price`로 보정하거나 COUNTER로 강등한다(창작자 보호 불변식).

### 검증
- **R11** IF `offer_usdc ≤ 0` 또는 숫자가 아니면 THEN the system SHALL 422를 반환한다.
- **R12** IF `usage_type`이 허용 목록(commercial/non-commercial/editorial) 밖이면 THEN the system SHALL 422를 반환한다.
- **R13** IF `asset_id`가 없으면 THEN the system SHALL 404를 반환한다.

### AP2 (선택)
- **R14** WHERE `AP2_ENABLED=true`이고 `status="ACCEPT"`이면, the system SHALL `X402Service.build_ap2_mandate(session, "cart")`로 Cart Mandate(VDC)를 생성하여 `session.ap2_cart_mandate`에 저장한다.

---

## 4. 인수조건
| AC | 조건 | 기대결과 |
|----|------|---------|
| AC-1 | offer=2.0, min=1.5 (일반 자산) | ACCEPT, price=2.0, pay_address=creator |
| AC-1b | offer=2.0, min=1.5 (2차 창작물) | ACCEPT, pay_address=PLATFORM_ESCROW_PUBKEY |
| AC-2 | offer=1.0, min=1.5, target=3.0 | COUNTER_OFFER, 1.5 ≤ price ≤ 3.0, pay_address=null |
| AC-3 | ACCEPT 응답 후 세션 | status=accepted, final_price 설정, rounds 길이≥1 |
| AC-4 | Gemini 강제실패 | 규칙기반 폴백으로 정상 ACCEPT/COUNTER 응답 |
| AC-5 | Gemini가 min 미만 ACCEPT 반환(mock) | 보정되어 price ≥ min_price (R10) |
| AC-6 | 6번째 COUNTER 라운드 | REJECT |
| AC-7 | offer=-1 | 422 |
| AC-8 | usage_type="unknown" | 422 |
| AC-9 | 협상 이벤트 | OFFER/COUNTER/ACCEPT 이벤트 기록 |
| AC-10 | AP2_ENABLED + ACCEPT | session.ap2_cart_mandate 생성 |

---

## 5. TDD 테스트 명세 (RED 우선 목록)

### 단위 — NegotiationEngine (규칙기반, Gemini 없이)
- `test_accept_when_offer_ge_min`.
- `test_counter_between_min_and_target_when_offer_below_min`.
- `test_counter_price_never_below_min` (R10).
- `test_reject_after_max_rounds` (R9).

### 단위 — GeminiService.negotiate (mock)
- `test_negotiate_uses_response_schema`.
- `test_negotiate_parses_status_price_reason`.
- `test_negotiate_falls_back_on_parse_error` (R8).
- `test_negotiate_clamps_accept_below_min` (R10).

### 통합 — `POST /negotiate`
- `test_negotiate_accept_returns_pay_address` (AC-1).
- `test_negotiate_accept_routes_secondary_to_escrow` (AC-1b).
- `test_negotiate_counter_offer_range` (AC-2).
- `test_negotiate_creates_and_updates_session` (AC-3).
- `test_negotiate_records_rounds_and_events` (AC-9).
- `test_negotiate_rejects_negative_offer_422` (AC-7).
- `test_negotiate_rejects_bad_usage_type_422` (AC-8).
- `test_negotiate_unknown_asset_404`.
- `test_negotiate_gemini_failure_uses_fallback` (AC-4).
- `test_negotiate_ap2_mandate_when_enabled` (AC-10).

---

## 6. 엣지 케이스 / 가정
- 동시 다중 구매자 협상 → 세션은 `(asset, buyer_agent_id)` 단위 분리.
- Gemini가 스키마 외 필드 반환 → 필요한 3필드만 취함.
- 통화 단위는 USDC(소수 6자리)로 반올림.
- 가정: 협상은 결제를 보장하지 않음(ACCEPT ≠ 결제완료). 결제는 SPEC-004에서 검증.
