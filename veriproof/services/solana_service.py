"""SolanaService — Solana via Google Cloud Blockchain RPC (Memo / SPL Token).

Architecture 4 contract. ``solana`` / ``solders`` / ``spl-token`` are
import-guarded; live RPC calls happen only inside method bodies. Tests swap
in ``tests.fakes.FakeSolanaService``.
"""
from __future__ import annotations

import decimal
import logging
from typing import Any

from ._types import PaymentVerification

logger = logging.getLogger(__name__)

# SPEC-001 R14: retry budget for the memo anchor before giving up.
ANCHOR_MAX_RETRIES = 3


class AnchorFailed(Exception):
    """Raised when on-chain hash anchoring exhausts its retry budget.

    SPEC-001 R14: the register view catches this and persists the asset as
    ``status=draft`` with ``anchor_tx_sig=None``, returning HTTP 202.
    """


class CertificateIssueError(Exception):
    """Raised when the on-chain certificate Memo cannot be issued.

    SPEC-004 R16: the settlement pipeline catches this so the License is kept
    while ``certificate_tx_sig`` stays ``None`` (payment verification and
    certificate issuance are intentionally decoupled).
    """


class VerifyUnavailable(Exception):
    """Raised when USDC payment verification cannot run (no RPC client).

    SPEC-004: distinct from an on-chain ``is_valid=False`` result — the caller
    cannot make a validity decision without a backend, so this surfaces as a
    server-side degrade rather than a client-side 400.
    """


class SolanaService:
    """Isolates all Solana on-chain I/O (anchoring, verification, transfer).

    Constructor stores config only and MUST NOT connect at import time. The
    real ``solana.Client`` is created lazily inside methods.
    """

    def __init__(
        self,
        rpc_url: str | None = None,
        usdc_mint: str | None = None,
        signer: Any = None,
        client: Any = None,
    ) -> None:
        self.rpc_url = rpc_url
        self.usdc_mint = usdc_mint
        # KmsSigner instance (or compatible). Injected for testability.
        self.signer = signer
        self._client = client

    # --- SPEC-004: verify / issue_certificate / transfer_usdc ----------------

    # USDC has 6 decimal places (architecture 8). Comparing in integer
    # min-units avoids float drift: 1.5 USDC == 1_500_000 min-units.
    USDC_DECIMALS = 6
    # Commitment floor for a verified payment (architecture 8).
    _CONFIRMED_OR_ABOVE = frozenset({"confirmed", "finalized"})

    def _to_min_units(self, amount: decimal.Decimal) -> int:
        """Convert USDC major units to integer min-units (6 decimals)."""
        quantum = decimal.Decimal(10) ** self.USDC_DECIMALS
        return int((amount * quantum).to_integral_value(rounding=decimal.ROUND_HALF_EVEN))

    def verify_usdc_payment(
        self,
        tx_sig: str,
        expected_recipient: str,
        expected_amount: decimal.Decimal,
        mint: str,
    ) -> PaymentVerification:
        """Verify a USDC payment: recipient + mint + amount (tolerance 0).

        SPEC-004 R2 / AC-2 / AC-3 / architecture 8. Commitment must be
        ``confirmed`` or above; recipient, mint, and amount must ALL match.
        Amount is compared in integer min-units (6 decimals) to avoid
        floating-point drift around the exact on-chain value.

        Raises ``VerifyUnavailable`` when no RPC backend is configured; returns
        ``is_valid=False`` (not an exception) for any on-chain mismatch.
        """
        client = self._get_client()
        if client is None:
            raise VerifyUnavailable(
                "Solana RPC client unavailable; cannot verify USDC payment"
            )
        payment = self._fetch_payment(client, tx_sig)
        if payment is None:
            return PaymentVerification(
                is_valid=False,
                amount=expected_amount,
                sender="",
                slot=0,
                commitment=None,
            )

        commitment = payment.get("commitment")
        amount_min = int(payment.get("amount_min_units", 0))
        expected_min = self._to_min_units(expected_amount)
        amount_major = (
            decimal.Decimal(amount_min) / (decimal.Decimal(10) ** self.USDC_DECIMALS)
        )

        is_valid = (
            commitment in self._CONFIRMED_OR_ABOVE
            and payment.get("recipient") == expected_recipient
            and payment.get("mint") == mint
            and amount_min == expected_min
        )
        return PaymentVerification(
            is_valid=is_valid,
            amount=amount_major,
            sender=payment.get("sender", ""),
            slot=int(payment.get("slot", 0)),
            commitment=commitment,
        )

    def _fetch_payment(self, client: Any, tx_sig: str) -> dict | None:
        """Fetch + parse the SPL-USDC transfer for ``tx_sig`` into a dict.

        Returns a dict with keys ``recipient``, ``mint``, ``amount_min_units``
        (int), ``sender``, ``slot``, ``commitment``; or None when the tx is
        unknown / not found.

        Test seam: an injected client may expose ``get_payment(tx_sig)``
        directly. The real path parses the Solana RPC ``get_transaction``
        response into the same shape; the SDK is import-guarded.
        """
        seam = getattr(client, "get_payment", None)
        if seam is not None:
            return seam(tx_sig)
        # Real RPC path: only runs with the full Solana toolchain installed
        # (cloud). Excluded from the offline coverage gate.
        try:  # pragma: no cover
            return self._parse_rpc_payment(client, tx_sig)  # pragma: no cover
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning("verify_usdc_payment parse failed: %s", exc)  # pragma: no cover
            return None  # pragma: no cover

    def _parse_rpc_payment(self, client: Any, tx_sig: str) -> dict | None:  # pragma: no cover
        """Parse the real Solana RPC get_transaction response (cloud only)."""
        resp = client.get_transaction(tx_sig, commitment="confirmed")
        # ``resp`` is the solders get_transaction result; the exact attribute
        # walk depends on the installed solders version. The cloud deployment
        # owns the real parser; offline tests use the ``get_payment`` seam.
        value = getattr(resp, "value", resp)
        if value is None:
            return None
        meta = getattr(value, "meta", None)
        slot = getattr(value, "slot", 0)
        commitment = "confirmed"
        # Walk the token balances / instruction data for the SPL transfer. This
        # is intentionally left coarse: the cloud path finalises the parsing
        # against the deployed solders schema.
        post_token_bal = getattr(meta, "post_token_balances", []) or []
        pre_token_bal = getattr(meta, "pre_token_balances", []) or []
        amount_min_units = 0
        recipient = ""
        mint_str = ""
        sender = ""
        for post in post_token_bal:
            pre_match = next(
                (p for p in pre_token_bal if getattr(p, "account_index", -1)
                 == getattr(post, "account_index", -1)),
                None,
            )
            pre_amount = decimal.Decimal(getattr(pre_match, "ui_token_amount", {}).get("amount", "0"))
            post_amount = decimal.Decimal(getattr(post, "ui_token_amount", {}).get("amount", "0"))
            if post_amount > pre_amount:
                recipient = getattr(post, "owner", "") or recipient
                mint_str = getattr(post, "mint", "") or mint_str
                amount_min_units = int(post_amount - pre_amount)
        return {
            "recipient": recipient,
            "mint": mint_str,
            "amount_min_units": amount_min_units,
            "sender": sender,
            "slot": int(slot or 0),
            "commitment": commitment,
        }

    def issue_certificate(
        self, asset_id: Any, buyer_pubkey: str, memo: str
    ) -> str:
        """Issue an on-chain certificate Memo (platform signature). R6.

        SPEC-004 R16: on failure raises ``CertificateIssueError`` so the
        pipeline keeps the license while leaving ``certificate_tx_sig=None``.
        """
        client = self._get_client()
        if client is None or self.signer is None:
            raise CertificateIssueError(
                "certificate issuance unavailable: RPC client or signer not configured"
            )
        cert_memo = self._build_certificate_memo(asset_id, buyer_pubkey, memo)
        try:
            signer_pubkey = self._signer_pubkey()
            return self._send_memo(client, cert_memo, signer_pubkey)
        except CertificateIssueError:
            raise
        except Exception as exc:  # noqa: BLE001 (RPC + signer errors are broad)
            logger.warning("issue_certificate failed: %s", exc)
            raise CertificateIssueError(
                f"certificate issuance failed: {exc}"
            ) from exc

    def issue_registration_certificate(
        self, asset_id: Any, creator_pubkey: str, content_sha256: str
    ) -> str:
        """창작자 등록 인증서를 온체인 Memo로 발급한다."""
        if not content_sha256:
            raise CertificateIssueError("content hash is required for registration certificate")
        return self.issue_certificate(
            asset_id,
            creator_pubkey,
            f"registration:{content_sha256}",
        )

    def transfer_usdc(
        self, to_pubkey: str, amount: decimal.Decimal
    ) -> str:
        """Escrow payout SPL-USDC transfer (royalty distribution). SPEC-008 R9.

        Implemented here for completeness; RoyaltyService.distribute (SPEC-008)
        calls it. KMS-signed by ``self.signer``.
        """
        client = self._get_client()
        if client is None or self.signer is None:
            raise CertificateIssueError(
                "transfer_usdc unavailable: RPC client or signer not configured"
            )
        amount_min = self._to_min_units(amount)
        try:
            signer_pubkey = self._signer_pubkey()
            # Test seam: an injected client may expose send_transfer directly.
            seam = getattr(client, "send_transfer", None)
            if seam is not None:
                return seam(to_pubkey, amount_min, signer_pubkey)
            # Real path: build + sign the SPL transfer tx (cloud only).
            return self._send_spl_transfer(  # pragma: no cover (cloud only)
                client, to_pubkey, amount_min, signer_pubkey
            )
        except CertificateIssueError:
            raise
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            raise CertificateIssueError(f"transfer_usdc failed: {exc}") from exc

    def _send_spl_transfer(  # pragma: no cover (cloud only)
        self, client, to_pubkey, amount_min_units, signer_pubkey
    ) -> str:  # pragma: no cover
        """미구성된 SPL 전송은 실패 처리하여 빈 거래 전송을 막는다.

        토큰 계정 조회와 ``transfer_checked`` 명령 생성이 갖춰지지 않은
        상태에서 빈 Transaction을 보내면 결제 성공처럼 보일 위험이 있다.
        실제 전송 어댑터를 연결하기 전에는 반드시 실패시킨다.
        """
        raise CertificateIssueError(
            "SPL-USDC transfer adapter is not configured; payout was not submitted"
        )

    @staticmethod
    def _build_certificate_memo(asset_id: Any, buyer_pubkey: str, memo: str) -> str:
        """Build the certificate Memo Program payload (architecture 4)."""
        return f"veriproof:cert:{asset_id}:{buyer_pubkey}:{memo}"

    # --- Architecture 4 methods (SPEC-001 implements anchor_hash) -----------
    def anchor_hash(self, image_sha256: str, creator_pubkey: str) -> str:
        """Anchor the original SHA-256 via the Memo Program.

        SPEC-001 R4 / R14. Attempts the on-chain Memo transaction up to 3
        times; raises ``AnchorFailed`` on exhaustion so the caller can fall
        back to ``status=draft`` / HTTP 202.
        """
        client = self._get_client()
        # Without a live client OR a signer we cannot submit a Memo tx.
        if client is None or self.signer is None:
            raise AnchorFailed(
                "Solana anchor unavailable: RPC client or signer not configured"
            )
        memo = self._build_memo(image_sha256, creator_pubkey)
        signer_pubkey = self._signer_pubkey()
        last_error: Exception | None = None
        for _ in range(ANCHOR_MAX_RETRIES):
            try:
                return self._send_memo(client, memo, signer_pubkey)
            except Exception as exc:  # noqa: BLE001 (RPC errors are broad)
                last_error = exc
                logger.warning("solana anchor_hash attempt failed: %s", exc)
        raise AnchorFailed(
            f"anchor_hash failed after {ANCHOR_MAX_RETRIES} retries: {last_error}"
        )

    # --- Internal helpers (SPEC-001) ----------------------------------------

    def _get_client(self) -> Any:
        """Return the injected client or a lazily-built real Solana client.

        Returns None when the SDK is unavailable so callers fail fast into
        the AnchorFailed path (no silent retry against a missing backend).
        """
        if self._client is not None:
            return self._client
        if not self.rpc_url:
            return None
        try:
            from solana.rpc.api import Client  # import-guarded
        except ImportError:
            logger.info("solana-py not installed; SolanaService.anchor_hash degrades")
            return None
        # Real Client construction needs solana-py installed (cloud only);
        # excluded from the offline coverage gate.
        try:  # pragma: no cover
            return Client(self.rpc_url)  # pragma: no cover
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning("solana client construction failed: %s", exc)  # pragma: no cover
            return None  # pragma: no cover

    def _signer_pubkey(self) -> str:
        """Return the platform signer's base58 pubkey (for the Memo instruction).

        Caller (``anchor_hash`` / ``issue_certificate``) guarantees
        ``self.signer is not None``; this helper only translates signer failures
        into ``AnchorFailed`` (kept SPEC-001-compatible) so the caller's single
        ``except AnchorFailed`` covers every signer failure mode.
        """
        # KmsSigner (SPEC-004) or any object exposing public_key().
        try:
            return self.signer.public_key()  # type: ignore[union-attr]
        except NotImplementedError as exc:
            raise AnchorFailed(
                "signer.public_key() is not implemented (KmsSigner stub)"
            ) from exc
        except AttributeError as exc:
            # Non-conforming signer object: degrade rather than crash.
            raise AnchorFailed(f"signer has no public_key(): {exc}") from exc
        except Exception as exc:
            # SPEC-004: KmsSigner now raises KmsSignerError when unconfigured.
            # Translate to AnchorFailed so SPEC-001's contract is unchanged.
            raise AnchorFailed(f"signer.public_key() failed: {exc}") from exc

    @staticmethod
    def _build_memo(image_sha256: str, creator_pubkey: str) -> str:
        """Build the Memo Program payload (architecture 4)."""
        return f"veriproof:anchor:{image_sha256}:{creator_pubkey}"

    def _send_memo(self, client: Any, memo: str, signer_pubkey: str) -> str:
        """Submit the Memo transaction via the client.

        Real path: builds a Transaction with ``spl.memo.instructions.create_memo``
        and signs it with ``self.signer``. Test path: the injected client exposes
        a simpler ``send_memo(memo, signer_pubkey)`` seam so unit tests can drive
        success/failure without the real SDK.
        """
        send = getattr(client, "send_memo", None)
        if send is not None:
            # Injected-stub seam (used by tests).
            return send(memo, signer_pubkey)
        # Real path: SDK packages are import-guarded so this branch only runs
        # when the full Solana toolchain is installed (cloud). Excluded from
        # the offline coverage gate.
        from solana.transaction import Transaction  # import-guarded  # pragma: no cover

        try:  # pragma: no cover
            from spl.memo.instructions import create_memo
        except ImportError as exc:  # pragma: no cover
            raise AnchorFailed("spl-token memo program not installed") from exc  # pragma: no cover
        tx = Transaction()  # pragma: no cover
        tx.add(create_memo(memo, signer_pubkey))  # pragma: no cover
        result = client.send_transaction(tx, self.signer.keypair)  # pragma: no cover
        sig = getattr(result, "value", result)  # pragma: no cover
        return str(sig)  # pragma: no cover


def get_solana_service() -> Any:
    """설정에 따라 로컬 mock 또는 실체인 Solana 어댑터를 선택한다."""
    from django.conf import settings

    adapter = getattr(settings, "SOLANA_ADAPTER", "mock").strip().lower()
    if adapter == "mock":
        from .mock_solana_service import LocalMockSolanaService

        return LocalMockSolanaService()
    if adapter != "real":
        raise ValueError(f"unsupported SOLANA_ADAPTER: {adapter}")
    from .kms_signer import get_kms_signer

    return SolanaService(
        rpc_url=getattr(settings, "SOLANA_RPC_URL", None),
        usdc_mint=getattr(settings, "USDC_MINT_ADDRESS", None),
        signer=get_kms_signer(),
    )
