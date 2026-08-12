"""Cloud KMS Ed25519 signing with a local Devnet keypair fallback.

Architecture 4 contract. ``google-cloud-kms`` is import-guarded. In local/dev
mode the signer falls back to a base58 keypair from the environment.
"""
from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class KmsSignerError(RuntimeError):
    """Raised when the platform signing key is unavailable / mis-configured.

    SPEC-004: raised at call time (never at construction) when neither a Cloud
    KMS key nor a local fallback key is configured, or when the local path
    cannot import ``solders``/``base58``. Distinct from ``NotImplementedError``
    so the smoke test can assert the implemented-contract shape.
    """


class KmsSigner:
    """Signs messages with the platform escrow key.

    - Cloud: Cloud KMS EC key (``KMS_KEY_NAME``).
    - Local/dev: base58 keypair from ``PLATFORM_ESCROW_SECRET_KEY`` (Devnet
      only). When neither is configured, the signer is inert and methods
      raise at call time (not at construction/import).
    """

    def __init__(
        self,
        kms_key_name: str | None = None,
        local_secret_key: str | None = None,
        kms_client: Any = None,
    ) -> None:
        self.kms_key_name = kms_key_name
        self.local_secret_key = local_secret_key
        self._kms_client = kms_client
        self._kms_key_version_name: str | None = None
        self._kms_public_key: Ed25519PublicKey | None = None

    # --- Architecture 4 methods (SPEC-004 implements local + KMS guards) ----
    def sign(self, message_bytes: bytes) -> bytes:
        """Sign ``message_bytes`` -> raw signature bytes.

        SPEC-004: Cloud KMS EC signing when ``kms_key_name`` + ``kms_client``
        are configured; else local base58 keypair fallback (Devnet). Raises
        ``KmsSignerError`` at call time when no key is configured or the local
        SDK (``solders``) is unavailable.
        """
        if self.kms_key_name is not None:
            return self._sign_kms(message_bytes)
        if self.local_secret_key:
            return self._sign_local(message_bytes)
        raise KmsSignerError(
            "no platform signing key configured (set KMS_KEY_NAME or "
            "PLATFORM_ESCROW_SECRET_KEY)"
        )

    def public_key(self) -> str:
        """Return the platform escrow public key (base58). SPEC-004."""
        if self.kms_key_name is not None:
            return self._public_key_kms()
        if self.local_secret_key:
            return self._public_key_local()
        raise KmsSignerError(
            "no platform signing key configured (set KMS_KEY_NAME or "
            "PLATFORM_ESCROW_SECRET_KEY)"
        )

    # --- Local fallback path (Devnet) ---------------------------------------

    def _keypair_local(self):
        """Load the local keypair from ``PLATFORM_ESCROW_SECRET_KEY``.

        ``solders`` is import-guarded — the TDD venv intentionally omits it.
        Offline callers hit the ImportError -> KmsSignerError; the cloud/dev
        env with ``solders`` installed materialises the Keypair.
        """
        try:
            from solders.keypair import Keypair  # import-guarded
        except ImportError as exc:
            raise KmsSignerError(
                f"local fallback requires solders (not installed): {exc}"
            ) from exc
        try:
            # Accept either a base58 string or a raw secret. solders expects
            # base58; the cloud env / Secret Manager supplies it that way.
            return Keypair.from_base58_string(self.local_secret_key)  # pragma: no cover
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            raise KmsSignerError(
                f"local keypair derivation failed: {exc}"
            ) from exc

    def _public_key_local(self) -> str:
        """로컬 키페어에서 공개키(base58)를 반환한다 (Devnet 폴백 경로)."""
        kp = self._keypair_local()
        return str(kp.pubkey())  # pragma: no cover (needs solders)

    def _sign_local(self, message_bytes: bytes) -> bytes:
        """로컬 키페어로 메시지에 서명하고 원시 서명 바이트를 반환한다 (Devnet 폴백)."""
        kp = self._keypair_local()
        sig = kp.sign_message(message_bytes)  # pragma: no cover (needs solders)
        return bytes(sig)  # pragma: no cover

    # --- Cloud KMS path -----------------------------------------------------

    def _client(self):
        if self._kms_client is not None:
            return self._kms_client
        try:
            from google.cloud import kms
        except ImportError as exc:  # pragma: no cover - cloud dependency guard
            raise KmsSignerError(
                f"Cloud KMS signing requires google-cloud-kms: {exc}"
            ) from exc
        try:
            self._kms_client = kms.KeyManagementServiceClient()
        except Exception as exc:  # noqa: BLE001 - normalize ADC failures
            raise KmsSignerError(f"Cloud KMS client initialization failed: {exc}") from exc
        return self._kms_client

    def _key_version_name(self) -> str:
        if self._kms_key_version_name is not None:
            return self._kms_key_version_name
        name = (self.kms_key_name or "").strip().rstrip("/")
        if not name:
            raise KmsSignerError("KMS_KEY_NAME is empty")
        if "/cryptoKeyVersions/" in name:
            self._kms_key_version_name = name
            return name
        if "/cryptoKeys/" not in name:
            raise KmsSignerError(
                "KMS_KEY_NAME must be a Cloud KMS CryptoKey or CryptoKeyVersion resource name"
            )
        try:
            key = self._client().get_crypto_key(request={"name": name})
            version_name = getattr(getattr(key, "primary", None), "name", "")
        except Exception as exc:  # noqa: BLE001 - normalize cloud boundary
            raise KmsSignerError(f"Cloud KMS primary key lookup failed: {exc}") from exc
        if not version_name:
            raise KmsSignerError("Cloud KMS key has no enabled primary version")
        self._kms_key_version_name = version_name
        return version_name

    def _load_kms_public_key(self) -> Ed25519PublicKey:
        if self._kms_public_key is not None:
            return self._kms_public_key
        try:
            response = self._client().get_public_key(
                request={"name": self._key_version_name()}
            )
            public_key = serialization.load_pem_public_key(response.pem.encode("ascii"))
        except Exception as exc:  # noqa: BLE001 - normalize cloud/PEM failures
            raise KmsSignerError(f"Cloud KMS public key retrieval failed: {exc}") from exc
        if not isinstance(public_key, Ed25519PublicKey):
            raise KmsSignerError("Cloud KMS signing key must use EC_SIGN_ED25519")
        self._kms_public_key = public_key
        return public_key

    def _public_key_kms(self) -> str:
        try:
            from solders.pubkey import Pubkey
        except ImportError as exc:
            raise KmsSignerError(f"KMS Solana public key conversion requires solders: {exc}") from exc
        raw = self._load_kms_public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return str(Pubkey.from_bytes(raw))

    def _sign_kms(self, message_bytes: bytes) -> bytes:
        if not isinstance(message_bytes, bytes) or not message_bytes:
            raise KmsSignerError("Cloud KMS signing requires non-empty message bytes")
        public_key = self._load_kms_public_key()
        try:
            response = self._client().asymmetric_sign(
                request={"name": self._key_version_name(), "data": message_bytes}
            )
            signature = bytes(response.signature)
            public_key.verify(signature, message_bytes)
        except Exception as exc:  # noqa: BLE001 - normalize RPC/integrity failures
            raise KmsSignerError(f"Cloud KMS Ed25519 signing failed: {exc}") from exc
        if len(signature) != 64:
            raise KmsSignerError("Cloud KMS returned a non-Ed25519 signature")
        return signature


def get_kms_signer() -> KmsSigner:
    """Factory: build a KmsSigner from current Django settings."""
    from django.conf import settings

    return KmsSigner(
        kms_key_name=getattr(settings, "KMS_KEY_NAME", "") or None,
        local_secret_key=getattr(settings, "PLATFORM_ESCROW_SECRET_KEY", "")
        or None,
    )
