"""Fake service adapters for offline TDD (test-plan 4, 5).

Each fake implements the real service INTERFACE (same method names/signatures)
so tests can swap it in via constructor injection. Fakes record every call and
support failure injection (e.g. ``FakeGeminiService(fail_analyze=True)``) to
exercise the degrade/fallback matrix in test-plan 5.

No external libraries are imported; these are plain Python.
"""
from __future__ import annotations

import decimal
import secrets
from typing import Any

from services._types import (
    AnalysisResult,
    BatchQuote,
    NegotiationResult,
    PaymentVerification,
    SubmittedPayment,
)


# === Gemini ==================================================================


class FakeGeminiService:
    """Fake GeminiService. Records calls; injects failures on demand."""

    def __init__(
        self,
        fail_analyze: bool = False,
        fail_negotiate: bool = False,
        fail_quote: bool = False,
    ) -> None:
        self.fail_analyze = fail_analyze
        self.fail_negotiate = fail_negotiate
        self.fail_quote = fail_quote
        self.calls: list[tuple[str, tuple, dict]] = []
        # Overridable canned results.
        self.analyze_result = AnalysisResult(
            tags=["test"], category="photography", originality_score=80,
            recommended_min_price_usdc=decimal.Decimal("1.00"),
            description="a test asset",
        )
        self.negotiate_result = NegotiationResult(
            status="ACCEPT", price_usdc=decimal.Decimal("1.50"),
            reason="fake accept",
        )

    def _record(self, name: str, args: tuple, kwargs: dict) -> None:
        self.calls.append((name, args, kwargs))

    def assist_with_attachments(self, context, message, files) -> str:
        self._record("assist_with_attachments", (context, message, files), {})
        return "Reviewed the attached file(s)."

    def analyze_asset(self, file_bytes: bytes, mime_type: str) -> AnalysisResult:
        """등록 서비스의 멀티모달 분석 계약을 외부 키 없이 재현한다.

        실제 Gemini 호출을 흉내 내는 것이 아니라, 테스트가 분석 성공·실패 뒤의
        등록 파이프라인만 결정적으로 검증할 수 있게 한다.
        """
        self._record("analyze_asset", (file_bytes, mime_type), {})
        if self.fail_analyze:
            raise RuntimeError("FakeGeminiService: forced analyze failure")
        return self.analyze_result

    def negotiate(
        self,
        min_price: decimal.Decimal,
        target_price: decimal.Decimal,
        offer_usdc: decimal.Decimal,
        usage_type: str,
        history: list[dict],
    ) -> NegotiationResult:
        self._record(
            "negotiate",
            (min_price, target_price, offer_usdc, usage_type, history),
            {},
        )
        if self.fail_negotiate:
            raise RuntimeError("FakeGeminiService: forced negotiate failure")
        return self.negotiate_result

    def quote_batch(self, items: list[dict], usage_type: str) -> list[BatchQuote]:
        self._record("quote_batch", (items, usage_type), {})
        if self.fail_quote:
            raise RuntimeError("FakeGeminiService: forced quote failure")
        return [
            BatchQuote(asset_id=item["asset_id"], unit_price_usdc=decimal.Decimal("0.05"))
            for item in items
        ]


# === Solana ==================================================================


class FakeSolanaService:
    """Fake SolanaService. Records calls; injects failures on demand."""

    def __init__(
        self,
        fail_anchor: bool = False,
        fail_verify: bool = False,
        fail_issue_cert: bool = False,
        fail_transfer: bool = False,
    ) -> None:
        self.fail_anchor = fail_anchor
        self.fail_verify = fail_verify
        self.fail_issue_cert = fail_issue_cert
        self.fail_transfer = fail_transfer
        self.calls: list[tuple[str, tuple, dict]] = []
        self._sig_counter = 0
        # Overridable verification result (valid by default).
        self.verification = None  # set per-call below

    def _record(self, name: str, args: tuple, kwargs: dict) -> None:
        self.calls.append((name, args, kwargs))

    def _next_sig(self, prefix: str) -> str:
        self._sig_counter += 1
        return f"{prefix}_fake_sig_{self._sig_counter}"

    def anchor_hash(self, image_sha256: str, creator_pubkey: str) -> str:
        self._record("anchor_hash", (image_sha256, creator_pubkey), {})
        if self.fail_anchor:
            # SPEC-001 R14: simulate "all retries exhausted" by raising the
            # real AnchorFailed exception type so the register view's
            # ``except AnchorFailed`` catches it uniformly.
            from services.solana_service import AnchorFailed

            raise AnchorFailed("FakeSolanaService: forced anchor failure")
        return self._next_sig("anchor")

    def verify_usdc_payment(
        self,
        tx_sig: str,
        expected_recipient: str,
        expected_amount: decimal.Decimal,
        mint: str,
    ) -> PaymentVerification:
        self._record(
            "verify_usdc_payment",
            (tx_sig, expected_recipient, expected_amount, mint),
            {},
        )
        if self.fail_verify:
            raise RuntimeError("FakeSolanaService: forced verify failure")
        if self.verification is not None:
            return self.verification
        # Default: a valid payment matching the expected amount/recipient.
        return PaymentVerification(
            is_valid=True,
            amount=expected_amount,
            sender="FAKEBUYER111111111111111111111111111111111",
            slot=123_456_789,
        )

    def issue_certificate(self, asset_id: Any, buyer_pubkey: str, memo: str) -> str:
        self._record("issue_certificate", (asset_id, buyer_pubkey, memo), {})
        if self.fail_issue_cert:
            # SPEC-004 R16: raise the real CertificateIssueError so the
            # pipeline's ``except CertificateIssueError`` catches it uniformly
            # (mirrors how fail_anchor raises the real AnchorFailed).
            from services.solana_service import CertificateIssueError

            raise CertificateIssueError(
                "FakeSolanaService: forced issue_cert failure"
            )
        return self._next_sig("cert")

    def issue_registration_certificate(
        self, asset_id: Any, creator_pubkey: str, content_sha256: str
    ) -> str:
        self._record(
            "issue_registration_certificate",
            (asset_id, creator_pubkey, content_sha256),
            {},
        )
        if self.fail_issue_cert:
            from services.solana_service import CertificateIssueError

            raise CertificateIssueError(
                "FakeSolanaService: forced registration certificate failure"
            )
        return self._next_sig("registration_cert")

    def transfer_usdc(self, to_pubkey: str, amount: decimal.Decimal) -> str:
        self._record("transfer_usdc", (to_pubkey, amount), {})
        if self.fail_transfer:
            raise RuntimeError("FakeSolanaService: forced transfer failure")
        return self._next_sig("transfer")


# === Storage =================================================================


class FakeStorageService:
    """Fake StorageService holding artifacts in memory keyed by asset_id."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.permanent: dict[tuple[str, Any], bytes] = {}
        self.temporary: dict[Any, bytes] = {}
        self.purged: set[Any] = set()

    def _record(self, name: str, args: tuple, kwargs: dict) -> None:
        self.calls.append((name, args, kwargs))

    def save_permanent(self, kind: str, asset_id: Any, data: bytes) -> str:
        self._record("save_permanent", (kind, asset_id, data), {})
        self.permanent[(kind, asset_id)] = data
        return f"memory://{kind}/{asset_id}"

    def save_temporary(
        self, asset_id: Any, data: bytes, ttl, content_mime_type: str | None = None
    ) -> str:
        self._record("save_temporary", (asset_id, data, ttl, content_mime_type), {})
        self.temporary[asset_id] = data
        return f"memory://original/{asset_id}"

    def purge_original(self, asset_id: Any) -> None:
        self._record("purge_original", (asset_id,), {})
        self.temporary.pop(asset_id, None)
        self.purged.add(asset_id)

    def signed_download_url(self, asset_id: Any, ttl) -> str | None:
        self._record("signed_download_url", (asset_id, ttl), {})
        if asset_id in self.temporary:
            return f"memory://signed/{asset_id}"
        return None

    def read_temporary(self, asset_id: Any) -> bytes | None:
        """Return the temporary original bytes, or None if purged/missing.

        SPEC-004: the download view streams the original via this seam. A
        missing (purged) original yields None so the view can return 410.
        """
        self._record("read_temporary", (asset_id,), {})
        return self.temporary.get(asset_id)

    def read_permanent(self, kind: str, asset_id: Any) -> bytes | None:
        """보호된 미리보기 경계 테스트를 위한 영속 아티팩트 읽기."""
        self._record("read_permanent", (kind, asset_id), {})
        return self.permanent.get((kind, asset_id))

    def has_temporary(self, asset_id: Any) -> bool:
        """Mirror of StorageService.has_temporary for the download purge check."""
        return asset_id in self.temporary


# === GCP sinks (Firestore / BigQuery / PubSub) ==============================


class FakeFirestore:
    """Fake FirestoreMirror. Records upserts in an in-memory dict."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.docs: dict[tuple[str, str], dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def set(self, collection: str, doc_id: str, data: dict) -> None:
        self.calls.append(("set", {"collection": collection, "doc_id": doc_id, "data": data}))
        if not self.enabled:
            return None
        self.docs[(collection, doc_id)] = data
        return None


class FakeBigQuery:
    """Fake BigQuerySink. Records inserted rows per table."""

    def __init__(self, dataset: str = "veriproof_analytics") -> None:
        self.dataset = dataset
        self.rows: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, dict]] = []

    def insert(self, table: str, row: dict) -> None:
        self.calls.append(("insert", {"table": table, "row": row}))
        if not self.dataset:
            return None
        self.rows.setdefault(table, []).append(row)
        return None


class FakePubSub:
    """Fake PubSubPublisher. Records published messages."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self._counter = 0

    def publish(self, topic: str, message: dict) -> str:
        self._counter += 1
        msg_id = f"fake-msg-{self._counter}"
        self.published.append((topic, message))
        return msg_id


# === Royalty =================================================================


class FakeRoyaltyService:
    """Fake RoyaltyService. Records distribute() calls (SPEC-004 R14b / SPEC-008).

    ``distribute`` stays a stub in real code (owned by SPEC-008); the settlement
    pipeline only CALLS it for 2nd-creation assets, and tests assert that here.
    """

    def __init__(self, fail_distribute: bool = False) -> None:
        self.fail_distribute = fail_distribute
        self.calls: list[Any] = []

    def distribute(self, license: Any) -> list[Any]:
        self.calls.append(license)
        if self.fail_distribute:
            raise RuntimeError("FakeRoyaltyService: forced distribute failure")
        return []


# === License service =========================================================


class FakeLicenseService:
    """Fake LicenseService implementing grant() so the download/webhook tests
    can drive the pipeline end-to-end without DB idempotency mechanics.

    Mirrors the real ``LicenseService.grant`` contract: idempotent on
    ``payment_tx``, issues an expiring download token.

    SPEC-007: ``fail_on_asset_ids`` injects a per-asset grant failure so the
    batch partial-failure path (R8/AC-8) can be exercised without a DB-level
    fault. A failed asset raises ``RuntimeError`` on every grant attempt.
    """

    def __init__(
        self,
        download_token_ttl_seconds: int = 3600,
        fail_on_asset_ids: set[Any] | None = None,
    ) -> None:
        self.download_token_ttl_seconds = download_token_ttl_seconds
        self.fail_on_asset_ids = set(fail_on_asset_ids or ())
        self.calls: list[tuple[tuple, dict]] = []
        self._granted: dict[str, Any] = {}

    def grant(
        self,
        asset: Any,
        buyer_wallet: str,
        price: decimal.Decimal,
        usage_type: str,
        payment_tx: str,
        session: Any = None,
        buyer_user: Any = None,
    ) -> Any:
        import datetime as _dt

        self.calls.append(
            (
                (asset, buyer_wallet, price, usage_type, payment_tx),
                {"session": session, "buyer_user": buyer_user},
            )
        )
        # SPEC-007 R8/AC-8: injected per-asset failure for partial-settle tests.
        asset_id = getattr(asset, "id", None)
        if asset_id in self.fail_on_asset_ids:
            raise RuntimeError(
                f"FakeLicenseService: forced grant failure for asset {asset_id}"
            )
        if payment_tx in self._granted:
            return self._granted[payment_tx]
        # Build a License-like stand-in carrying the fields the pipeline reads.
        import uuid as _uuid

        class _License:
            pass

        lic = _License()
        lic.id = _uuid.uuid4()
        lic.asset = asset
        lic.session = session
        lic.buyer_user = buyer_user
        lic.buyer_wallet = buyer_wallet
        lic.price_usdc = price
        lic.usage_type = usage_type
        lic.payment_tx_sig = payment_tx
        lic.certificate_tx_sig = None
        lic.download_token = secrets.token_urlsafe(24)
        lic.download_expires_at = (
            _dt.datetime.now(_dt.timezone.utc)
            + _dt.timedelta(seconds=self.download_token_ttl_seconds)
        )
        lic.granted_at = _dt.datetime.now(_dt.timezone.utc)
        lic.asset_id = getattr(asset, "id", None)
        self._granted[payment_tx] = lic
        return lic

    def is_licensed(self, asset: Any, tx_sig: str) -> bool:
        return tx_sig in self._granted


# === X402 parse helper ======================================================


def make_submitted_payment(
    tx_signature: str = "fake_tx_sig",
    buyer_wallet: str = "FAKEBUYER111111111111111111111111111111111",
    amount_usdc: str = "1.50",
) -> SubmittedPayment:
    """Build a SubmittedPayment value-object for X402 parse tests."""
    return SubmittedPayment(
        tx_signature=tx_signature,
        buyer_wallet=buyer_wallet,
        amount_usdc=decimal.Decimal(amount_usdc),
    )
