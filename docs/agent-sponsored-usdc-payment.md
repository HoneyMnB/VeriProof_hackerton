# Buyer Agent sponsor-paid USDC 결제

## 목적

Buyer Agent는 Phantom이나 브라우저 지갑을 사용하지 않고, 전용 Buyer KMS
Ed25519 지갑으로 USDC 전송만 서명한다. VeriProof sponsor 지갑은 Solana
네트워크 수수료와 수취인의 USDC ATA 생성비만 부담한다. Buyer Agent의 개인키는
대화, 도구 인자, 로그에 전달하지 않는다.

이 경로는 브라우저의 `/sponsored-usdc` Phantom 결제와 분리되어 있다. Agent는
`/agent-sponsored-usdc` endpoint만 사용하며, Browser intent를 정산하거나 그 반대로
정산할 수 없다.

## 결제 흐름

1. Buyer Agent가 `POST /api/v1/ip/{asset_id}/agent-sponsored-usdc`를 Bearer token과 함께 호출한다.
2. API는 서버 설정의 Buyer 공개키를 사용한다. 요청이 보낸 지갑이나 사용자는 받지 않는다.
3. API는 `currency=USDC` 및 `target_amount`가 양수인 공개 자산만 대상으로 고정 금액, 수취인, memo, 5분 만료 intent를 만든다.
4. Sponsor가 ATA 생성, `transferChecked`, memo가 포함된 legacy transaction에 먼저 서명한다.
5. Buyer Agent는 응답의 금액·mint·Buyer 공개키를 정책과 비교하고, transaction을 같은 instruction으로 재구성한다. 메시지가 일치할 때만 Buyer KMS로 두 번째 서명을 추가해 RPC에 제출한다.
6. Agent가 `POST /agent-sponsored-usdc/settle`을 호출한다. API는 finalized 상태, USDC mint, 정확한 최소 단위 금액, 수취인, memo, 송신자를 모두 검증하고 기존 settlement pipeline으로 license와 기한 제한 download URL을 발급한다.

## 구성

API와 Buyer Agent에 공통으로 다음 값을 Secret Manager 등 안전한 배포 설정으로 주입한다.

```dotenv
USDC_MINT_ADDRESS=<cluster에 맞는 USDC mint>
SOLANA_RPC_URL=<cluster에 맞는 RPC>

PAYMENT_SPONSOR_PUBKEY=<sponsor 공개키>
PAYMENT_SPONSOR_KMS_KEY_NAME=projects/.../cryptoKeyVersions/1
SPONSORED_PAYMENT_TTL_SECONDS=300

AGENT_SPONSORED_PAYMENT_TOKEN=<32바이트 이상 난수>
AGENT_SPONSORED_PAYMENT_BUYER_PUBKEY=<BUYER_KMS_KEY_NAME에서 얻은 공개키>

BUYER_AUTONOMOUS_SPONSORED_USDC_ENABLED=true
BUYER_MAX_SPONSORED_USDC=1.000000
BUYER_KMS_KEY_NAME=projects/.../cryptoKeyVersions/1
```

로컬 Devnet에서만 `BUYER_WALLET_SECRET_KEY`와
`PAYMENT_SPONSOR_SECRET_KEY`를 KMS 대체로 사용할 수 있다. 운영에서는 둘 다
사용하지 않고 버전이 고정된 `EC_SIGN_ED25519` Cloud KMS 키를 사용한다.

`AGENT_SPONSORED_PAYMENT_BUYER_PUBKEY`는 Buyer KMS signer 공개키와 정확히
일치해야 한다. Agent 결제 intent와 라이선스는 Django 사용자 계정이 아니라 Buyer
지갑과 온체인 거래 서명으로 식별된다.

## 정책과 보안 경계

- Agent는 `BUYER_MAX_SPONSORED_USDC`를 초과하거나 6자리 USDC 최소 단위가 아닌 금액에는 서명하지 않는다.
- Agent는 설정된 mint, KMS 공개키, sponsor 서명이 포함된 canonical transaction만 서명한다. API 응답의 금액 문자열만 신뢰하지 않는다.
- API는 bearer token을 상수 시간 비교하고, Buyer wallet을 환경 설정에서만 결정한다.
- Token은 Browser에 절대 제공하지 않는다. `/agent-sponsored-usdc*`는 bearer token으로 보호되므로 CSRF 예외가 적용된다.
- sponsor에는 ATA rent와 network fee를 감당할 SOL만 보관한다. Buyer USDC와 Creator 수취 지갑·escrow 키를 섞지 않는다.
- 운영 전 agent identity별 rate limit, 거래당·일일·누적 한도 원장, sponsor SOL 잔액·결제 실패·intent 만료 알림을 활성화한다. 현재 구현은 거래당 한도만 강제한다.

## API 결과

- `201`: sponsor 부분서명 transaction과 intent 정보
- `200 PAID`: finalized 검증 및 license 발급 완료
- `202 PENDING`: transaction이 아직 finalized되지 않음
- `401`: Agent bearer token 없음 또는 불일치
- `409`: 만료·거래 서명 충돌·USDC 가격 미설정
- `422`: intent와 거래가 일치하지 않음
- `503`: sponsor/KMS/RPC/settlement 또는 Agent Buyer 공개키 설정을 사용할 수 없음

## 배포 및 검증

1. migration `ip.0022_sponsoredpaymentintent_buyer_user_nullable`까지 적용한다.
2. Buyer KMS 키, sponsor KMS 키, 공통 bearer token을 설정한다.
3. Buyer KMS 공개키를 `AGENT_SPONSORED_PAYMENT_BUYER_PUBKEY`에 넣고, Buyer 지갑에는 USDC만, sponsor에는 SOL만 충전한다.
4. Devnet에서 Buyer SOL 0·Buyer USDC 충분·수취인 ATA 없음의 성공 결제를 확인한다.
5. 한도 초과, mint 불일치, canonical transaction 변조, 서명 실패, USDC 부족, intent 만료, 중복 정산, RPC/sponsor 장애를 검증한다.

현재 단위 검증은 Agent가 canonical sponsor transaction만 서명하고 amount 변조를 거절하는 것을 포함한다. 실제 Devnet E2E는 KMS 권한, 지갑 잔액, 실행 중인 PostgreSQL/RPC가 필요하다.
