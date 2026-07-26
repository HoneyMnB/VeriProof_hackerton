# 해커톤 1단계 자율 x402 결제

## 적용 범위

Buyer Agent B는 별도 사용자 계정이나 AP2 mandate 테이블 없이 테스트 전용
Solana Devnet 지갑을 프로세스 환경에서 읽는다. 모델은 개인키를 보거나 전달하지
않으며, 공식 x402 SVM 클라이언트가 다음 조건을 모두 만족할 때만 서명한다.

- x402 V2의 `exact` 결제
- `X402_NETWORK`와 정확히 같은 Solana Devnet
- `USDC_MINT_ADDRESS`와 정확히 같은 Devnet USDC
- 0보다 크고 `BUYER_MAX_PAYMENT_USDC` 이하인 단일 거래

현재 단계의 누적 지출 상한은 별도 DB 원장이 아니라 Buyer 테스트 지갑의 실제
USDC 잔액이다. 따라서 이 지갑에는 데모에서 위임할 총액만 충전한다.

## 로컬 설정

`.env`에 다음 값을 설정한다. 개인키는 저장소에 커밋하지 않는다.

```dotenv
X402_NETWORK=solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1
SOLANA_RPC_URL=https://api.devnet.solana.com
USDC_MINT_ADDRESS=4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU

BUYER_AUTONOMOUS_PAYMENT_ENABLED=true
BUYER_MAX_PAYMENT_USDC=1.000000
BUYER_WALLET_SECRET_KEY=<테스트 전용 Solana Base58 개인키>

DEMO_CREATOR_WALLET=<판매자 테스트 지갑 공개키>
```

Buyer와 Seller는 서로 다른 제어 가능한 Devnet 지갑을 사용한다. Buyer 지갑에는
수수료용 Devnet SOL과 결제용 Devnet USDC가 필요하다. Seller 지갑에는 Devnet
USDC associated token account가 있어야 하므로, 테스트 전 USDC를 한 번 수령해
계정을 준비한다.

- [Circle USDC 테스트넷 Faucet](https://faucet.circle.com/)
- [x402 구매자 빠른 시작](https://docs.x402.org/getting-started/quickstart-for-buyers)
- [x402 네트워크와 토큰 지원](https://docs.x402.org/core-concepts/network-and-token-support)

`seed_demo_catalog`를 사용하는 경우 기존 시스템 프로그램 주소가 아니라 위
`DEMO_CREATOR_WALLET`으로 데모 자산의 수취 지갑이 보정된다.

```bash
cd veriproof
python manage.py seed_demo_catalog
```

## 실행과 ADK UI 테스트

API와 Buyer Agent 이미지를 다시 만들고 실행한다.

```bash
./ctl.sh build api
./ctl.sh build buyer-agent
./ctl.sh api reload
./ctl.sh buyer-agent reload
```

ADK 개발 UI로 Buyer를 직접 대화 테스트하려면 호스트 가상환경에서 실행한다.

```bash
adk web agents --port 8002
```

Buyer Agent에서 다음 순서가 자연스럽게 이어지는 요청을 사용한다.

```text
공개된 바다 이미지를 찾아 commercial 라이선스를 협상해 줘.
최종 가격이 위임 한도 이하면 별도 승인 없이 구매까지 완료하고,
정산 트랜잭션과 라이선스 상태를 알려 줘.
```

성공 기준은 Agent의 자연어 답변이 아니라 `purchase_x402_asset` 도구 결과다.
결과가 `status=purchased`이고 디코딩된 `payment_response.success=true`이며,
Seller 응답의 라이선스가 활성 상태여야 한다. 정책 초과, 잘못된 mint, 서명 실패,
Facilitator 거절은 모두 `payment_rejected`로 끝나고 성공으로 표현되지 않는다.

## 보안 경계

이 설정은 로컬 해커톤 데모용이다. 자율 결제가 활성화된 Buyer Agent를 인증 없이
공개 Cloud Run에 배포하지 않는다. 다음 단계에서는 사용자별 위임 원장, 누적/일일
한도, 수취자 제한, 만료, 취소, 멱등성 키 및 Cloud Run 호출자 인증을 추가한다.
