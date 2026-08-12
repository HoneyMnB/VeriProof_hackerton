"""Cloud KMS Ed25519 signer used by the independently deployed Buyer Agent."""

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from solders.pubkey import Pubkey

from .policy import PaymentConfigurationError, PaymentExecutionError


class KmsEd25519Signer:
    """Sign Solana message bytes without exporting the private key from KMS."""

    def __init__(self, key_name: str, client: Any | None = None) -> None:
        self._key_name = key_name.strip().rstrip("/")
        self._client_instance = client
        self._version_name: str | None = None
        self._public_key: Ed25519PublicKey | None = None

    def public_key(self) -> Pubkey:
        raw = self._load_public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return Pubkey.from_bytes(raw)

    def sign(self, message: bytes) -> bytes:
        if not message:
            raise PaymentExecutionError("KMS 서명 메시지가 비어 있습니다.")
        public_key = self._load_public_key()
        try:
            response = self._client().asymmetric_sign(
                request={"name": self._key_version(), "data": message}
            )
            signature = bytes(response.signature)
            public_key.verify(signature, message)
        except Exception as exc:
            raise PaymentExecutionError("Cloud KMS 결제 서명에 실패했습니다.") from exc
        if len(signature) != 64:
            raise PaymentExecutionError("Cloud KMS가 Ed25519 서명을 반환하지 않았습니다.")
        return signature

    def _client(self):
        if self._client_instance is not None:
            return self._client_instance
        try:
            from google.cloud import kms

            self._client_instance = kms.KeyManagementServiceClient()
        except Exception as exc:
            raise PaymentConfigurationError(
                "Cloud KMS 클라이언트를 초기화할 수 없습니다."
            ) from exc
        return self._client_instance

    def _key_version(self) -> str:
        if self._version_name is not None:
            return self._version_name
        if "/cryptoKeyVersions/" in self._key_name:
            self._version_name = self._key_name
            return self._version_name
        if "/cryptoKeys/" not in self._key_name:
            raise PaymentConfigurationError(
                "BUYER_KMS_KEY_NAME은 Cloud KMS CryptoKey 리소스 이름이어야 합니다."
            )
        try:
            key = self._client().get_crypto_key(request={"name": self._key_name})
            version_name = getattr(getattr(key, "primary", None), "name", "")
        except Exception as exc:
            raise PaymentConfigurationError(
                "Buyer KMS 키의 primary version을 조회할 수 없습니다."
            ) from exc
        if not version_name:
            raise PaymentConfigurationError("Buyer KMS 키에 활성 primary version이 없습니다.")
        self._version_name = version_name
        return version_name

    def _load_public_key(self) -> Ed25519PublicKey:
        if self._public_key is not None:
            return self._public_key
        try:
            response = self._client().get_public_key(
                request={"name": self._key_version()}
            )
            public_key = serialization.load_pem_public_key(response.pem.encode("ascii"))
        except Exception as exc:
            raise PaymentConfigurationError(
                "Buyer KMS 공개키를 조회할 수 없습니다."
            ) from exc
        if not isinstance(public_key, Ed25519PublicKey):
            raise PaymentConfigurationError(
                "Buyer KMS 키는 EC_SIGN_ED25519 알고리즘이어야 합니다."
            )
        self._public_key = public_key
        return public_key
