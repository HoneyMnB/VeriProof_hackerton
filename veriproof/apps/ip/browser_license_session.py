"""Browser-session binding for public Solana Pay requests."""
from __future__ import annotations

import datetime
from typing import Any

from apps.settlement.models import License
from services.license_service import get_license_service

_PAYMENT_REQUESTS_SESSION_KEY = "vp_browser_solpay_requests"
_PAYMENT_REQUEST_TTL = datetime.timedelta(minutes=30)


def remember_browser_payment_request(request: Any, asset: Any, reference: str) -> None:
    """Bind a generated Solana Pay reference to the current browser session."""
    session = getattr(request, "session", None)
    if session is None:
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    requests = _active_payment_requests(session.get(_PAYMENT_REQUESTS_SESSION_KEY, {}), now)
    requests[reference] = {"asset_id": str(asset.id), "created_at": now.isoformat()}
    session[_PAYMENT_REQUESTS_SESSION_KEY] = requests
    session.modified = True


def has_browser_payment_request(request: Any, asset: Any, reference: str) -> bool:
    """Return whether ``reference`` is a current-session request for ``asset``."""
    session = getattr(request, "session", None)
    if session is None:
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    requests = _active_payment_requests(session.get(_PAYMENT_REQUESTS_SESSION_KEY, {}), now)
    session[_PAYMENT_REQUESTS_SESSION_KEY] = requests
    session.modified = True
    request_data = requests.get(reference)
    return bool(request_data and request_data.get("asset_id") == str(asset.id))


def get_active_browser_license(request: Any, asset: Any) -> License | None:
    """Return the signed-in buyer's current active License for ``asset``."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    licenses = License.objects.filter(asset=asset, buyer_user=user).order_by("-granted_at")
    for license in licenses:
        if get_license_service().is_download_active(license):
            return license
    return None


def _active_payment_requests(value: Any, now: datetime.datetime) -> dict[str, dict[str, str]]:
    requests = value if isinstance(value, dict) else {}
    active: dict[str, dict[str, str]] = {}
    for reference, request_data in requests.items():
        if not isinstance(reference, str) or not isinstance(request_data, dict):
            continue
        created_at = _parse_timestamp(request_data.get("created_at"))
        asset_id = request_data.get("asset_id")
        if created_at is None or not isinstance(asset_id, str):
            continue
        if now - created_at <= _PAYMENT_REQUEST_TTL:
            active[reference] = {"asset_id": asset_id, "created_at": created_at.isoformat()}
    return active


def _parse_timestamp(value: Any) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.timezone.utc)
