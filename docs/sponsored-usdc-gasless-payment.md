# 플랫폼 수수료 부담 USDC 브라우저 결제

## 목적

공개 브라우저 결제는 Solana의 USDC를 사용한다. 구매자는 SPL Token 전송만 승인하고, VeriProof 전용 sponsor가 SOL 네트워크 수수료 및 필요한 경우 수취인의 Associated Token Account(ATA) 생성 비용을 부담한다. 브라우저 결제 경로에서는 더 이상 native SOL Solana Pay를 사용하지 않는다.

## 트랜잭션 및 정산 흐름

1. 인증된 구매자가 Phantom을 연결하고 공개 지갑 주소를 포함해 `POST /api/v1/ip/{asset_id}/sponsored-usdc`를 호출한다.
2. 서버는 고정된 자산 USDC 가격, 구매자, 수취인, 고유 memo 및 만료 시각을 담은 5분짜리 `SponsoredPaymentIntent`를 생성한다.
3. 서버는 하나의 legacy Solana 트랜잭션을 구성한다. 내용은 멱등적인 수취인 USDC ATA 생성, 설정된 USDC mint에 대한 `transferChecked`, intent memo다. 전용 sponsor가 fee payer가 되어 Cloud KMS 또는 로컬 Devnet 키로 먼저 서명한다.
4. Phantom은 구매자의 token-authority 서명만 추가하고 트랜잭션을 제출한다. 구매자는 개인키를 VeriProof에 제공하지 않으며 SOL 보유도 필요 없다.
5. `POST /api/v1/ip/{asset_id}/sponsored-usdc/settle`는 intent ID와 거래 서명을 받는다. 서버는 거래가 `finalized` 상태인지 확인하고 mint, 수취인, 최소 단위 금액, memo, 송신자를 검증한 뒤 기존 정산 파이프라인으로 라이선스와 기한이 있는 다운로드 URL을 발급한다.

모든 instruction은 원자적으로 실행된다. ATA 누락, USDC 잔액 부족, 구매자 서명 오류 또는 다른 instruction 실패가 발생하면 USDC 전송과 라이선스 발급이 모두 일어나지 않는다.

## 필수 설정

```dotenv
USDC_MINT_ADDRESS=<설정한 클러스터와 일치하는 정확한 mint 주소>
PAYMENT_SPONSOR_PUBKEY=<전용 sponsor 공개키>
PAYMENT_SPONSOR_KMS_KEY_NAME=<운영 환경의 버전 고정 Cloud KMS Ed25519 키>
# Devnet 전용 폴백이며 운영 환경에서는 설정하지 않는다.
PAYMENT_SPONSOR_SECRET_KEY=
SPONSORED_PAYMENT_TTL_SECONDS=300
```

`PAYMENT_SPONSOR_PUBKEY`는 설정된 서명자의 공개키와 반드시 일치해야 한다. sponsor는 트랜잭션 수수료와 잠재적 ATA rent를 감당할 SOL을 보유해야 하지만, 구매자·창작자·escrow 보관 키와 분리돼야 한다. 운영 환경에서는 버전이 고정된 `EC_SIGN_ED25519` Cloud KMS 키를 사용하며, 로컬 secret 폴백은 Devnet 전용이다.

## 보안 및 운영

- 서버가 자산에서 수취인과 금액을 결정하며, 브라우저가 이 값을 전달하거나 변경할 수 없다.
- intent는 인증 사용자, 구매 지갑, 자산, USDC 금액, 고유 memo, 만료 시각에 묶인다.
- 거래 서명은 intent와 License 계층 모두에서 고유하다. 같은 정산 요청은 기존 라이선스를 반환한다.
- 클라이언트의 성공 응답을 신뢰하지 않는다. 라이선스 발급 전 finalized 온체인 상태와 정확한 USDC 전송 조건을 확인한다.
- sponsor API 장애 시 실제 unavailable 응답을 반환하며, SOL 결제나 가짜 성공으로 fallback하지 않는다.
- 공개 sponsor 운영 전 계정·지갑·IP별 rate limit을 적용하고, sponsor SOL 잔액, 요청량, finalization 실패율, intent 만료를 모니터링·알림 처리한다.

## 배포 체크리스트

1. Secret Manager에 클러스터와 일치하는 USDC mint, sponsor 공개키 및 KMS 키를 설정한다.
2. sponsor에만 제한된 SOL 운영 잔액을 충전하고, 공개키가 `PAYMENT_SPONSOR_PUBKEY`와 일치하는지 확인한다.
3. `ip.0019_sponsoredpaymentintent` migration을 적용한다.
4. 집중 결제 테스트 및 Devnet 지갑 테스트를 수행한다. 구매자 SOL 없음, 수취인 ATA 없음, USDC 부족, 서명 거절, intent 만료, 중복 정산, finalized 성공을 모두 포함한다.
5. 신뢰되지 않은 트래픽에 결제를 공개하기 전에 rate limit과 모니터링을 활성화한다.

## API 응답

- `201`: 부분 서명된 Base64 트랜잭션, intent ID, USDC 금액, 만료 시각
- `202`: 제출된 거래가 아직 finalized되지 않음. 클라이언트가 짧은 간격으로 조회
- `200 PAID`: 라이선스 및 만료 다운로드 URL 발급 완료
- `409`: intent 만료 또는 거래 서명 충돌
- `422`: 거래가 현재 intent와 정확히 일치하지 않음
- `503`: sponsor, RPC 또는 정산 인프라를 사용할 수 없음
