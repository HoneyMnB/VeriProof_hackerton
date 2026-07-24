# SPEC-002 — x402 접근 인터셉터 & 클라이언트 판별

- 상위: [PRD](../PRD.md) · [아키텍처](../00-architecture-and-data-model.md)
- 시나리오/페이지: S1·S2 / 프로토콜 계층
- 관련 SPEC: SPEC-003(협상), SPEC-004(정산)
- 개발방법: TDD

---

## 1. 목적
외부 AI 에이전트가 자산 URL에 접근할 때 라이선스 보유 여부를 판별하여, 미보유 시 `HTTP 402`와 **a2a-x402 payment-required** 스펙(협상·결제 방법)을 런타임에 즉시 전달한다. 사람(Non-agent) 클라이언트는 Solana Pay 고정가 폴백으로 안내한다.

## 2. 범위
- IN: 클라이언트 판별(미들웨어), 402 응답 생성(a2a-x402 정렬), 라이선스 검사, Solana Pay 폴백.
- OUT: 실제 협상 로직(SPEC-003), 결제 검증(SPEC-004).

---

## 3. EARS 요구사항

### 접근 제어
- **R1** WHEN `GET /api/v1/ip/{asset_id}` 요청이 오면, the system SHALL 자산 존재 여부를 확인하고 없으면 404를 반환한다.
- **R2** WHEN 요청 헤더 `X-Solana-Tx-Sig`가 존재하고 `LicenseService.is_licensed(asset, tx_sig)`가 참이면, the system SHALL 200과 원본 접근 정보(`download_url` 또는 서명 토큰)를 반환한다.
- **R3** IF 유효 라이선스가 없으면 THEN the system SHALL `X402Service.build_payment_required(asset)`로 402 상태와 헤더/본문(§아키텍처 3.1)을 반환한다.
- **R4** WHEN 402를 반환하면, the system SHALL 헤더 `X-402-Payment-Required: true`, `X-402-Negotiation-Endpoint`, `X-Solana-Pay-Address`, `X-Payment-Mint`를 포함한다.
- **R5** WHEN 402 본문을 생성하면, the system SHALL `accepts`(scheme/network/mint/pay_to/max_amount_required)와 `how_to_negotiate`(endpoint/method/required_payload/settle_endpoint)와 `preview_url`을 포함한다.
- **R5b** WHEN `pay_to`/`X-Solana-Pay-Address`를 설정하면, the system SHALL **결제 수취주소 해석 규칙**(아키텍처 §8)을 적용한다: `asset.parent_asset`가 있으면(2차 창작물) `PLATFORM_ESCROW_PUBKEY`, 없으면 `creator.wallet_address`. (S3 로열티 분배를 위해 2차 창작물은 반드시 에스크로로 라우팅)

### 클라이언트 판별
- **R6** WHEN 요청 헤더가 `X-Agent-Protocol: x402` 또는 `Accept: application/json`을 포함하면, the system SHALL 해당 요청을 **에이전트**로 분류한다.
- **R7** IF 요청이 에이전트가 아니고(브라우저 등) 라이선스가 없으면 THEN the system SHALL 402 대신 **Solana Pay 고정가(Buy-It-Now) 안내**(고정가 = `target_price_usdc`, QR/주소 포함)를 200으로 반환한다.
- **R8** WHERE 에이전트 요청인 경우, the system SHALL 402 경로로 처리한다.

### 관측/멱등
- **R9** WHEN 402가 반환되면, the system SHALL `EventRecorder.record("HTTP_402", {asset_id, buyer_hint})`를 기록한다.
- **R10** WHEN `X-Solana-Tx-Sig` 검증을 수행하면, the system SHALL 온체인 재검증 없이 우선 DB의 기발급 라이선스를 조회하고, 없을 때만 `SolanaService.verify_usdc_payment`를 호출한다(성능·비용 절감).

---

## 4. 인수조건
| AC | 조건 | 기대결과 |
|----|------|---------|
| AC-1 | 존재하지 않는 asset_id | 404 |
| AC-2 | 에이전트 요청(x402 헤더), 라이선스 없음 | 402 + 필수 헤더 4종 + 본문 accepts/how_to_negotiate |
| AC-3 | 에이전트 요청, 유효 라이선스 tx 보유 | 200 + download 정보 |
| AC-4 | 브라우저 요청(Accept: text/html), 라이선스 없음 | 200 + Solana Pay 고정가 안내 |
| AC-5 | 402 본문 스키마 | `preview_url`, `accepts[0].mint == USDC_MINT_ADDRESS` |
| AC-6 | 402 반환 시 | HTTP_402 이벤트 1건 기록 |
| AC-7 | 이미 발급된 라이선스 tx 재조회 | 온체인 재검증 호출 없음(mock 호출횟수 0) |
| AC-8 | 일반 자산 402 | `pay_to == creator.wallet_address` |
| AC-9 | 2차 창작물(parent 있음) 402 | `pay_to == PLATFORM_ESCROW_PUBKEY` |

---

## 5. TDD 테스트 명세 (RED 우선 목록)

### 단위 — 판별/빌더
- `test_classify_agent_by_x_agent_protocol_header`
- `test_classify_agent_by_accept_json`
- `test_classify_browser_by_accept_html`
- `test_build_payment_required_headers_present` — 헤더 4종.
- `test_build_payment_required_body_schema` — accepts/how_to_negotiate/preview_url.
- `test_build_payment_required_uses_target_price_as_max_amount`.
- `test_pay_to_is_creator_for_standalone_asset` (AC-8).
- `test_pay_to_is_escrow_for_secondary_asset` (AC-9, R5b).

### 통합 — `GET /ip/{id}`
- `test_get_unknown_asset_404`.
- `test_agent_without_license_gets_402` (AC-2).
- `test_agent_with_valid_license_gets_200` (AC-3, mock is_licensed=True).
- `test_browser_without_license_gets_solana_pay_fallback` (AC-4).
- `test_402_body_contains_usdc_mint` (AC-5).
- `test_402_records_event` (AC-6).
- `test_existing_license_skips_onchain_verify` (AC-7, mock 호출횟수 검증).

---

## 6. 엣지 케이스 / 가정
- `Accept`가 `*/*`인 애매한 요청 → 기본은 에이전트(402)로 처리(보수적 접근제어).
- 동일 tx_sig 다중 자산 재사용 시도 → 라이선스는 asset 단위로 검증.
- 미들웨어는 `/api/v1/ip/{id}` GET에만 개입, 등록/협상/정산 경로는 통과.
