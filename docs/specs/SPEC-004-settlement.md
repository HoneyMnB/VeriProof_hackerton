# SPEC-004 — Solana USDC 정산 & 라이선스·인증서

- 상위: [PRD](../PRD.md) · [아키텍처](../00-architecture-and-data-model.md)
- 시나리오/페이지: S1 / 정산 계층
- 관련 SPEC: SPEC-003(협상), SPEC-002(접근), SPEC-008(로열티)
- 개발방법: TDD

---

## 1. 목적
구매자 AI가 온체인 USDC 결제 후 트랜잭션 서명을 제출(`/settle`) 하거나 pay.sh webhook이 도착하면, 서버가 온체인 결제를 검증하고 라이선스를 발급하며 인증서 Memo를 발행하고 만료형 원본 다운로드 토큰을 제공한다. 후속처리는 GCP 비동기 파이프라인(Workflows) 또는 동기 폴백으로 동일 서비스 메서드를 통해 수행된다.

## 2. 범위
- IN: 결제 검증, 라이선스 발급(멱등), 인증서 Memo 발행, 다운로드 토큰, Firestore 상태 미러, BigQuery 로그, webhook→Pub/Sub 발행.
- OUT: 협상(SPEC-003), 로열티 분배(SPEC-008), 배치(SPEC-007).

---

## 3. EARS 요구사항

### 결제 검증
- **R1** WHEN `POST /api/v1/ip/{asset_id}/settle`(`session_id`, `tx_signature`, `buyer_wallet`)가 오면, the system SHALL `SolanaService.verify_usdc_payment(tx_sig, expected_recipient=<수취주소 해석 규칙>, expected_amount=final_price, mint=USDC)`를 호출한다. (`expected_recipient`은 아키텍처 §8 규칙: 2차 창작물이면 `PLATFORM_ESCROW_PUBKEY`, 아니면 `creator.wallet_address` — SPEC-002/003의 `pay_to`와 반드시 일치)
- **R2** WHEN 결제 검증이 수행되면, the system SHALL recipient·mint·amount가 **모두** 일치하고 커밋먼트가 `confirmed` 이상일 때만 유효로 판정한다.
- **R3** IF 검증이 무효면 THEN the system SHALL 400 `{error:"invalid_settlement"}`를 반환한다.

### 라이선스·인증서
- **R4** WHEN 결제가 유효하면, the system SHALL `LicenseService.grant()`로 `License`를 발급하고 `payment_tx_sig`를 저장한다.
- **R5** WHERE `payment_tx_sig`가 이미 존재하면(멱등), the system SHALL 신규 발급 없이 기존 `License`를 반환한다.
- **R6** WHEN 라이선스가 발급되면, the system SHALL `SolanaService.issue_certificate(asset_id, buyer_wallet, memo)`(KMS 서명)로 인증서 Memo tx를 발행하고 `certificate_tx_sig`를 저장한다.
- **R7** WHEN 라이선스가 발급되면, the system SHALL 만료형 `download_token`(`DOWNLOAD_TOKEN_TTL_SECONDS`)을 생성하고 `download_url`을 반환한다.
- **R8** WHEN 라이선스가 발급되면, the system SHALL `NegotiationSession`이 있으면 이를 라이선스에 연결한다.

### 다운로드
- **R9** WHEN `GET /files/{token}`이 유효·미만료 토큰으로 오면, the system SHALL 원본을 서명 URL 또는 스트림으로 제공한다.
- **R10** IF 토큰이 만료/무효면 THEN the system SHALL 403을 반환한다.
- **R11** IF 원본이 이미 purge되었으면 THEN the system SHALL 410(Gone, 재다운로드 불가)을 반환한다.

### 비동기 파이프라인 / webhook
- **R12** WHEN `POST /api/v1/paysh/webhook`이 오면, the system SHALL `X-PaySh-Signature`를 `PAYSH_WEBHOOK_SECRET`으로 검증하고 불일치 시 401을 반환한다.
- **R13** WHEN webhook 서명이 유효하면, the system SHALL 최소 필드 검증 후 이벤트를 `PubSubPublisher.publish(PUBSUB_PAYMENTS_TOPIC, ...)`로 발행하고 즉시 200을 반환한다(블로킹 금지).
- **R14** WHEN 정산 후속처리(Workflows 또는 동기 폴백)가 실행되면, the system SHALL 순서대로 [검증 → 라이선스 → 인증서 → Firestore 미러(status=LICENSED) → BigQuery 로그]를 수행한다.
- **R14b** IF 정산 대상 자산이 2차 창작물(`parent_asset` 존재)이면 THEN the system SHALL 라이선스 발급 후 `RoyaltyService.distribute(license)`(SPEC-008)를 파이프라인 단계로 실행한다(에스크로 → 원작자/2차창작자 분배).
- **R15** WHEN 상태가 전이되면, the system SHALL `EventRecorder.record("PAYMENT_VERIFIED"|"CERT_ISSUED", ...)`로 팬아웃한다.

### 관측/견고성
- **R16** IF `issue_certificate`가 실패하면 THEN the system SHALL 라이선스는 유지하되 `certificate_tx_sig=null`로 두고 재시도 큐/로그를 남긴다(결제 검증과 인증서 발행 분리).
- **R17** WHEN webhook이 동일 `tx_signature`로 재전송되면, the system SHALL 멱등 처리하여 중복 라이선스를 생성하지 않는다.

---

## 4. 인수조건
| AC | 조건 | 기대결과 |
|----|------|---------|
| AC-1 | 유효 결제(recipient/mint/amount 일치) | 200, License 발급, certificate_tx, download_url |
| AC-2 | 금액 부족(amount < final_price) | 400 invalid_settlement |
| AC-3 | 잘못된 mint | 400 |
| AC-4 | 동일 tx_sig 재제출 | 200 + 기존 License(중복 없음) |
| AC-5 | 유효 토큰으로 /files 접근 | 200 + 원본 |
| AC-6 | 만료 토큰 | 403 |
| AC-7 | purge된 원본 토큰 | 410 |
| AC-8 | webhook 서명 불일치 | 401 |
| AC-9 | webhook 유효 | 200 + Pub/Sub publish 1회 호출 |
| AC-10 | 인증서 발행 실패 주입 | License 유지 + certificate_tx_sig=null (R16) |
| AC-11 | 정산 완료 | Firestore status=LICENSED 미러 + BigQuery insert 호출 |
| AC-12 | 이벤트 | PAYMENT_VERIFIED/CERT_ISSUED 기록 |

---

## 5. TDD 테스트 명세 (RED 우선 목록)

### 단위 — SolanaService (mock RPC)
- `test_verify_requires_matching_recipient`.
- `test_verify_requires_matching_mint`.
- `test_verify_requires_matching_amount_min_units`.
- `test_verify_rejects_unconfirmed_commitment`.
- `test_issue_certificate_returns_signature`.

### 단위 — LicenseService
- `test_grant_creates_license_with_payment_tx`.
- `test_grant_is_idempotent_on_duplicate_tx` (R5).
- `test_grant_generates_expiring_download_token`.

### 통합 — `POST /settle`
- `test_settle_valid_payment_grants_license` (AC-1).
- `test_settle_insufficient_amount_400` (AC-2).
- `test_settle_wrong_mint_400` (AC-3).
- `test_settle_duplicate_tx_returns_existing` (AC-4).
- `test_settle_certificate_failure_keeps_license` (AC-10, R16).
- `test_settle_mirrors_firestore_and_bigquery` (AC-11, mock 호출검증).
- `test_settle_records_events` (AC-12).

### 통합 — 다운로드 / webhook
- `test_download_valid_token_returns_original` (AC-5).
- `test_download_expired_token_403` (AC-6).
- `test_download_purged_original_410` (AC-7).
- `test_paysh_webhook_bad_signature_401` (AC-8).
- `test_paysh_webhook_valid_publishes_pubsub` (AC-9).
- `test_paysh_webhook_idempotent_on_replay` (R17).

### 파이프라인 로직 (동기 폴백)
- `test_settlement_pipeline_runs_all_steps_in_order` — verify→license→cert→firestore→bigquery.

---

## 6. 엣지 케이스 / 가정
- Devnet 지연으로 tx가 아직 `processed`면 재시도 후 `confirmed` 대기.
- amount 비교는 최소단위 정수(6 decimals)로 수행(부동소수 오차 방지).
- 가정: 일반 판매는 구매자→창작자 **직접** 송금(플랫폼 미보유). 로열티(S3)만 에스크로 경유.
- webhook과 `/settle`은 동일 정산 파이프라인을 호출(경로만 다름).
