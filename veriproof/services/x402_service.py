"""X402Service — a2a-x402 / AP2 protocol mapping (architecture 3, 4).

Builds the HTTP 402 ``payment-required`` envelope, parses the
``payment-submitted`` reply, and (optionally) signs AP2 Mandate VDCs when
``AP2_ENABLED``.

SPEC-002 implements:
- ``classify_client`` (R6/R7): agent vs browser classification from headers.
- ``build_payment_required`` (R3/R4/R5): the a2a-x402 402 envelope, using the
  shared Payment Recipient Resolution rule (architecture §8) for ``pay_to``.
- ``build_solana_pay_fallback`` (R7): the Solana Pay Buy-It-Now body for
  non-agent (browser) clients.

``parse_payment_submitted`` and ``build_ap2_mandate`` remain stubs (owned by
SPEC-003 / SPEC-004).
"""
from __future__ import annotations

import decimal
from typing import Any

from ._payment import resolve_pay_to
from ._types import SubmittedPayment

# SPEC-002: the a2a-x402 payment-required envelope version this server speaks.
X402_VERSION = "1"
# SPEC-002 R5: the single payment scheme VeriProof accepts on devnet.
SOLANA_USDC_SCHEME = "solana-usdc"
# SPEC-002 R7: human-readable label for the Solana Pay QR fallback.
SOLANA_PAY_LABEL = "VeriProof IP License"


class InvalidPaymentSubmitted(ValueError):
    """Raised when an a2a-x402 ``payment-submitted`` body cannot be parsed.

    SPEC-004: ``X402Service.parse_payment_submitted`` raises this on missing
    required fields or malformed values so the caller can map it to a 422/400.
    """


class X402Service:
    """Maps between VeriProof domain objects and the x402 / AP2 wire formats."""

    def __init__(
        self,
        ap2_enabled: bool | None = None,
        usdc_mint: str | None = None,
        network: str = "devnet",
        escrow_pubkey: str | None = None,
    ) -> None:
        self.ap2_enabled = ap2_enabled
        self.usdc_mint = usdc_mint
        self.network = network
        # SPEC-002 R5b: platform escrow pubkey used by the shared recipient
        # resolution rule when ``asset.parent_asset`` is set. ``None`` defers
        # to ``settings.PLATFORM_ESCROW_PUBKEY`` at call time.
        self.escrow_pubkey = escrow_pubkey

    # --- SPEC-002: client classification (R6 / R7) --------------------------

    def classify_client(self, request_or_headers: Any) -> str:
        """Classify a request as ``"agent"`` or ``"browser"``.

        Pure function over headers. Accepts either a Django ``HttpRequest``
        (reads ``request.headers``) or a plain headers dict.

        Rules (architecture §6 edge note + SPEC-002 R6/R7):
        - ``X-Agent-Protocol: x402`` -> agent
        - ``Accept`` contains ``application/json`` -> agent
        - ``Accept`` favours ``text/html`` -> browser
        - ambiguous (``*/*`` or missing) -> agent (conservative access control)

        Header lookup is case-insensitive for plain-dict inputs; Django's
        ``request.headers`` is already case-insensitive.
        """
        headers = getattr(request_or_headers, "headers", request_or_headers)

        def _get(name: str) -> str | None:
            if headers is None:
                return None
            # Django HttpHeaders + exact-casing dicts resolve via .get directly.
            val = headers.get(name)
            if val is not None:
                return val
            # Case-insensitive scan for plain dicts with different key casing.
            target = name.lower()
            for k, v in headers.items():
                if k.lower() == target:
                    return v
            return None

        x_agent = _get("X-Agent-Protocol")
        if x_agent and "x402" in x_agent.lower():
            return "agent"

        accept = (_get("Accept") or "").lower()
        if "application/json" in accept:
            return "agent"
        if "text/html" in accept:
            return "browser"

        # Ambiguous (``*/*`` or no Accept) defaults to agent per SPEC §6.
        return "agent"

    # --- SPEC-002: 402 envelope (R3 / R4 / R5 / R5b) ------------------------

    def build_payment_required(self, asset: Any) -> tuple[dict, dict]:
        """Build the 402 ``(headers, body)`` per architecture §3.1.

        Implements the single payment-recipient resolution rule (§8):
        ``parent_asset`` set -> escrow, else creator wallet.
        """
        asset_id = str(asset.id)
        # R5b: the shared SSOT helper; pass our resolved escrow pubkey so no
        # settings access is needed inside the hot path.
        pay_to = resolve_pay_to(asset, escrow_pubkey=self.escrow_pubkey)
        negotiate_endpoint = f"/api/v1/ip/{asset_id}/negotiate"
        settle_endpoint = f"/api/v1/ip/{asset_id}/settle"

        headers = {
            "X-402-Payment-Required": "true",
            "X-Agent-Protocol": "x402",
            "X-402-Negotiation-Endpoint": negotiate_endpoint,
            "X-Solana-Pay-Address": pay_to,
            "X-Payment-Mint": self.usdc_mint,
        }

        from services.preview_service import watermark_preview_url

        body = {
            "error": "Payment or License Required",
            "asset_id": asset_id,
            "preview_url": watermark_preview_url(asset.id),
            "x402_version": X402_VERSION,
            "accepts": [
                {
                    "scheme": SOLANA_USDC_SCHEME,
                    "network": self.network,
                    "mint": self.usdc_mint,
                    "pay_to": pay_to,
                    # R5 / AC: max_amount_required == target_price_usdc.
                    "max_amount_required": str(asset.target_price_usdc),
                }
            ],
            "how_to_negotiate": {
                "endpoint": negotiate_endpoint,
                "method": "POST",
                "required_payload": {
                    "buyer_agent_id": "string",
                    "offer_usdc": "float",
                    "usage_type": "string",
                },
                "settle_endpoint": settle_endpoint,
            },
        }
        return headers, body

    # --- SPEC-002: Solana Pay browser fallback (R7) -------------------------

    def build_solana_pay_fallback(self, asset: Any) -> dict:
        """Build the 200 Solana Pay Buy-It-Now body for non-agent clients.

        The fixed price equals ``asset.target_price_usdc``. The address is
        routed through the same shared recipient-resolution rule as the 402
        path so browser and agent clients cannot disagree on the recipient.
        """
        pay_to = resolve_pay_to(asset, escrow_pubkey=self.escrow_pubkey)
        amount_str = str(asset.target_price_usdc)
        uri = (
            f"solana-pay:{pay_to}?amount={amount_str}"
            f"&spl-token={self.usdc_mint}"
        )
        from services.preview_service import watermark_preview_url

        return {
            "status": "PAYMENT_REQUIRED",
            "asset_id": str(asset.id),
            "preview_url": watermark_preview_url(asset.id),
            "solana_pay": {
                "address": pay_to,
                "mint": self.usdc_mint,
                "amount_usdc": amount_str,
                "label": SOLANA_PAY_LABEL,
                "uri": uri,
            },
        }

    # --- SPEC-004: a2a-x402 payment-submitted parsing -----------------------

    def parse_payment_submitted(self, payload: dict) -> SubmittedPayment:
        """Parse an a2a-x402 ``payment-submitted`` / settle body. SPEC-004.

        Required fields: ``tx_signature``, ``buyer_wallet``. Optional
        ``amount_usdc`` (string or JSON number) is coerced to a Decimal. All
        other fields (``session_id``, ``asset_id``, ``network`` ...) are
        preserved in ``SubmittedPayment.extra``.

        Raises ``InvalidPaymentSubmitted`` on a non-object payload, missing
        required fields, or a non-numeric ``amount_usdc``.
        """
        if not isinstance(payload, dict):
            raise InvalidPaymentSubmitted(
                "payment-submitted payload must be a JSON object"
            )

        tx_signature = payload.get("tx_signature")
        buyer_wallet = payload.get("buyer_wallet")
        if not tx_signature or not isinstance(tx_signature, str):
            raise InvalidPaymentSubmitted(
                "tx_signature is required and must be a non-empty string"
            )
        if not buyer_wallet or not isinstance(buyer_wallet, str):
            raise InvalidPaymentSubmitted(
                "buyer_wallet is required and must be a non-empty string"
            )

        amount_usdc: decimal.Decimal | None = None
        if "amount_usdc" in payload and payload.get("amount_usdc") is not None:
            try:
                amount_usdc = decimal.Decimal(str(payload["amount_usdc"]))
            except (decimal.InvalidOperation, ValueError, TypeError) as exc:
                raise InvalidPaymentSubmitted(
                    f"amount_usdc is not a valid number: {exc}"
                ) from exc

        # Preserve the remaining fields for downstream settlement wiring.
        reserved = {"tx_signature", "buyer_wallet", "amount_usdc"}
        extra = {k: v for k, v in payload.items() if k not in reserved}

        return SubmittedPayment(
            tx_signature=tx_signature,
            buyer_wallet=buyer_wallet,
            amount_usdc=(
                amount_usdc if amount_usdc is not None else decimal.Decimal("0")
            ),
            extra=extra,
        )

    def build_ap2_mandate(self, session: Any, kind: str) -> dict | None:
        """Build an AP2 Cart/Payment Mandate VDC. Returns None if AP2 disabled.

        SPEC-003 R14. ``kind`` is ``"cart"`` (post-negotiation, pre-payment) or
        ``"payment"`` (settlement, SPEC-004). When ``ap2_enabled`` is falsy this
        is a no-op (returns ``None``) so the local/TDD path is unaffected.

        The returned dict is a minimal Verifiable Digital Credential shaped
        after the AP2 Cart Mandate: context, type, kind, the negotiated amount,
        the resolved recipient (via the shared ``resolve_pay_to`` SSOT so the
        mandate can never disagree with the 402 / negotiate recipient), mint,
        and network. Signing/issuance as a verifiable credential is a cloud
        concern (KMS); this method produces the unsigned mandate body only.
        """
        if not self.ap2_enabled:
            return None

        kind = kind or "cart"
        asset = getattr(session, "asset", None)
        # Prefer the session's final price (set on ACCEPT); fall back to the
        # initial offer so a cart mandate is always populated with a concrete
        # amount.
        amount = getattr(session, "final_price_usdc", None)
        if amount is None:
            amount = getattr(session, "initial_offer_usdc", None)

        mandate = {
            "@context": ["https://ap2.dev/2024/1/mandate"],
            "type": "CartMandate" if kind == "cart" else "PaymentMandate",
            "kind": kind,
            "asset_id": str(getattr(asset, "id", None)) if asset is not None else None,
            "session_id": str(getattr(session, "id", None)),
            "buyer_agent_id": getattr(session, "buyer_agent_id", None),
            "usage_type": getattr(session, "usage_type", None),
            "amount_usdc": str(amount) if amount is not None else None,
            "mint": self.usdc_mint,
            "network": self.network,
        }
        # Recipient goes through the shared rule so it matches the 402 envelope
        # and the negotiation ACCEPT pay_address (architecture §8).
        if asset is not None:
            mandate["pay_to"] = resolve_pay_to(asset, escrow_pubkey=self.escrow_pubkey)
        else:
            mandate["pay_to"] = None
        return mandate


def get_x402_service() -> X402Service:
    """Factory: build an X402Service from current Django settings."""
    from django.conf import settings

    return X402Service(
        ap2_enabled=getattr(settings, "AP2_ENABLED", False),
        usdc_mint=getattr(settings, "USDC_MINT_ADDRESS", None),
        escrow_pubkey=getattr(settings, "PLATFORM_ESCROW_PUBKEY", None),
    )
