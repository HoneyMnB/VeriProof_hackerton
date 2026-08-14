"""WebAuthn ceremonies for Django-native passkey authentication."""
from __future__ import annotations

import base64
import json
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone
from webauthn import generate_authentication_options, generate_registration_options, options_to_json
from webauthn import verify_authentication_response, verify_registration_response
from webauthn.helpers.structs import AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor
from webauthn.helpers.structs import ResidentKeyRequirement, UserVerificationRequirement

CEREMONY_SESSION_KEY = "passkey_ceremony"
CEREMONY_TTL = timedelta(minutes=5)


class PasskeyCeremonyError(ValueError):
    """Raised when a WebAuthn ceremony is missing, stale, or mismatched."""


def context_for_request(request) -> tuple[str, list[str]]:
    origins = [value.strip().rstrip("/") for value in settings.PASSKEY_ORIGINS if value.strip()]
    if not origins:
        origins = [f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"]
    rp_id = settings.PASSKEY_RP_ID or (urlsplit(origins[0]).hostname or "")
    if not rp_id:
        raise PasskeyCeremonyError("Passkey RP ID is not configured.")
    return rp_id, origins


def registration_options(request, user, credentials) -> str:
    rp_id, _ = context_for_request(request)
    existing = list(credentials)
    user_id = bytes(existing[0].user_handle) if existing else None
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=settings.PASSKEY_RP_NAME,
        user_id=user_id,
        user_name=user.get_username(),
        user_display_name=user.get_full_name() or user.get_username(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=bytes(item.credential_id)) for item in existing],
    )
    _store_ceremony(
        request, "register", options.challenge, user_id=user.pk,
        user_handle=_b64url(options.user.id),
    )
    return options_to_json(options)


def authentication_options(request) -> str:
    rp_id, _ = context_for_request(request)
    options = generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    _store_ceremony(request, "authenticate", options.challenge)
    return options_to_json(options)


def consume_ceremony(request, expected_kind: str) -> tuple[bytes, dict]:
    ceremony = request.session.pop(CEREMONY_SESSION_KEY, None)
    request.session.modified = True
    if not ceremony or ceremony.get("kind") != expected_kind:
        raise PasskeyCeremonyError("Passkey ceremony is missing or has already been used.")
    try:
        created_at = timezone.datetime.fromisoformat(ceremony["created_at"])
        challenge = _b64url_decode(ceremony["challenge"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PasskeyCeremonyError("Passkey ceremony data is invalid.") from exc
    if timezone.now() - created_at > CEREMONY_TTL:
        raise PasskeyCeremonyError("Passkey ceremony has expired.")
    if expected_kind == "register" and ceremony.get("user_id") != request.user.pk:
        raise PasskeyCeremonyError("Passkey ceremony belongs to another account.")
    return challenge, ceremony


def verify_registration(request, credential, expected_challenge: bytes):
    rp_id, origins = context_for_request(request)
    return verify_registration_response(
        credential=credential, expected_challenge=expected_challenge,
        expected_rp_id=rp_id, expected_origin=origins, require_user_verification=True,
    )


def verify_authentication(request, credential, stored, expected_challenge: bytes):
    rp_id, origins = context_for_request(request)
    return verify_authentication_response(
        credential=credential, expected_challenge=expected_challenge,
        expected_rp_id=rp_id, expected_origin=origins,
        credential_public_key=bytes(stored.public_key),
        credential_current_sign_count=stored.sign_count,
        require_user_verification=True,
    )


def _store_ceremony(
    request, kind: str, challenge: bytes, user_id: int | None = None,
    user_handle: str | None = None,
) -> None:
    request.session[CEREMONY_SESSION_KEY] = {
        "kind": kind, "challenge": _b64url(challenge),
        "created_at": timezone.now().isoformat(), "user_id": user_id,
        "user_handle": user_handle,
    }


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
