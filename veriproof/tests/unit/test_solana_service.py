"""SPEC-001 unit tests — SolanaService.anchor_hash (services layer).

Covers the TDD list:
- test_solana_anchor_hash_returns_signature (mock client)
Plus the 3-retry -> AnchorFailed contract that the register view relies on
(R14). The real ``solana``/``solders`` SDKs are NOT installed; tests inject a
stub client.
"""
from __future__ import annotations

import pytest

# --- Stubs: client exposes ``send_memo``, signer exposes ``public_key`` ------


class _StubSigner:
    """Minimal signer stub: exposes public_key() like KmsSigner."""

    def __init__(self, pubkey: str = "PlatformEscrow111111111111111111111111") -> None:
        self._pubkey = pubkey

    def public_key(self) -> str:
        return self._pubkey


class _OkClient:
    def __init__(self, sig: str = "anchor_stub_sig_42") -> None:
        self.sig = sig
        self.calls = 0

    def send_memo(self, memo: str, signer_pubkey: str) -> str:
        self.calls += 1
        return self.sig


class _FailClient:
    def __init__(self) -> None:
        self.calls = 0

    def send_memo(self, memo: str, signer_pubkey: str) -> str:
        self.calls += 1
        raise RuntimeError("stub rpc failure")


# --- Tests -------------------------------------------------------------------


def test_solana_anchor_hash_returns_signature():
    """With a working client+signer, anchor_hash returns the memo tx sig (R4)."""
    from services.solana_service import SolanaService

    svc = SolanaService(client=_OkClient(sig="memoSIG123"), signer=_StubSigner())
    sig = svc.anchor_hash("a" * 64, "CreatorPubkey123")

    assert isinstance(sig, str)
    assert sig == "memoSIG123"


def test_solana_anchor_hash_retries_three_times_then_raises():
    """3 consecutive failures -> AnchorFailed (R14); client called exactly 3x."""
    from services.solana_service import AnchorFailed, SolanaService

    fail = _FailClient()
    svc = SolanaService(client=fail, signer=_StubSigner())

    with pytest.raises(AnchorFailed):
        svc.anchor_hash("b" * 64, "CreatorPubkey123")

    assert fail.calls == 3  # exactly 3 retries


def test_solana_anchor_hash_raises_anchor_failed_without_client():
    """No client/signer (offline default) -> AnchorFailed immediately."""
    from services.solana_service import AnchorFailed, SolanaService

    svc = SolanaService()  # no client, no signer
    with pytest.raises(AnchorFailed):
        svc.anchor_hash("c" * 64, "CreatorPubkey123")


def test_solana_anchor_hash_succeeds_after_transient_failure():
    """A failure followed by success within the retry budget returns the sig."""
    from services.solana_service import SolanaService

    class _Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def send_memo(self, memo, signer_pubkey):  # noqa: ANN001
            self.calls += 1
            if self.calls < 2:
                raise RuntimeError("transient")
            return "recovered_sig"

    svc = SolanaService(client=_Flaky(), signer=_StubSigner())
    sig = svc.anchor_hash("d" * 64, "CreatorPubkey123")
    assert sig == "recovered_sig"


def test_solana_anchor_hash_degrades_when_signer_missing():
    """Client present but signer None -> AnchorFailed (cannot sign Memo)."""
    from services.solana_service import AnchorFailed, SolanaService

    svc = SolanaService(client=_OkClient(), signer=None)
    with pytest.raises(AnchorFailed):
        svc.anchor_hash("e" * 64, "CreatorPubkey123")


def test_solana_anchor_hash_degrades_when_signer_is_stub():
    """Real KmsSigner.public_key() (unconfigured) -> AnchorFailed.

    SPEC-004: KmsSigner now raises ``KmsSignerError`` (not NotImplementedError)
    when unconfigured; ``_signer_pubkey`` translates every signer failure into
    ``AnchorFailed`` so SPEC-001's ``except AnchorFailed`` contract is stable.
    """
    from services.kms_signer import KmsSigner
    from services.solana_service import AnchorFailed, SolanaService

    svc = SolanaService(client=_OkClient(), signer=KmsSigner())
    with pytest.raises(AnchorFailed):
        svc.anchor_hash("f" * 64, "CreatorPubkey123")


def test_solana_anchor_hash_degrades_when_signer_non_conforming():
    """Signer object without public_key() -> AnchorFailed (graceful)."""
    from services.solana_service import AnchorFailed, SolanaService

    svc = SolanaService(client=_OkClient(), signer=object())
    with pytest.raises(AnchorFailed):
        svc.anchor_hash("g" * 64, "CreatorPubkey123")


def test_solana_get_client_returns_none_when_sdk_missing():
    """rpc_url set but solana-py absent -> _get_client() is None (offline)."""
    from services.solana_service import SolanaService

    svc = SolanaService(rpc_url="https://api.devnet.solana.com")
    # solana-py is intentionally NOT installed in the TDD env.
    assert svc._get_client() is None


def test_solana_get_client_returns_none_without_rpc_url():
    """No rpc_url and no client -> _get_client() is None immediately."""
    from services.solana_service import SolanaService

    assert SolanaService()._get_client() is None


def test_solana_get_client_returns_injected_client():
    """An injected client is returned as-is."""
    from services.solana_service import SolanaService

    stub = _OkClient()
    assert SolanaService(client=stub)._get_client() is stub


def test_solana_factory_builds_local_mock_by_default():
    """로컬 기본값은 실체인처럼 보이지 않는 명시적 목업 어댑터다."""
    from services.mock_solana_service import LocalMockSolanaService
    from services.solana_service import get_solana_service

    svc = get_solana_service()
    assert isinstance(svc, LocalMockSolanaService)


def test_solana_build_memo_format():
    """The Memo payload carries the sha + creator for on-chain attestation."""
    from services.solana_service import SolanaService

    memo = SolanaService._build_memo("a" * 64, "CreatorPub")
    assert "a" * 64 in memo
    assert "CreatorPub" in memo
