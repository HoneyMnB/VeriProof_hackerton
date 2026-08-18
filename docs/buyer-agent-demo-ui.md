# Buyer Agent 데모 UI 설계

## 목적

ADK-UI를 운영·개발 도구로 유지하면서, 발표에서는 구매자의 요청과 실제 Tool 실행 과정을 대화 안에서 설명할 수 있는 전용 화면을 제공한다. 화면은 채팅에 집중하고 부가 패널은 두지 않는다.

접속 경로: `https://<buyer-agent-service>/demo/`

## 핵심 사용자 흐름

1. 구매자가 필요한 에셋을 자연어로 입력한다.
2. Buyer Agent가 Seller Agent에 탐색을 위임하고 검증된 결과를 반환한다.
3. 각 답변 아래에서 실제 Tool 호출명, 입력, 응답과 완료 상태를 확인한다.
4. 사용자가 결과를 검토한 뒤 후속 메시지로 협상 또는 구매를 명시적으로 지시한다.

## 화면 정보 구조

### 채팅 영역

- 자연어 Buyer Brief 입력
- 대화 기록과 에이전트 작업 중 로딩 애니메이션
- 발표 시작을 돕는 예시 프롬프트. 예시는 입력만 채우며 결과를 생성하지 않는다.
- 각 Buyer Agent 답변 바로 아래 접을 수 있는 Execution 영역
- Tool 호출 인자와 Tool 응답은 사용자가 펼쳐서 확인
- 사용자 메시지는 사람 아이콘, Buyer/Seller 메시지는 색상이 다른 로봇 아이콘으로 구분
- 실제 `veriproof_seller_agent` Tool의 요청과 응답을 Buyer → Seller, Seller → Buyer A2A 대화로 표시

### 구매 모드

- `Agent buys`가 기본값이며 기존 Buyer Agent 위임 한도 안에서 구매 Tool을 직접 실행한다.
- `Ask approval`은 x402와 native SOL 구매 Tool 모두에 서버 측 승인 게이트를 적용한다.
- 승인 모드에서는 구매 Tool이 `approval_required`를 반환하고 결제 서명 전에 멈춘다.
- UI는 pending asset과 결제수단을 표시하는 모달을 열고 승인 또는 거절을 받는다.
- 승인은 동일 세션·자산·결제수단에만 일회성으로 적용되며 사용 후 즉시 소진된다.
- 거절하면 `payment_declined`로 종료되고 결제 클라이언트를 생성하지 않는다.

## 프로토콜 처리

화면은 동일 오리진의 `/demo/api/chat`에 요청하고 실제 ADK Runner 이벤트를 SSE로 수신한다. Tool 호출과 응답은 `Event.get_function_calls()` 및 `Event.get_function_responses()`에서만 생성한다.

- Tool 이름, 호출 ID, 실제 입력과 실제 응답을 순서대로 표시
- private key, signature, token, credential 등 민감 키는 서버에서 재귀적으로 마스킹
- Tool 데이터의 크기와 깊이를 제한해 UI 및 로그 과다 노출 방지
- 모델의 비공개 `thought` 필드는 전송하거나 표시하지 않음
- 최종 사용자 응답만 채팅 본문에 표시
- 실패 시 실제 실패 상태를 표시하고 성공으로 위장하지 않음
- Tool 응답의 `status=approval_required`일 때만 승인 모달 표시

Seller Agent의 현재 공개 A2A Skill은 에셋 탐색만 지원한다. `negotiate_license`, 결제조건 조회와 구매는 Buyer Agent가 호출하는 REST/x402 Tool이므로 Execution에는 표시하되 A2A 대화로 표기하지 않는다.

## 시각·반응형 원칙

- 기존 VeriProof 라이브 데모와 어울리는 뉴트럴 배경, 라임 상태색, 모노스페이스 메타데이터 사용
- 데스크톱과 모바일 모두 하나의 채팅 열만 사용
- Execution은 해당 답변 바로 아래에 배치하고 기본적으로 펼쳐 진행 상황을 노출
- Tool 입력과 응답 원문은 `view data`를 눌렀을 때만 확장
- 로딩 중 점 애니메이션 제공, `prefers-reduced-motion` 지원
- 텍스트 입력 UI에 브라우저 기본 파란 포커스 링을 노출하지 않고 중립 포커스 스타일 적용

## 발표 시나리오

1. `Sea image · under 10 USDC` 예시를 선택한다.
2. 요청 문장에 USDC x402, 예산, 사용 목적, 구매 보류 조건이 포함됐는지 확인한다.
3. 메시지를 보내고 Execution에서 Seller Agent 호출과 응답을 짚는다.
4. 이어서 x402 결제 조건 조회 Tool이 호출되는 과정을 보여준다.
5. Buyer Agent의 최종 답변과 실제 Tool 결과가 일치하는지 설명한다.
6. 조건이 맞으면 후속 메시지로 협상을 지시하고, 구매는 별도 메시지로 명시적으로 승인한다.

실거래 시연 전에는 Buyer Agent의 위임 한도, Devnet 지갑 잔액, Seller API URL과 정산 수단 환경 설정을 별도로 확인해야 한다.
