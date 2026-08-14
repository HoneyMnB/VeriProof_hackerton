# SPEC-009 — 발표 기준 데모 프로토콜 정합성

- 상태: Draft
- 기준: `VeriProof_AI_Rights_and_Payment_Protocol.pdf`
- 관련: SPEC-001~006, SPEC-008
- 검증: TDD 후 Solana Devnet E2E

## 1. 목표

발표와 실제 런타임을 다음 단일 흐름으로 일치시킨다.

`Passkey 로그인 → 작품 등록 → 지문 생성 → Solana 앵커링 → AI 협상 → x402 USDC Gasless 결제 → 라이선스·인증서 → 창작자 정산 → Firestore 시각화`

온체인 앵커링은 콘텐츠 지문과 등록 시점에 대한 검증 가능한 기록으로 설명하며, 법적 소유권을 단독 확정하는 기능으로 표현하지 않는다.

## 2. 설계 결정

| 항목 | 결정 |
|---|---|
| 표시·협상·결제 통화 | USDC |
| 네트워크 | Solana Devnet |
| 가스비 | Facilitator 또는 VeriProof sponsor가 SOL로 부담 |
| 금액 | Decimal, USDC 6자리 정밀도 |
| 결제 검증 | recipient, mint, atomic amount, commitment 모두 검증 |
| 기존 SOL 데이터 | 보존하며 자동 환산하지 않음 |
| 신규 SOL 경로 | 데모 기본 경로에서 비활성화 |
| Passkey | Django 로그인 전용, 지갑 서명과 분리 |
| SSOT | PostgreSQL License/Settlement, Firestore는 읽기용 미러 |

## 3. 우선순위와 범위

### P0 — 본선 데모 필수

1. 등록·수정·카탈로그·협상·Agent 계약을 USDC로 통일한다.
2. 공식 x402 V2 USDC 요구, 검증, 정산을 정상화한다.
3. 구매자가 SOL을 준비하지 않는 Gasless 결제를 Devnet에서 입증한다.
4. 결제 후 라이선스와 온체인 인증서를 멱등하게 발급한다.
5. Firestore에서 실제 프로토콜 이벤트를 시각화한다.
6. 실패·재시도·중복 요청으로 중복 라이선스나 정산이 생기지 않게 한다.

### P1 — 발표 주장 완성

1. 플랫폼 12%, 창작자 88%의 실제 SPL USDC 분배
2. 2차 창작 원작자 로열티의 실제 USDC 분배
3. 파일 또는 SHA-256 기반 공개 등록 증명 API
4. 월 $1.9 구독의 실제 결제 검증과 활성화

### 제외

- Mainnet, SOL/USDC 자동 환전, Passkey의 지갑 키 사용
- 온체인 기록만을 이용한 법적 소유권 자동 판정
- 실제 발생하지 않은 이벤트·정산 결과를 고정 데이터로 표시하는 기능

## 4. 요구사항

### 4.1 USDC 가격과 기존 SOL 데이터

- **R1** 신규 등록과 판매 조건 수정은 `min_price_usdc`, `target_price_usdc`를 가격 원장으로 사용한다.
- **R2** USDC는 양의 유한 Decimal이며 소수점 6자리까지만 허용한다.
- **R3** 등록, 라이브러리, 카탈로그, 상세, Agent 응답은 통화를 명시하고 USDC로 표시한다.
- **R4** 기존 SOL 필드와 라이선스는 감사 목적으로 보존한다.
- **R5** SOL 가격을 환율 근거 없이 USDC로 변환하거나 fallback하지 않는다.
- **R6** USDC 가격이 없는 기존 SOL 자산은 거래를 차단하고 가격 재입력을 요구한다.

### 4.2 AI 협상

- **R7** 구매자 `offer_usdc`와 자산의 USDC 최저가·목표가만 사용한다.
- **R8** AI 응답은 창작자의 최저가보다 낮은 ACCEPT를 확정할 수 없다.
- **R9** ACCEPT 시 `final_price_usdc`, 수취인, network, USDC mint를 확정한다.
- **R10** offer/counter/final price, round, reason, session ID를 이벤트로 기록한다.
- **R11** Seller Agent, Buyer Agent, Sandbox가 동일한 USDC tool schema를 사용한다.

### 4.3 x402 USDC

- **R12** Payment Required는 Devnet, mint, recipient, amount, session을 포함한다.
- **R13** `amount_usdc × 1,000,000`을 정수 문자열로 보낸다. 예: `1.8 → "1800000"`.
- **R14** float 변환 없이 Decimal로 최소단위 정합성을 검증한다.
- **R15** Facilitator verify 또는 settle 실패 시 License를 발급하지 않는다.
- **R16** 동일 payment tx 재전송은 기존 License를 반환하며 중복 정산하지 않는다.
- **R17** 성공 결과는 payment tx, amount, currency, buyer, asset, session, license, certificate 상태를 포함한다.

### 4.4 Gasless

- **R18** Buyer는 Devnet USDC만 보유한 상태에서 거래할 수 있어야 한다.
- **R19** Facilitator 또는 sponsor가 SOL 네트워크 수수료를 부담한다.
- **R20** 이벤트에는 `fee_sponsored`, sponsor 유형, network를 기록하되 비밀정보는 제외한다.
- **R21** sponsor 실패 시 명시적으로 실패하며 일반 SOL 결제로 조용히 전환하지 않는다.
- **R22** UI는 USDC 결제와 SOL 가스비 후원을 별개로 표시한다.

### 4.5 라이선스·인증서

- **R23** 검증된 결제 후에만 License를 생성한다.
- **R24** `payment_tx_sig`를 유일한 멱등성 키로 사용한다.
- **R25** License에 Solana Memo 인증서 tx를 연결한다.
- **R26** 인증서 발급 실패 시 결제와 License를 보존하고 재시도 가능하게 한다.
- **R27** 원본 접근은 해당 License와 만료형 토큰으로 제한한다.

### 4.6 88/12 정산과 로열티

- **R28** 수수료 정책 활성화 시 총액의 12%와 88%를 USDC 최소단위 정수로 계산한다.
- **R29** 분배 합은 결제 총액과 정확히 같아야 한다.
- **R30** 실제 SPL USDC 송금 서명 없이 `settled`로 표시하지 않는다.
- **R31** 일부 실패 시 성공 leg는 보존하고 실패 leg만 같은 멱등성 키로 재시도한다.
- **R32** 2차 창작은 `총액 → 플랫폼 12% → 잔여 88%를 원작자/2차 창작자에게 배분`하는 정책을 기본안으로 한다.
- **R33** R32는 구현 전에 제품 정책으로 확정하며 런타임별로 다른 계산 순서를 허용하지 않는다.

### 4.7 등록 증명 API

- **R34** SHA-256 또는 업로드 파일을 입력받는다.
- **R35** 파일 입력은 서버가 SHA-256을 직접 계산한다.
- **R36** 일치 시 asset ID, hash, 등록 시각, anchor tx, 인증서 tx, Explorer URL을 반환한다.
- **R37** 지각 해시 유사성은 참고 정보이며 동일성·소유권 판정으로 표현하지 않는다.
- **R38** 비공개 자산의 원본 URL과 개인정보를 권한 없이 노출하지 않는다.

### 4.8 Passkey

- **R39** Passkey는 기존 Django 사용자와 세션에 연결한다.
- **R40** Passkey 성공을 지갑 결제 승인으로 간주하지 않는다.
- **R41** 운영 RP ID와 origin을 배포 설정에서 고정한다.
- **R42** 복구 경로를 유지하고 Passkey 추가·삭제를 감사 가능하게 한다.

### 4.9 Firestore 라이브 데모

- **R43** 실제 이벤트를 `REGISTERED → HASHED → ANCHORED → NEGOTIATING → ACCEPTED → PAYMENT_REQUIRED → PAYMENT_VERIFIED → LICENSED → CERT_ISSUED → DISTRIBUTED` 순으로 표시한다.
- **R44** 발생하지 않은 이벤트를 완료로 미리 표시하지 않는다.
- **R45** skeleton loading과 empty/error/reconnecting 상태를 구분한다.
- **R46** 결제에는 USDC amount, mint 축약값, payment tx, sponsor 여부를 표시한다.
- **R47** 실제 분배 tx가 있을 때만 88/12 또는 로열티 완료를 표시한다.
- **R48** Firestore를 권한 판정이나 정산 SSOT로 사용하지 않는다.
- **R49** 사용자가 허용된 자산·세션 이벤트만 조회할 수 있게 한다.
- **R49a** 등록 흐름은 asset UUID, A2A 거래 흐름은 asset·buyer agent에서 파생한
  `correlation_id`로 그룹화한다.
- **R49b** Firestore server listener를 인증된 Django SSE로 중계하고, 브라우저는
  SSE 장애 시에만 snapshot API polling으로 전환한다.

### 4.10 구독

- **R50** `$1.9/month` 발표 시 결제 tx 검증, 기간, 상태, 등록 한도를 실제 연결한다.
- **R51** 결제 검증 없이 활성 구독을 생성하지 않는다.
- **R52** 구현하지 않으면 UI와 발표에서 로드맵으로 명시한다.

## 5. 데이터 변경 원칙

1. 기존 USDC와 SOL 원장은 모두 보존한다.
2. 신규 데모 자산은 USDC 가격을 명시적으로 입력한다.
3. 필수 필드 변경은 Django migration으로 버저닝하고 `db_reference.md`에 기록한다.
4. migration에서 SOL→USDC 환산을 수행하지 않는다.
5. 기존 SOL 자산은 `USDC 가격 재입력 완료` 또는 `거래 불가`로 명확히 구분한다.
6. API 응답에 currency를 명시해 구버전 클라이언트의 단위 혼동을 방지한다.

## 6. 보안·장애 원칙

- 운영 서명키는 KMS/Secret Manager 밖에 저장하지 않는다.
- 결제는 recipient, mint, amount, commitment, 중복 tx를 모두 확인한다.
- 로그와 Firestore에 개인키, Passkey challenge, 원본 URL을 기록하지 않는다.
- RPC, Facilitator, Firestore 장애는 fail-closed로 처리한다.
- 재시도는 동일 idempotency key를 사용하고 완료된 송금을 다시 실행하지 않는다.

## 7. 인수 조건

| AC | 조건 | 기대 결과 |
|---|---|---|
| AC-1 | 신규 작품 1.5/3.0 입력 | DB·UI·API 모두 USDC |
| AC-2 | SOL 가격만 있는 기존 자산 | 거래 차단, USDC 재입력 안내 |
| AC-3 | 1.8 USDC 결제 요구 | atomic amount `1800000` |
| AC-4 | 잘못된 mint/recipient/amount | 결제 거부, License 없음 |
| AC-5 | 동일 tx 재전송 | License 1개, 정산 1회 |
| AC-6 | Buyer SOL=0, USDC 충분 | Gasless Devnet 결제 성공 |
| AC-7 | sponsor 불가 | 명시적 실패, SOL fallback 없음 |
| AC-8 | 결제 성공 | License와 download token 발급 |
| AC-9 | 인증서 실패 | License 유지, 재시도 가능 |
| AC-10 | 10 USDC, 12% 정책 | platform 1.2, creator pool 8.8 |
| AC-11 | USDC 분배 실패 | 해당 leg failed, 완료 표시 없음 |
| AC-12 | 파일 검증 | 서버 계산 hash 기반 결과 반환 |
| AC-13 | Passkey 로그인 | Django 세션 생성, 결제 승인은 별도 |
| AC-14 | 전체 E2E | 실제 이벤트·tx가 Firestore 타임라인에 표시 |
| AC-15 | 권한 없는 접근 | 타 사용자 이벤트 노출 없음 |

## 8. 검증 계획

### 단위

- Decimal ↔ USDC atomic 변환
- 협상 최저가 불변식과 라운드 제한
- 88/12 및 로열티 최소단위 합계
- 멱등성·상태 전이·Firestore 공개 필드 필터

### 통합

- 등록 → 협상 → x402 payment required
- Facilitator verify/settle 성공·실패
- 결제 → License → certificate → download
- 일부 분배 실패와 재시도
- Passkey 사용자와 자산 접근 제어

### Devnet E2E

1. Passkey 로그인
2. USDC 가격 작품 등록 및 anchor tx 확인
3. 외부 Buyer Agent 협상과 ACCEPT
4. SOL 없는 Buyer의 USDC x402 결제
5. payment tx, License, certificate tx, 다운로드 확인
6. 범위에 포함되면 88/12 SPL transfer tx 확인
7. Firestore가 같은 거래의 실제 이벤트만 표시하는지 확인

## 9. 구현 순서와 완료 기준

| 순서 | 작업 | 완료 기준 |
|---|---|---|
| 1 | USDC 도메인 복구 | 등록·카탈로그·협상·Agent 테스트 통과 |
| 2 | x402 금액·검증 수정 | 단위·Facilitator 통합 테스트 통과 |
| 3 | Gasless 연결 | SOL 없는 Buyer Devnet E2E 성공 |
| 4 | 라이선스 회귀 | 발급·인증서·중복 방지 검증 |
| 5 | Firestore 보강 | 실제 타임라인·오류·권한 검증 |
| 6 | 88/12·로열티 | 실제 SPL USDC tx와 재시도 검증 |
| 7 | 공개 검증 API | 파일/hash·정보 노출 테스트 통과 |
| 8 | 구독 결제 | 포함 시 결제 기반 활성화 검증 |

## 10. 발표 문구 게이트

| 문구 | 사용 조건 |
|---|---|
| `USDC로 즉시 결제` | Devnet x402 E2E 성공 |
| `Gasless` | Buyer SOL=0에서 실제 성공 |
| `창작자에게 88% 즉시 정산` | 실제 창작자 SPL USDC tx 확인 |
| `로열티 자동 분배` | 원작자·2차 창작자 tx 확인 |
| `무료 검증 API` | 공개 API 보안 테스트 통과 |
| `온체인 권리 증명` | `콘텐츠 지문 및 등록 시점 증명`으로 한정 |
| `전 구간 실동작` | 등록부터 시각화까지 단일 E2E 통과 |
