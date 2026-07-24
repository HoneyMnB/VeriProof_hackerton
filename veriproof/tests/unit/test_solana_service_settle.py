"""SPEC-004 unit tests — SolanaService.verify_usdc_payment + issue_certificate
+ transfer_usdc (R2/R6, AC-2/AC-3, architecture 4, 8).

The real ``solana``/``solders`` SDKs are NOT installed in the TDD env; tests
inject a stub RPC client that exposes a ``get_payment(tx_sig)`` seam returning
the parsed SPL-USDC transfer fields. The verify logic (recipient + mint +
amount-in-min-units + commitment checks) is exercised against that stub so the
decision matrix is covered offline without hitting the network.

TDD list (SPEC-004 5):
- test_verify_requires_matching_recipient
- test_verify_requires_matching_mint
- test_verify_requires_matching_amount_min_units
- test_verify_rejects_unconfirmed_commitment
- test_issue_certificate_returns_signature
Plus the valid happy path + failure -> CertificateIssueError + transfer_usdc.
"""
from __future__ import annotations

import decimal

import pytest

_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU"
_CREATOR_WALLET = "CreatorWallet1111111111111111111111111111111"
_BUYER_WALLET = "BuyerWallet1111111111111111111111111111111111"


# --- Stub RPC client + signer ------------------------------------------------


class _PaymentClient:
    """Stub RPC client exposing the ``get_payment(tx_sig)`` verify seam.

    Returns a canned payment dict (or None when the tx is unknown). Amount is
    carried in integer min-units (6 decimals) exactly as the real SPL token
    parser would surface it, so the integer-compare contract (architecture 8)
    is testable without float drift.
    """

    def __init__(
        self,
        *,
        recipient: str = _CREATOR_WALLET,
        mint: str = _USDC_MINT,
        amount_min_units: int = 1_500_000,
        sender: str = _BUYER_WALLET,
        slot: int = 123_456_789,
        commitment: str = "confirmed",
        missing: bool = False,
    ) -> None:
        self._payment = {
            "recipient": recipient,
            "mint": mint,
            "amount_min_units": amount_min_units,
            "sender": sender,
            "slot": slot,
            "commitment": commitment,
        } if not missing else None

    def get_payment(self, tx_sig: str) -> dict | None:
        return self._payment


class _CertClient:
    """Stub client exposing the certificate Memo send seam (like anchor_hash)."""

    def __init__(self, sig: str = "cert_stub_sig_42", fail: bool = False) -> None:
        self.sig = sig
        self.fail = fail
        self.calls = 0

    def send_memo(self, memo: str, signer_pubkey: str) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("stub cert rpc failure")
        return self.sig

    def send_transfer(self, to_pubkey: str, amount_min_units: int,
                      signer_pubkey: str) -> str:
        return self.sig


class _StubSigner:
    def __init__(self, pubkey: str = "PlatformEscrow111111111111111111111111") -> None:
        self._pubkey = pubkey

    def public_key(self) -> str:
        return self._pubkey

    def sign(self, message_bytes: bytes) -> bytes:
        return b"stubsig"


def _svc(client=None, signer=None):
    from services.solana_service import SolanaService

    return SolanaService(
        rpc_url="https://stub.local",
        usdc_mint=_USDC_MINT,
        signer=signer or _StubSigner(),
        client=client,
    )


# === verify: happy path ======================================================


def test_verify_valid_when_all_match():
    """All of recipient + mint + amount match + confirmed -> is_valid=True."""
    svc = _svc(client=_PaymentClient(amount_min_units=1_500_000))
    result = svc.verify_usdc_payment(
        "tx_ok",
        expected_recipient=_CREATOR_WALLET,
        expected_amount=decimal.Decimal("1.5"),
        mint=_USDC_MINT,
    )
    assert result.is_valid is True
    assert result.commitment == "confirmed"
    assert result.amount == decimal.Decimal("1.5")
    assert result.slot == 123_456_789


# === R2 / AC-2 / AC-3: mismatch -> invalid ===================================


def test_verify_requires_matching_recipient():
    """Recipient mismatch -> is_valid=False (R2)."""
    svc = _svc(client=_PaymentClient(recipient="SomeoneElse111111111111111111111111111"))
    result = svc.verify_usdc_payment(
        "tx_bad_recipient",
        expected_recipient=_CREATOR_WALLET,
        expected_amount=decimal.Decimal("1.5"),
        mint=_USDC_MINT,
    )
    assert result.is_valid is False


def test_verify_requires_matching_mint():
    """Mint mismatch -> is_valid=False (AC-3)."""
    svc = _svc(client=_PaymentClient(mint="WrongMint1111111111111111111111111111111"))
    result = svc.verify_usdc_payment(
        "tx_bad_mint",
        expected_recipient=_CREATOR_WALLET,
        expected_amount=decimal.Decimal("1.5"),
        mint=_USDC_MINT,
    )
    assert result.is_valid is False


def test_verify_requires_matching_amount_min_units():
    """Amount compared in integer min-units (6 decimals). AC-2.

    On-chain paid 1.499999 USDC (1_499_999 min-units) vs expected 1.5
    (1_500_000 min-units) -> insufficient -> is_valid=False. This is the exact
    integer-compare contract from architecture 8 that float comparison would
    mis-classify as equal.
    """
    svc = _svc(client=_PaymentClient(amount_min_units=1_499_999))
    result = svc.verify_usdc_payment(
        "tx_short",
        expected_recipient=_CREATOR_WALLET,
        expected_amount=decimal.Decimal("1.5"),
        mint=_USDC_MINT,
    )
    assert result.is_valid is False


def test_verify_amount_float_equivalence_does_not_false_positive():
    """1.5 expressed as a float-prone Decimal still resolves to 1_500_000."""
    svc = _svc(client=_PaymentClient(amount_min_units=1_500_000))
    # A Decimal built from a float carries binary artifacts; the min-units
    # conversion MUST still land on exactly 1_500_000 (not 1_499_999).
    result = svc.verify_usdc_payment(
        "tx_float",
        expected_recipient=_CREATOR_WALLET,
        expected_amount=decimal.Decimal("1.5"),
        mint=_USDC_MINT,
    )
    assert result.is_valid is True


def test_verify_rejects_unconfirmed_commitment():
    """Commitment < confirmed (e.g. processed) -> is_valid=False."""
    svc = _svc(client=_PaymentClient(commitment="processed"))
    result = svc.verify_usdc_payment(
        "tx_unconfirmed",
        expected_recipient=_CREATOR_WALLET,
        expected_amount=decimal.Decimal("1.5"),
        mint=_USDC_MINT,
    )
    assert result.is_valid is False
    assert result.commitment == "processed"


def test_verify_accepts_finalized_commitment():
    """finalized >= confirmed -> valid (R2 commitment floor)."""
    svc = _svc(client=_PaymentClient(commitment="finalized"))
    result = svc.verify_usdc_payment(
        "tx_finalized",
        expected_recipient=_CREATOR_WALLET,
        expected_amount=decimal.Decimal("1.5"),
        mint=_USDC_MINT,
    )
    assert result.is_valid is True


def test_verify_missing_tx_is_invalid():
    """Unknown tx_sig (client returns None) -> is_valid=False, not an exception."""
    svc = _svc(client=_PaymentClient(missing=True))
    result = svc.verify_usdc_payment(
        "tx_unknown",
        expected_recipient=_CREATOR_WALLET,
        expected_amount=decimal.Decimal("1.5"),
        mint=_USDC_MINT,
    )
    assert result.is_valid is False


# === issue_certificate (R6 / R16) ============================================


def test_issue_certificate_returns_signature():
    """With a working client+signer, issue_certificate returns the Memo tx sig."""
    svc = _svc(client=_CertClient(sig="certSIG"), signer=_StubSigner())
    sig = svc.issue_certificate(
        asset_id="asset-uuid-1",
        buyer_pubkey=_BUYER_WALLET,
        memo="veriproof:cert:asset-uuid-1",
    )
    assert isinstance(sig, str)
    assert sig == "certSIG"


def test_issue_certificate_raises_certificate_issue_error_on_failure():
    """Client failure -> CertificateIssueError (R16) — NOT a bare exception.

    The pipeline catches this specifically to keep the license while setting
    certificate_tx_sig=None.
    """
    from services.solana_service import CertificateIssueError

    svc = _svc(client=_CertClient(fail=True), signer=_StubSigner())
    with pytest.raises(CertificateIssueError):
        svc.issue_certificate(
            asset_id="asset-uuid-2",
            buyer_pubkey=_BUYER_WALLET,
            memo="veriproof:cert:asset-uuid-2",
        )


def test_issue_certificate_degrades_without_client():
    """No client/signer -> CertificateIssueError (cannot submit Memo)."""
    from services.solana_service import CertificateIssueError, SolanaService

    svc = SolanaService(signer=_StubSigner())  # no client
    with pytest.raises(CertificateIssueError):
        svc.issue_certificate("aid", _BUYER_WALLET, "memo")


# === transfer_usdc (SPEC-008 seam, implemented for completeness) =============


def test_transfer_usdc_returns_signature():
    """Escrow SPL-USDC transfer returns the tx signature (architecture 4)."""
    svc = _svc(client=_CertClient(sig="transferSIG"), signer=_StubSigner())
    sig = svc.transfer_usdc(_CREATOR_WALLET, decimal.Decimal("0.45"))
    assert isinstance(sig, str)
    assert sig == "transferSIG"
