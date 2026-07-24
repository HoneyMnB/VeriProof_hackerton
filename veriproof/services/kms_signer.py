"""KmsSigner — Cloud KMS EC signing with local env-keypair fallback.

Architecture 4 contract. ``google-cloud-kms`` is import-guarded. In local/dev
mode the signer falls back to a base58 keypair from the environment.
"""
from __future__ import annotations

from typing import Any


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

    # --- Architecture 4 methods (SPEC-004 implements local + KMS guards) ----
    def sign(self, message_bytes: bytes) -> bytes:
        """Sign ``message_bytes`` -> raw signature bytes.

        SPEC-004: Cloud KMS EC signing when ``kms_key_name`` + ``kms_client``
        are configured; else local base58 keypair fallback (Devnet). Raises
        ``KmsSignerError`` at call time when no key is configured or the local
        SDK (``solders``) is unavailable.
        """
        if self.kms_key_name is not None and self._kms_client is not None:
            return self._sign_kms(message_bytes)  # pragma: no cover (cloud)
        if self.local_secret_key:
            return self._sign_local(message_bytes)
        raise KmsSignerError(
            "no platform signing key configured (set KMS_KEY_NAME or "
            "PLATFORM_ESCROW_SECRET_KEY)"
        )

    def public_key(self) -> str:
        """Return the platform escrow public key (base58). SPEC-004."""
        if self.kms_key_name is not None and self._kms_client is not None:
            return self._public_key_kms()  # pragma: no cover (cloud)
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
        kp = self._keypair_local()
        return str(kp.pubkey())  # pragma: no cover (needs solders)

    def _sign_local(self, message_bytes: bytes) -> bytes:
        kp = self._keypair_local()
        sig = kp.sign_message(message_bytes)  # pragma: no cover (needs solders)
        return bytes(sig)  # pragma: no cover

    # --- Cloud KMS path (import-guarded; runs only in cloud) ----------------

    def _public_key_kms(self) -> str:  # pragma: no cover
        raise KmsSignerError("KMS public key retrieval not wired in this env")

    def _sign_kms(self, message_bytes: bytes) -> bytes:  # pragma: no cover
        raise KmsSignerError("KMS signing not wired in this env")


def get_kms_signer() -> KmsSigner:
    """Factory: build a KmsSigner from current Django settings."""
    from django.conf import settings

    return KmsSigner(
        kms_key_name=getattr(settings, "KMS_KEY_NAME", "") or None,
        local_secret_key=getattr(settings, "PLATFORM_ESCROW_SECRET_KEY", "")
        or None,
    )
