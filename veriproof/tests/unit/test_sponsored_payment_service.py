import base64
import decimal

import pytest
from solders.keypair import Keypair
from solders.transaction import Transaction

from services.solana_service import SolanaService
from services.sponsored_payment_service import (
    SponsoredPaymentConfigurationError,
    SponsoredPaymentService,
)


class _Signer:
    def __init__(self, keypair):
        self.keypair = keypair

    def public_key(self):
        return str(self.keypair.pubkey())

    def sign(self, message):
        return bytes(self.keypair.sign_message(message))


def test_build_transaction_preserves_sponsor_signature_and_requires_buyer_signature(monkeypatch):
    sponsor, buyer, recipient = Keypair(), Keypair(), Keypair()
    monkeypatch.setattr(
        SolanaService,
        "_request_latest_blockhash",
        lambda self: "9np6nYVt7gVYHjGsCL8APiSmFm1hyVeTLjkcbYvZLhJr",
    )
    service = SponsoredPaymentService(
        sponsor_pubkey=str(sponsor.pubkey()),
        signer=_Signer(sponsor),
    )

    result = service.build_transaction(
        buyer_wallet=str(buyer.pubkey()),
        recipient_wallet=str(recipient.pubkey()),
        amount_usdc=decimal.Decimal("1.250000"),
        memo="VERIPROOF:USDC:12345678-1234-1234-1234-123456789012",
    )

    transaction = Transaction.from_bytes(base64.b64decode(result.serialized_transaction))
    assert result.sponsor == str(sponsor.pubkey())
    assert transaction.verify_with_results() == [True, False]


def test_rejects_a_sponsor_key_that_does_not_match_its_signer():
    sponsor, other = Keypair(), Keypair()
    service = SponsoredPaymentService(
        sponsor_pubkey=str(sponsor.pubkey()),
        signer=_Signer(other),
    )

    with pytest.raises(SponsoredPaymentConfigurationError):
        service._resolved_sponsor_pubkey()
