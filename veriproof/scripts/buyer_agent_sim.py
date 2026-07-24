"""Buyer-agent simulator for the VeriProof sandbox / E2E (architecture 7).

A standalone, dependency-light CLI that drives an a2a-x402 client through the
full agent flow against a RUNNING VeriProof server:

    1. GET  /api/v1/ip/{asset_id}            (X-Agent-Protocol: x402) -> 402
    2. POST /api/v1/ip/{asset_id}/negotiate   (buyer offer)            -> ACCEPT
    3. POST /api/v1/ip/{asset_id}/settle      (confirmed tx signature) -> SUCCESS

This mirrors what the in-process ``SandboxRunner`` (apps.sandbox.services)
does, but over real HTTP, so it doubles as an E2E smoke driver. It uses only
the Python standard library (``urllib``) so it runs without installing
``x402_a2a`` or ``requests`` — the equivalent 3-step x402 flow is implemented
inline. If ``x402_a2a`` is installed it can be swapped in for richer envelope
handling.

Usage (start the Django server first, e.g. ``manage.py runserver``):

    python scripts/buyer_agent_sim.py \\
        --base http://127.0.0.1:8000 \\
        --asset <uuid> \\
        --offer 1.5 \\
        --usage commercial \\
        --buyer-wallet <pubkey> --tx-signature <confirmed_signature>

Exit codes: 0 success, 1 a step failed, 2 network/usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

AGENT_HEADERS = {"X-Agent-Protocol": "x402", "Accept": "application/json"}


def _request(method: str, url: str, *, body: dict | None = None) -> tuple[int, dict, dict]:
    """Perform an HTTP request; return (status, json_body, headers)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = dict(AGENT_HEADERS)
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            parsed = json.loads(raw) if raw else {}
            return resp.status, parsed, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError:
            parsed = {"raw": raw.decode("utf-8", "replace")}
        return exc.code, parsed, dict(exc.headers.items())


def run(base_url: str, asset_id: str, offer: float, usage: str,
        buyer_wallet: str, tx_signature: str) -> int:
    """Execute the 3-step x402 flow; return process exit code."""
    base = base_url.rstrip("/")
    asset_path = f"{base}/api/v1/ip/{asset_id}"
    negotiate_path = f"{base}/api/v1/ip/{asset_id}/negotiate"
    settle_path = f"{base}/api/v1/ip/{asset_id}/settle"

    # Step 1: GET -> observe the 402 payment-required envelope.
    status, body, headers = _request("GET", asset_path)
    print(f"[1] GET {asset_path} -> {status}")
    if status == 404:
        print("    asset not found")
        return 1
    if status != 402:
        print(f"    expected 402, got {status}: {body}")
        return 1
    print(f"    pay_to={headers.get('X-Solana-Pay-Address')} "
          f"negotiate={headers.get('X-402-Negotiation-Endpoint')}")

    # Step 2: negotiate the buyer's offer.
    status, body, _ = _request("POST", negotiate_path, body={
        "buyer_agent_id": "buyer-agent-sim",
        "offer_usdc": str(offer),
        "usage_type": usage,
    })
    print(f"[2] POST {negotiate_path} -> {status} {body.get('status')}")
    if status != 200 or body.get("status") != "ACCEPT":
        print(f"    negotiation not accepted: {body}")
        return 1
    session_id = body.get("session_id")
    price = body.get("price_usdc")

    # Step 3: settle only with a transaction that was actually submitted by the buyer.
    status, body, _ = _request("POST", settle_path, body={
        "session_id": session_id,
        "tx_signature": tx_signature,
        "buyer_wallet": buyer_wallet,
        "amount_usdc": price,
    })
    print(f"[3] POST {settle_path} -> {status} {body.get('status')}")
    if status != 200:
        print(f"    settlement failed: {body}")
        return 1
    print(f"    certificate_tx={body.get('certificate_tx')} "
          f"download_url={body.get('download_url')}")
    print("[buyer_agent_sim] flow complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VeriProof buyer-agent simulator")
    parser.add_argument("--base", default="http://127.0.0.1:8000",
                        help="server base URL (default %(default)s)")
    parser.add_argument("--asset", required=True, help="target asset uuid")
    parser.add_argument("--offer", type=float, default=1.5, help="initial offer USDC")
    parser.add_argument("--usage", default="commercial", help="usage type")
    parser.add_argument("--buyer-wallet", required=True, help="buyer Solana public key")
    parser.add_argument("--tx-signature", required=True, help="confirmed Solana payment transaction")
    args = parser.parse_args(argv)

    if args.offer <= 0:
        print("--offer must be positive", file=sys.stderr)
        return 2
    try:
        return run(args.base, args.asset, args.offer, args.usage,
                   args.buyer_wallet, args.tx_signature)
    except urllib.error.URLError as exc:
        print(f"network error: {exc} (is the server running at {args.base}?)",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
