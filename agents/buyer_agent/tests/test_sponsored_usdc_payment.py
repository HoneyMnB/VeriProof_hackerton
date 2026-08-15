"""Tests for the KMS/local signer sponsored USDC payment boundary."""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
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

from agents.buyer_agent.payments.policy import PaymentPolicyRejected
from agents.buyer_agent.payments.sponsored_usdc_client import (
    AutonomousSponsoredUsdcBuyer,
    SponsoredUsdcPolicy,
)
def _intent(*, buyer: Keypair, sponsor: Keypair, recipient: Keypair, amount="0.25"):
    mint = Pubkey.from_string("4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
    memo = "VERIPROOF:USDC:12345678-1234-1234-1234-123456789012"
    atomic = int(float(amount) * 1_000_000)
    sender_ata = get_associated_token_address(buyer.pubkey(), mint, TOKEN_PROGRAM_ID)
    recipient_ata = get_associated_token_address(recipient.pubkey(), mint, TOKEN_PROGRAM_ID)
    message = Message.new_with_blockhash(
        [
            create_idempotent_associated_token_account(sponsor.pubkey(), recipient.pubkey(), mint, TOKEN_PROGRAM_ID),
            transfer_checked(TransferCheckedParams(
                program_id=TOKEN_PROGRAM_ID,
                source=sender_ata,
                mint=mint,
                dest=recipient_ata,
                owner=buyer.pubkey(),
                amount=atomic,
                decimals=6,
            )),
            Instruction(Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"), memo.encode(), []),
        ],
        sponsor.pubkey(),
        Hash.from_string("9np6nYVt7gVYHjGsCL8APiSmFm1hyVeTLjkcbYvZLhJr"),
    )
    sponsor_signature = sponsor.sign_message(bytes(message))
    transaction = Transaction.populate(message, [sponsor_signature, Signature.default()])
    return {
        "intent_id": "12345678-1234-1234-1234-123456789012",
        "transaction": base64.b64encode(bytes(transaction)).decode(),
        "amount_usdc": amount,
        "currency": "USDC",
        "sponsor": str(sponsor.pubkey()),
        "buyer_wallet": str(buyer.pubkey()),
        "recipient_wallet": str(recipient.pubkey()),
        "usdc_mint": str(mint),
        "memo": memo,
    }


def test_sponsored_buyer_signs_only_the_canonical_partial_transaction():
    buyer, sponsor, recipient = Keypair(), Keypair(), Keypair()
    intent = _intent(buyer=buyer, sponsor=sponsor, recipient=recipient)
    policy = SponsoredUsdcPolicy(enabled=True, mint=intent["usdc_mint"], max_atomic_amount=500_000)
    payment = AutonomousSponsoredUsdcBuyer(policy=policy, private_key=str(buyer))

    signed = asyncio.run(payment._sign_intent(intent, buyer.pubkey()))

    assert base64.b64decode(signed)


def test_sponsored_buyer_rejects_a_response_whose_amount_does_not_match_transaction():
    buyer, sponsor, recipient = Keypair(), Keypair(), Keypair()
    intent = _intent(buyer=buyer, sponsor=sponsor, recipient=recipient)
    intent["amount_usdc"] = "0.30"
    policy = SponsoredUsdcPolicy(enabled=True, mint=intent["usdc_mint"], max_atomic_amount=500_000)
    payment = AutonomousSponsoredUsdcBuyer(policy=policy, private_key=str(buyer))

    with pytest.raises(PaymentPolicyRejected):
        asyncio.run(payment._sign_intent(intent, buyer.pubkey()))
