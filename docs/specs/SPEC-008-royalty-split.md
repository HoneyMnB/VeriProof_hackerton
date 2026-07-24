# SPEC-008 — 2차 창작 로열티 자동 분배 (시나리오 3)

- 상위: [PRD](../PRD.md) · [아키텍처](../00-architecture-and-data-model.md)
- 시나리오: S3 (2차 창작물 판매 시 원작자/2차창작자 자동 분배)
- 관련 SPEC: SPEC-001(부모 링크), SPEC-004(정산)
- 개발방법: TDD

---

## 1. 목적
2차 창작물(`parent_asset` 링크 + `royalty_share_bps` 보유)이 판매되면, 플랫폼 에스크로 지갑이 결제를 수취한 뒤 원작자와 2차창작자에게 정해진 비율로 **실제 온체인 USDC 분할 송금**을 수행한다(Rust 스마트컨트랙트 대체: 서버 오케스트레이션 + 실 SPL 송금).

## 2. 범위
- IN: 2차 창작물 등록(부모 링크·분배율), 에스크로 수취 검증, 분할 송금, `RoyaltyDistribution` 기록, 부분실패 보상.
- OUT: 온체인 스마트컨트랙트 배포(제외), 단순 단건 정산(SPEC-004).

---

## 3. EARS 요구사항

### 2차 창작물 등록
- **R1** WHEN 창작자가 `parent_asset_id`와 `royalty_share_bps`를 포함해 등록하면, the system SHALL 부모 자산 존재를 확인하고 `IpAsset.parent_asset`/`royalty_share_bps`를 설정한다.
- **R2** IF `royalty_share_bps`가 1~10000 범위를 벗어나면 THEN the system SHALL 400을 반환한다.
- **R3** IF `parent_asset_id`가 존재하지 않으면 THEN the system SHALL 404를 반환한다.

### 에스크로 정산 + 분배
- **R4** WHEN 2차 창작물 결제가 에스크로 지갑(`PLATFORM_ESCROW_PUBKEY`)으로 수취·검증되면, the system SHALL 라이선스를 발급하고 로열티 분배 절차를 개시한다.
- **R5** WHEN 분배를 산정하면, the system SHALL 원작자 몫 `original = total × royalty_share_bps/10000`, 2차창작자 몫 `secondary = total − original`을 계산한다.
- **R6** WHEN 분배를 실행하면, the system SHALL `RoyaltyService.distribute()`가 `SolanaService.transfer_usdc()`(KMS 서명)로 원작자·2차창작자 지갑에 각각 실제 송금하고 `RoyaltyDistribution`(role, amount, transfer_tx_sig)을 기록한다.
- **R7** WHEN 각 송금이 완료되면, the system SHALL 해당 분배 레코드를 `status="settled"`로 전이하고 `EventRecorder.record("ROYALTY_SPLIT", ...)`를 기록한다.

### 정합성/견고성
- **R8** WHEN 분배 금액을 산정하면, the system SHALL 최소단위 정수로 계산하여 `original + secondary == total`(잔돈 손실 0)을 보장한다.
- **R9** IF 일부 송금이 실패하면 THEN the system SHALL 실패 레코드를 `status="failed"`로 남기고 재시도 큐에 등록하며, 성공분은 유지한다.
- **R10** WHERE 다단계 2차창작(부모의 부모)인 경우, the system SHALL MVP에서는 직계 1단계만 분배하고 상위 체인은 확장 항목으로 표기한다.

---

## 4. 인수조건
| AC | 조건 | 기대결과 |
|----|------|---------|
| AC-1 | 부모 링크 + share=3000 등록 | 저장 성공(parent_asset, bps=3000) |
| AC-2 | share=0 또는 10001 | 400 |
| AC-3 | 없는 parent | 404 |
| AC-4 | total=10, bps=3000 | original=3.0, secondary=7.0 |
| AC-5 | 분배 실행 | 2건 transfer_tx_sig, 각 status=settled |
| AC-6 | 정수 합산 검증 | original+secondary == total (오차 0) |
| AC-7 | 2차창작자 송금 실패 주입 | 원작자분 settled 유지 + 실패분 failed + 재시도 등록 |
| AC-8 | 이벤트 | ROYALTY_SPLIT 기록 |
| AC-9 | 에스크로 수취 검증 | recipient=PLATFORM_ESCROW_PUBKEY 일치 필수 |

---

## 5. TDD 테스트 명세 (RED 우선 목록)

### 단위 — 분배 계산/실행
- `test_split_computes_original_and_secondary` (AC-4).
- `test_split_uses_integer_min_units_no_loss` (AC-6, R8).
- `test_distribute_transfers_to_both_wallets` (mock transfer) (AC-5).
- `test_distribute_partial_failure_keeps_success` (AC-7, R9).
- `test_distribute_records_royalty_events` (AC-8).

### 단위 — 등록 검증
- `test_secondary_registration_sets_parent_and_bps` (AC-1).
- `test_reject_out_of_range_bps` (AC-2).
- `test_reject_missing_parent` (AC-3).

### 통합 — 에스크로 정산
- `test_royalty_settlement_requires_escrow_recipient` (AC-9).
- `test_royalty_settlement_grants_license_then_distributes`.
- `test_royalty_settlement_end_to_end_split` (mock RPC) — 10 USDC → 3/7 분배 + 라이선스.

---

## 6. 엣지 케이스 / 가정
- 원작자=2차창작자 동일 지갑 → 단일 송금으로 합산 처리.
- 분배율 반올림 → 나머지(remainder)는 2차창작자(판매자)에게 귀속.
- 에스크로 잔액 부족 → 송금 실패로 처리·알림.
- 가정: S3은 에스크로 경유(SPEC-004의 P2P 직접결제와 구분). 에스크로 서명키는 KMS/Secret Manager(로컬 env 폴백).
