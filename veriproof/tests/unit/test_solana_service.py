"""Unit tests for the native SOL transfer service."""
from __future__ import annotations

import decimal

import pytest


def test_transfer_sol_rejects_a_non_cli_secret_key_before_rpc_access():
    from services.solana_service import CertificateIssueError, SolanaService

    service = SolanaService(rpc_url="https://api.devnet.solana.com")

    with pytest.raises(CertificateIssueError, match="exactly 64"):
        service.transfer_sol(
            "ESCDLDSaqAkeGDgHxoafZCd7SYDNG2ZkUD3ueDpWgNTy",
            [0],
            decimal.Decimal("0.001"),
        )


def test_transfer_sol_rejects_non_positive_amount_before_rpc_access():
    from services.solana_service import CertificateIssueError, SolanaService

    service = SolanaService(rpc_url="https://api.devnet.solana.com")

    with pytest.raises(CertificateIssueError, match="must be positive"):
        service.transfer_sol(
            "ESCDLDSaqAkeGDgHxoafZCd7SYDNG2ZkUD3ueDpWgNTy",
            [0] * 64,
            decimal.Decimal("0"),
        )
