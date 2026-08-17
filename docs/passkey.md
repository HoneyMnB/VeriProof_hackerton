# VeriProof Passkey 인증

## 1. 목적

Passkey는 비밀번호 대신 공개키 암호를 사용하는 로그인 자격증명이다. VeriProof는
W3C WebAuthn과 FIDO2 표준을 Django 인증·세션 위에 연결한다. 브라우저와 운영체제가
Windows Hello, Touch ID, Face ID, 기기 PIN 또는 보안키를 통해 사용자를 확인하고,
VeriProof 서버는 그 결과로 생성된 서명을 검증한다.

Passkey는 Solana 지갑 키가 아니다. VeriProof 계정 로그인과 소유자 인증서 조회 전
재인증에 사용하며, 온체인 등록·결제 서명은 기존 Solana/KMS 경로가 별도로 담당한다.

## 2. 저장되는 정보

`accounts.PasskeyCredential`은 다음 공개 자격증명 정보만 저장한다.

- Django 사용자 FK와 불투명 WebAuthn user handle
- 전역 고유 Credential ID
- 자격증명 공개키와 서명 카운터
- authenticator transport, 기기 유형 및 백업 여부
- 사용자가 지정한 기기 이름, 등록·최근 사용 시각

Passkey 개인키, 지문, 얼굴 정보와 기기 PIN은 서버로 전달되거나 DB에 저장되지
않는다. 개인키 사용은 브라우저·OS authenticator 내부에서만 일어난다.

## 3. 등록 흐름

1. 사용자가 기존 방식으로 로그인한다.
2. 계정 설정에서 Passkey 이름을 입력하고 등록을 시작한다.
3. `POST /accounts/passkeys/register/options/`가 5분 유효 challenge와 WebAuthn
   등록 옵션을 발급한다.
4. 브라우저가 `navigator.credentials.create()`를 호출하고 기기 인증을 요청한다.
5. `POST /accounts/passkeys/register/verify/`가 challenge, RP ID, origin, 사용자
   확인 및 attestation 결과를 검증한다.
6. 검증된 공개 자격증명만 `PasskeyCredential`에 저장한다.

계정당 여러 Passkey를 등록할 수 있으며 기존 Credential ID는 등록 옵션의 제외
목록에 포함된다.

## 4. 로그인 흐름

1. 로그인 화면에서 **Sign in with a passkey**를 선택한다.
2. `POST /accounts/passkeys/login/options/`가 discoverable credential용 challenge를
   발급한다.
3. 브라우저가 `navigator.credentials.get()`으로 사용 가능한 Passkey를 제시한다.
4. 서버는 Credential ID로 공개키를 찾고 challenge, RP ID, origin, user handle,
   사용자 확인, 서명과 sign counter를 검증한다.
5. 검증 성공 후 Django `login()`을 호출해 기존 세션을 발급한다.

Challenge는 ceremony 종류와 함께 Django 세션에 저장되며 5분 후 만료되고, 성공과
실패 여부와 관계없이 검증 요청에서 한 번 소비된다. 따라서 재전송할 수 없다.

## 5. Passkey 관리

로그인한 사용자는 계정 설정에서 자신의 `user_id`에 연결된 Passkey 목록과 기기명,
등록·최근 사용 시각을 조회하고 개별 자격증명을 삭제할 수 있다. 다른 사용자의
자격증명은 조회·삭제할 수 없으며, 비밀번호가 없는 계정은 마지막 Passkey를 삭제할
수 없다.

## 6. 인증서 조회 재인증

`/library`에서 인증서 보기를 선택하면 소유권 검사 뒤 별도 step-up 인증을 수행한다.
Passkey가 하나라도 등록된 계정은 해당 Passkey만 사용할 수 있고, 등록된 Passkey가
없는 계정은 현재 비밀번호를 확인한다. 성공 권한은 해당 사용자·작품에만 적용되며
5분 뒤 만료된다. PDF 응답은 `private, no-store`로 캐시를 차단한다.

## 7. 도메인 설정

WebAuthn 자격증명은 RP ID와 origin에 묶인다. 로컬에서는 환경값이 비어 있으면 현재
요청의 `localhost` 호스트를 사용한다. 운영에서는 최종 HTTPS 도메인을 명시한다.

```env
PASSKEY_RP_ID=app.example.com
PASSKEY_RP_NAME=VeriProof
PASSKEY_ORIGINS=https://app.example.com
```

- `PASSKEY_RP_ID`에는 scheme이나 경로를 넣지 않는다.
- `PASSKEY_ORIGINS`에는 정확한 HTTPS origin을 넣는다. 복수 origin은 쉼표로 구분한다.
- Cloud Run 임시 주소와 최종 서비스 도메인을 혼용하면 서로 다른 Passkey 범위가 된다.
- Reverse proxy 환경에서는 Django가 `X-Forwarded-Proto: https`를 신뢰하도록 현재
  `SECURE_PROXY_SSL_HEADER` 설정을 유지한다.

## 8. 기존 인증과 복구

현재 이메일·비밀번호 가입과 로그인은 그대로 유지한다. 이는 기존 사용자 호환성과
Passkey 기기 분실 시 계정 복구 수단이다. Passkey 인증 성공 뒤에도 권한 모델과
로그인 상태는 Django 세션을 그대로 사용하므로 기존 `login_required`, CSRF 및
소유권 검사가 동일하게 적용된다.

## 9. 관련 코드와 운영 절차

- 모델: `apps/accounts/models.py::PasskeyCredential`
- ceremony와 검증: `apps/accounts/passkeys.py`
- HTTP 경계: `apps/accounts/views_passkey.py`
- 인증서 step-up: `apps/ip/views_web.py`
- 브라우저 코드: `static/js/passkeys.js`
- DB migration: `accounts.0006_passkeycredential`

배포 전 다음을 수행한다.

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check
```

실제 HTTPS 주소에서 `기존 로그인 → Passkey 등록 → 로그아웃 → Passkey 로그인`을
검증한다. RP ID나 origin 변경은 기존 Passkey를 무효화할 수 있으므로 운영 도메인
확정 후 변경하지 않는 것이 원칙이다.
