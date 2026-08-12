# GCP KMS 및 Secret Manager 운영 배포 안내

VeriProof 운영 환경은 외부로 내보낼 수 없는 Cloud KMS
`EC_SIGN_ED25519` 키 두 개를 사용해 서명한다.

- `veriproof-platform-signing`: 플랫폼의 Solana Memo 및 인증서 서명
- `veriproof-buyer-signing`: Buyer Agent의 native SOL 트랜잭션 서명

Terraform은 CryptoKey와 Secret Manager 보안 비밀 컨테이너를 생성하고,
Cloud Run 서비스 계정에 최소 권한만 부여한다. 보안 비밀의 실제 값이 Terraform
state에 들어가지 않도록 Secret Manager 버전과 값은 Terraform으로 생성하지 않는다.

## 1. GCP 리소스 최초 구성

다음 명령으로 KMS, Secret Manager 및 IAM 리소스를 구성한다.

```bash
cd terraform
terraform init
terraform apply \
  -var="project_id=${GCP_PROJECT_ID}" \
  -var="region=${GCP_REGION}" \
  -var="cloud_run_service_account_email=${CLOUD_RUN_SERVICE_ACCOUNT}"
```

실행 전에 대상 프로젝트, 리전 및 Cloud Run 서비스 계정이 정확한지 반드시 확인한다.
특히 운영 프로젝트와 테스트 프로젝트를 혼동하지 않도록 주의한다.

## 2. Secret Manager 보안 비밀 등록

다음 보안 비밀의 새 버전을 보안이 확보된 운영자 터미널에서 등록한다.

- `veriproof-django-secret-key`
- `veriproof-wallet-encryption-key`
- `veriproof-postgres-password`
- `veriproof-buyer-wallet-secret-key`: 기존 USDC x402 경로에서만 사용

보안 비밀 값은 `env.prod`, Terraform 변수와 `tfvars`, GitHub Secrets, 셸 명령
기록, CI 로그·산출물, 이슈·PR·메신저·운영 문서에 입력하면 안 된다.

배포 워크플로는 보안 비밀 이름과 숫자형 버전을 참조한다. 키를 교체할 때는 다음
GitHub Environment Variable을 새 버전 번호로 갱신한다.

- `DJANGO_SECRET_VERSION`
- `WALLET_ENCRYPTION_SECRET_VERSION`
- `POSTGRES_PASSWORD_SECRET_VERSION`
- `BUYER_WALLET_SECRET_VERSION`
- `PLATFORM_KMS_KEY_VERSION`
- `BUYER_KMS_KEY_VERSION`

각 변수의 기본값은 `1`이다. Secret Manager 버전 변수에는 보안 비밀 값이 아니라
숫자형 버전 번호만 저장한다. KMS 버전 변수에도 CryptoKeyVersion의 숫자만 저장한다.
배포 워크플로는 이 값을 사용해 `/cryptoKeyVersions/<버전>`까지 포함한 리소스 이름을
Cloud Run에 주입한다.

native SOL 결제 경로는 `BUYER_KMS_KEY_NAME`을 우선 사용한다. 이 경로에서는 기존
`BUYER_WALLET_SECRET_KEY`가 사용되지 않는다. Buyer 지갑 보안 비밀은 USDC x402
호환 경로가 남아 있는 동안에만 유지한다.

## 3. 반드시 수행해야 하는 기존 키 교체

저장소에 과거 Buyer Devnet 개인키와 고정 wallet encryption key가 포함됐으므로 두
값은 모두 유출된 것으로 간주한다.

1. 기존 값을 Secret Manager에 그대로 복사하지 않는다.
2. 새 Buyer KMS 공개 주소를 확인하고 Devnet SOL을 충전한다.
3. 기존 USDC x402 Buyer 지갑을 새 지갑으로 교체한 후에만 Secret Manager 버전을
   생성한다.
4. 새 Fernet wallet encryption key를 생성해
   `veriproof-wallet-encryption-key`의 새 버전으로 등록한다.
5. 모든 활성 창작자 지갑 개인키를 VeriProof에서 다시 입력하고 검증하여 새 Fernet
   키로 재암호화한다.
6. 새 등록·협상·결제 데모가 정상 동작하는지 확인한 후 기존 Devnet 지갑의 권한과
   잔액을 제거한다.

기존 Fernet 키로 암호화된 `WalletConfiguration.private_address` 값은 새 Fernet
키로 복호화할 수 없다. Secret Manager 값만 교체하고 기존 ciphertext를 그대로 두면
창작물 등록 서명이 실패하므로 각 창작자 지갑을 반드시 다시 등록해야 한다.

## 4. KMS 키 교체

KMS CryptoKey에는 Terraform `prevent_destroy`가 적용돼 있다. 기존 CryptoKey를
삭제하지 말고 새 CryptoKeyVersion을 생성한다.

비대칭 서명 키는 암·복호화 키처럼 primary version을 선택하지 않는다. 애플리케이션에는
반드시 `/cryptoKeyVersions/<버전>`까지 포함한 리소스 이름을 설정한다. 키를 교체할 때는
GitHub Environment Variable `PLATFORM_KMS_KEY_VERSION` 또는 `BUYER_KMS_KEY_VERSION`을
새 버전 번호로 변경하고 Cloud Run을 다시 배포한다.

키 교체 후 다음 항목을 확인한다.

1. Cloud Run 서비스 계정이 새 CryptoKeyVersion으로 서명할 수 있는가
2. KMS 공개키로 변환된 Solana 주소가 예상 주소와 일치하는가
3. Buyer KMS 주소에 Devnet SOL 수수료와 결제 잔액이 충분한가
4. 플랫폼 KMS 주소에 Memo 트랜잭션 수수료가 충분한가
5. 이전 키 버전으로 새 서명이 발생하지 않는가

## 5. 배포 전·후 검증

배포 전에는 Terraform `validate`, Secret Manager 버전 존재 여부, Cloud Run 서비스
계정의 KMS·Secret Manager 권한, 운영 secret 누락 시 fail-closed 동작을 확인한다.
또한 `env.prod`, GitHub Actions 임시 env 파일 및 애플리케이션 로그에 원시 개인키가
존재하지 않는지 검사한다.

배포 후에는 실제 런타임 경로로 다음 E2E 검증을 수행한다.

1. 플랫폼 KMS를 이용한 등록 anchor 및 등록 인증서 Memo 발행
2. Buyer KMS를 이용한 native SOL 트랜잭션 서명과 Devnet 제출
3. 결제 트랜잭션 검증
4. 라이선스 및 다운로드 권한 발급
5. Cloud Audit Logs에서 예상 서비스 계정과 KMS 키 버전의 서명 기록 확인

검증에 실패하면 이전 애플리케이션 revision만 되돌리고 종료하지 않는다. Secret
Manager 버전, 명시된 KMS CryptoKeyVersion 및 Cloud Run revision이 서로 맞는 상태인지 함께
확인해 복구한다.
