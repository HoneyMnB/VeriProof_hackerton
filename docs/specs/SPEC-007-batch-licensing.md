# SPEC-007 — B2B 초소액 대량 라이선싱 (시나리오 2)

- 상위: [PRD](../PRD.md) · [아키텍처](../00-architecture-and-data-model.md)
- 시나리오: S2 (뉴스/언론 AI의 다량 스톡 이미지 초소액 라이선싱)
- 관련 SPEC: SPEC-003(협상), SPEC-004(정산)
- 개발방법: TDD

---

## 1. 목적
구매자 AI가 다수 자산을 한 번에 견적·정산한다. 이미지당 초소액(예: 0.05 USDC)으로 배치 견적을 산정하고, 일괄 결제(또는 개별 결제) 검증 후 각 자산 라이선스를 발급한다. 견적은 고속·구조화 모델(`gemini-3.5-flash-lite`)을 사용한다.

## 2. 범위
- IN: 배치 견적(`/batch/negotiate`), 배치 정산(`/batch/settle`), `BatchOrder`/`BatchItem` 관리, 부분 성공 처리.
- OUT: 단건 협상(SPEC-003), 로열티(SPEC-008).

---

## 3. EARS 요구사항

### 배치 견적
- **R1** WHEN `POST /api/v1/ip/batch/negotiate`(`buyer_agent_id`, `items:[asset_id...]`, `usage_type`)가 오면, the system SHALL 각 자산의 `min_price` 기반 단가를 산정하여 `BatchOrder`(status=quoted)와 항목별 `unit_price_usdc`, `total_usdc`를 반환한다.
- **R2** WHEN 단가를 산정하면, the system SHALL `GeminiService.quote_batch()`(`gemini-3.5-flash-lite`)를 사용하되, 실패 시 `unit = max(min_price, MICRO_FLOOR)` 규칙 폴백을 적용한다.
- **R3** IF `items`가 비었거나 존재하지 않는 asset_id를 포함하면 THEN the system SHALL 422와 무효 항목 목록을 반환한다.
- **R4** WHERE 항목 수가 `BATCH_MAX`(기본 200)를 초과하면, the system SHALL 422를 반환한다.

### 배치 정산
- **R5** WHEN `POST /api/v1/ip/batch/settle`(`order_id`, `tx_signature`)가 오면, the system SHALL 결제 총액이 `BatchOrder.total_usdc`와 일치하는지 온체인 검증한다.
- **R6** WHEN 결제가 유효하면, the system SHALL 각 `BatchItem`에 대해 `LicenseService.grant()`로 라이선스를 발급하고 항목에 연결한다.
- **R7** WHEN 배치 정산이 완료되면, the system SHALL `BatchOrder.status="settled"`로 전이하고 항목별 `download_token`을 반환한다.
- **R8** IF 일부 항목 발급이 실패하면 THEN the system SHALL 성공/실패 항목을 분리 보고하고 `status="partial"`로 표기하며 실패 항목 재시도 정보를 제공한다.

### 관측/멱등
- **R9** WHEN 배치 정산이 수행되면, the system SHALL 항목별 `EventRecorder`/BigQuery 로그를 기록한다.
- **R10** WHERE 동일 `order_id`·`tx_signature`로 재요청되면, the system SHALL 멱등 처리한다.

---

## 4. 인수조건
| AC | 조건 | 기대결과 |
|----|------|---------|
| AC-1 | items 3개 견적 | quoted, 3개 unit_price, total=합계 |
| AC-2 | 초소액 단가 | unit_price ≥ MICRO_FLOOR(예 0.05) |
| AC-3 | 빈 items | 422 |
| AC-4 | 존재하지 않는 asset 포함 | 422 + 무효목록 |
| AC-5 | 201개 항목 | 422 (BATCH_MAX) |
| AC-6 | 총액 일치 결제 | settled, 각 항목 License + download_token |
| AC-7 | 총액 불일치 | 400 |
| AC-8 | 일부 항목 실패 주입 | partial + 성공/실패 분리 보고 |
| AC-9 | quote 모델 실패 | 규칙 폴백 단가 |
| AC-10 | 동일 order/tx 재요청 | 멱등(중복 없음) |

---

## 5. TDD 테스트 명세 (RED 우선 목록)

### 단위 — 견적/정산 로직
- `test_quote_batch_sums_total`.
- `test_quote_unit_respects_micro_floor` (AC-2).
- `test_quote_batch_fallback_on_model_failure` (AC-9).
- `test_batch_settle_requires_total_match` (AC-7).
- `test_batch_grant_all_items_on_success`.
- `test_batch_partial_failure_reporting` (AC-8, R8).

### 통합 — API
- `test_batch_negotiate_returns_quote` (AC-1).
- `test_batch_negotiate_empty_items_422` (AC-3).
- `test_batch_negotiate_invalid_asset_422` (AC-4).
- `test_batch_negotiate_exceeds_max_422` (AC-5).
- `test_batch_settle_success_grants_licenses` (AC-6).
- `test_batch_settle_idempotent` (AC-10).
- `test_batch_settle_logs_each_item` (R9).

---

## 6. 엣지 케이스 / 가정
- 개별 결제 모드(항목별 tx) 옵션은 확장 여지로 두고, MVP는 일괄 결제 총액 검증 우선.
- 초소액 합산 시 소수 6자리 반올림 누적오차 → 최소단위 정수 합산으로 처리.
- 대량 라이선스는 각 자산 창작자 지갑이 상이할 수 있음 → 정산 모델은 플랫폼 에스크로 경유 또는 창작자별 분할(해커톤은 단일 판매자 데모셋 가정 허용, 다중 창작자는 S3 분배 로직 재사용).
