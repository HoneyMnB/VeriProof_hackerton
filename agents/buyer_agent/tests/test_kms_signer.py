"""Cloud KMS signer tests for the independently deployed Buyer Agent."""

from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agents.buyer_agent.payments.kms_signer import KmsEd25519Signer
from agents.buyer_agent.payments.policy import (
    PaymentConfigurationError,
    PaymentExecutionError,
)


def test_buyer_kms_signer_uses_explicit_version_and_signs_message():
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    class FakeClient:
        def get_public_key(self, request):
            assert request["name"].endswith("/cryptoKeyVersions/7")
            return SimpleNamespace(pem=pem)

        def asymmetric_sign(self, request):
            return SimpleNamespace(signature=private_key.sign(request["data"]))

    signer = KmsEd25519Signer(
        "projects/p/locations/l/keyRings/r/cryptoKeys/buyer/cryptoKeyVersions/7",
        client=FakeClient(),
    )

    assert len(signer.sign(b"payment-message")) == 64
    assert str(signer.public_key())


def test_buyer_kms_signer_rejects_unversioned_asymmetric_key():
    signer = KmsEd25519Signer(
        "projects/p/locations/l/keyRings/r/cryptoKeys/buyer",
        client=SimpleNamespace(),
    )

    with pytest.raises(PaymentConfigurationError, match="CryptoKeyVersion"):
        signer.public_key()


def test_buyer_kms_signer_rejects_tampered_signature():
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    client = SimpleNamespace(
        get_public_key=lambda request: SimpleNamespace(pem=pem),
        asymmetric_sign=lambda request: SimpleNamespace(signature=b"x" * 64),
    )
    signer = KmsEd25519Signer(
        "projects/p/locations/l/keyRings/r/cryptoKeys/buyer/cryptoKeyVersions/1",
        client=client,
    )

    with pytest.raises(PaymentExecutionError, match="KMS"):
        signer.sign(b"payment-message")
