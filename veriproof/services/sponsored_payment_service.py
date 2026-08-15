"""Build sponsor-paid browser USDC transfers without custodying buyer funds."""
from __future__ import annotations

import base64
import decimal
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from .kms_signer import KmsSigner, KmsSignerError
from .solana_service import CertificateIssueError, SolanaService

USDC_DECIMALS = 6
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"


class SponsoredPaymentConfigurationError(RuntimeError):
    """Raised when gas sponsorship is unavailable or incorrectly configured."""


@dataclass(frozen=True)
class SponsoredTransaction:
    serialized_transaction: str
    sponsor: str


class SponsoredPaymentService:
    """Creates a partially-signed USDC transfer with the platform as fee payer.

    The returned legacy transaction contains exactly three business operations:
    idempotent recipient ATA creation, a checked USDC transfer, and the
    payment-intent memo.  The buyer still has to sign as token authority.
    """

    def __init__(
        self,
        *,
        rpc_url: str | None = None,
        usdc_mint: str | None = None,
        sponsor_pubkey: str | None = None,
        signer: KmsSigner | None = None,
    ) -> None:
        self.rpc_url = rpc_url if rpc_url is not None else settings.SOLANA_RPC_URL
        self.usdc_mint = usdc_mint if usdc_mint is not None else settings.USDC_MINT_ADDRESS
        self.sponsor_pubkey = sponsor_pubkey if sponsor_pubkey is not None else settings.PAYMENT_SPONSOR_PUBKEY
        self.signer = signer if signer is not None else KmsSigner(
            kms_key_name=settings.PAYMENT_SPONSOR_KMS_KEY_NAME or None,
            local_secret_key=settings.PAYMENT_SPONSOR_SECRET_KEY or None,
        )

    def build_transaction(
        self,
        *,
        buyer_wallet: str,
        recipient_wallet: str,
        amount_usdc: decimal.Decimal,
        memo: str,
    ) -> SponsoredTransaction:
        try:
            from solders.hash import Hash
            from solders.instruction import Instruction
            from solders.message import Message
            from solders.pubkey import Pubkey
            from solders.signature import Signature
            from solders.transaction import Transaction
            from spl.token.constants import TOKEN_PROGRAM_ID
            from spl.token.instructions import (
                TransferCheckedParams,
                create_idempotent_associated_token_account,
                get_associated_token_address,
                transfer_checked,
            )
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise SponsoredPaymentConfigurationError("USDC sponsor requires solders and spl-token") from exc

        if not memo.startswith("VERIPROOF:USDC:") or len(memo.encode("utf-8")) > 566:
            raise SponsoredPaymentConfigurationError("payment memo is invalid")
        amount = self._to_atomic_units(amount_usdc)
        try:
            sponsor = Pubkey.from_string(self._resolved_sponsor_pubkey())
            buyer = Pubkey.from_string(buyer_wallet)
            recipient = Pubkey.from_string(recipient_wallet)
            mint = Pubkey.from_string(self.usdc_mint)
            sender_ata = get_associated_token_address(buyer, mint, TOKEN_PROGRAM_ID)
            recipient_ata = get_associated_token_address(recipient, mint, TOKEN_PROGRAM_ID)
            instructions = [
                create_idempotent_associated_token_account(sponsor, recipient, mint, TOKEN_PROGRAM_ID),
                transfer_checked(
                    TransferCheckedParams(
                        program_id=TOKEN_PROGRAM_ID,
                        source=sender_ata,
                        mint=mint,
                        dest=recipient_ata,
                        owner=buyer,
                        amount=amount,
                        decimals=USDC_DECIMALS,
                    )
                ),
                Instruction(Pubkey.from_string(MEMO_PROGRAM_ID), memo.encode("utf-8"), []),
            ]
            blockhash = SolanaService(rpc_url=self.rpc_url)._request_latest_blockhash()
            message = Message.new_with_blockhash(instructions, sponsor, Hash.from_string(blockhash))
            unsigned = Transaction.new_unsigned(message)
            sponsor_signature = Signature.from_bytes(self.signer.sign(unsigned.message_data()))
            transaction = Transaction.populate(message, [sponsor_signature, Signature.default()])
        except (CertificateIssueError, KmsSignerError, ValueError) as exc:
            raise SponsoredPaymentConfigurationError("USDC gas sponsor is unavailable") from exc
        return SponsoredTransaction(
            serialized_transaction=base64.b64encode(bytes(transaction)).decode("ascii"),
            sponsor=str(sponsor),
        )

    def _resolved_sponsor_pubkey(self) -> str:
        configured = str(self.sponsor_pubkey or "").strip()
        if not configured:
            raise SponsoredPaymentConfigurationError("PAYMENT_SPONSOR_PUBKEY is required")
        actual = self.signer.public_key()
        if actual != configured:
            raise SponsoredPaymentConfigurationError("payment sponsor public key does not match signer")
        return configured

    @staticmethod
    def _to_atomic_units(amount_usdc: decimal.Decimal) -> int:
        amount = decimal.Decimal(amount_usdc)
        atomic = amount * (decimal.Decimal(10) ** USDC_DECIMALS)
        if not amount.is_finite() or amount <= 0 or atomic != atomic.to_integral_value():
            raise SponsoredPaymentConfigurationError("USDC amount must be a positive six-decimal value")
        return int(atomic)


def get_sponsored_payment_service() -> SponsoredPaymentService:
    return SponsoredPaymentService()
