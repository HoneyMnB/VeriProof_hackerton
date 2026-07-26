# VeriProof A2A Postman 테스트

## 가져오기

Postman의 `Import`에서 다음 파일을 가져온다.

1. `VeriProof-A2A.postman_collection.json`
2. `VeriProof-Local-A2A.postman_environment.json`

오른쪽 위 환경 선택에서 `VeriProof Local A2A`를 선택한다.

## 로컬 서비스 실행

```bash
./ctl.sh api reload
./ctl.sh buyer-agent reload
```

- Seller Agent A: `http://localhost:8000`
- Buyer Agent B: `http://localhost:8001`

## 실행 순서

컬렉션을 위에서 아래 순서로 실행한다.

1. Seller/Buyer Agent Card 조회
2. Seller Agent 직접 호출
3. Buyer에서 Seller로 위임하는 결과 없음 시나리오
4. Buyer에서 Seller로 위임하는 실제 자산 검색 시나리오
5. `asset_id`를 설정하고 x402 결제 조건 조회
6. 필요하면 가격 협상 후 저장된 `session_id`로 결제 조건 재조회
7. 외부 지갑이 만든 `PAYMENT-SIGNATURE`를 설정하고 동일 GET 재호출
8. 직전 Buyer Task 조회

Buyer 위임 요청은 Vertex Gemini를 실제 호출하므로 일반 HTTP 요청보다 오래 걸릴
수 있다.

## 판정 기준

- 모든 HTTP 응답이 200이어야 한다.
- JSON-RPC 응답에 `error`가 없어야 한다.
- Agent 실행 결과가 Task이면 `status.state`가 `completed`여야 한다.
- 실제 자산 검색 응답에는 검증된 카탈로그 데이터와 USDC 조건이 포함되어야 한다.
- 조건에 맞는 자산이 없으면 Agent가 자산이나 가격을 만들어내지 않아야 한다.
- 첫 자산 GET은 402와 `PAYMENT-REQUIRED`를 반환해야 한다.
- 결제 재요청은 200과 `PAYMENT-RESPONSE`를 반환해야 한다.

## x402 결제 서명 경계

Postman 컬렉션은 개인키를 취급하거나 결제 서명을 만들지 않는다. 먼저 402 응답의
`PAYMENT-REQUIRED` 값을 공식 x402 SVM 클라이언트에 전달하고, 사용자가 외부
지갑에서 결제를 승인해 생성한 Base64 `PAYMENT-SIGNATURE`만 컬렉션 변수에 넣는다.
로컬 `PAYMENT_VERIFIER=mock`은 실제 Devnet 거래를 허용하지 않지만, 표준 x402
경로는 Facilitator 검증 결과를 사용하므로 별도의 가짜 거래 서명으로 성공시킬 수
없다.

현재 A2A Task Store는 인메모리다. Buyer 컨테이너를 재시작하면
`last_task_id`로 이전 Task를 조회할 수 없다.
