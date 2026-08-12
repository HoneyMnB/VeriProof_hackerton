"""SPEC-004 unit tests — KmsSigner sign() / public_key() (architecture 4).

The local fallback needs ``solders``/``base58`` (not installed in the TDD env),
so the real keypair derivation path is import-guarded and marked
``# pragma: no cover`` offline. These tests cover the contract shape:
- unconfigured signer raises KmsSignerError (not NotImplementedError).
- factory wires from settings.
- the import-guard seam is exercised (solders missing -> KmsSignerError).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_public_key_raises_kms_signer_error_when_unconfigured():
    """No KMS key, no local key -> KmsSignerError at call time (not ImportError)."""
    from services.kms_signer import KmsSigner, KmsSignerError

    signer = KmsSigner()  # no keys configured
    with pytest.raises(KmsSignerError):
        signer.public_key()


def test_sign_raises_kms_signer_error_when_unconfigured():
    """sign() mirrors public_key(): unconfigured -> KmsSignerError."""
    from services.kms_signer import KmsSigner, KmsSignerError

    signer = KmsSigner()
    with pytest.raises(KmsSignerError):
        signer.sign(b"message bytes")


def test_local_secret_key_path_raises_kms_signer_error_without_solders():
    """Local key present but solders absent -> KmsSignerError (offline guard).

    The TDD venv intentionally lacks ``solders``; the local-derivation branch
    must translate that ImportError into KmsSignerError rather than crash.
    """
    from services.kms_signer import KmsSigner, KmsSignerError

    signer = KmsSigner(local_secret_key="5" * 64)  # plausible base58-ish key
    with pytest.raises(KmsSignerError):
        signer.public_key()


def test_factory_reads_settings(settings):
    """get_kms_signer() wires KMS key name + local key from settings."""
    from services.kms_signer import KmsSigner, get_kms_signer

    settings.KMS_KEY_NAME = "projects/p/locations/l/keyRings/r/cryptoKeys/k"
    settings.PLATFORM_ESCROW_SECRET_KEY = ""
    signer = get_kms_signer()
    assert isinstance(signer, KmsSigner)
    assert signer.kms_key_name == "projects/p/locations/l/keyRings/r/cryptoKeys/k"


def test_factory_returns_empty_signer_when_unset(settings):
    """No env configured -> factory returns an inert signer (errors deferred)."""
    from services.kms_signer import KmsSigner, get_kms_signer

    settings.KMS_KEY_NAME = ""
    settings.PLATFORM_ESCROW_SECRET_KEY = ""
    signer = get_kms_signer()
    assert isinstance(signer, KmsSigner)
    assert signer.kms_key_name is None
    assert signer.local_secret_key is None


def test_kms_signer_resolves_primary_and_verifies_ed25519_signature():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from services.kms_signer import KmsSigner

    private_key = Ed25519PrivateKey.generate()
    pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    class FakeKmsClient:
        def get_crypto_key(self, request):
            assert request["name"].endswith("/cryptoKeys/platform")
            return SimpleNamespace(primary=SimpleNamespace(name=f"{request['name']}/cryptoKeyVersions/1"))

        def get_public_key(self, request):
            assert request["name"].endswith("/cryptoKeyVersions/1")
            return SimpleNamespace(pem=pem)

        def asymmetric_sign(self, request):
            return SimpleNamespace(signature=private_key.sign(request["data"]))

    signer = KmsSigner(
        kms_key_name="projects/p/locations/l/keyRings/r/cryptoKeys/platform",
        kms_client=FakeKmsClient(),
    )

    assert len(signer.sign(b"solana-message")) == 64
    assert len(signer.public_key()) >= 32


def test_kms_signer_rejects_non_ed25519_public_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ec import (
        SECP256R1,
        generate_private_key,
    )

    from services.kms_signer import KmsSigner, KmsSignerError

    pem = generate_private_key(SECP256R1()).public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    client = SimpleNamespace(
        get_public_key=lambda request: SimpleNamespace(pem=pem),
    )
    signer = KmsSigner(
        kms_key_name="projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1",
        kms_client=client,
    )

    with pytest.raises(KmsSignerError, match="EC_SIGN_ED25519"):
        signer.public_key()
