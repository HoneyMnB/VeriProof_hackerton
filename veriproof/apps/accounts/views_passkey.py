"""Django HTTP boundaries for passkey registration and authentication."""
from __future__ import annotations

import base64
import json

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpRequest, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from webauthn.helpers.exceptions import WebAuthnException

from .models import PasskeyCredential
from .passkeys import PasskeyCeremonyError, authentication_options, consume_ceremony
from .passkeys import registration_options, verify_authentication, verify_registration


def _payload(request: HttpRequest) -> dict:
    try:
        value = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        raise PasskeyCeremonyError("Request body is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise PasskeyCeremonyError("Request body must be a JSON object.")
    return value


def _error(exc: Exception, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": "passkey_failed", "detail": str(exc)}, status=status)


@login_required
@require_GET
def credential_list(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"items": [
        {
            "id": item.pk, "device_name": item.device_name,
            "created_at": item.created_at.isoformat(),
            "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None,
            "backed_up": item.backed_up,
        }
        for item in request.user.passkey_credentials.all()
    ]})


@login_required
@require_POST
def registration_begin(request: HttpRequest) -> JsonResponse:
    return JsonResponse(json.loads(registration_options(
        request, request.user, request.user.passkey_credentials.all()
    )))


@login_required
@require_POST
def registration_complete(request: HttpRequest) -> JsonResponse:
    try:
        payload = _payload(request)
        challenge, ceremony = consume_ceremony(request, "register")
        verified = verify_registration(request, payload["credential"], challenge)
        handle = _decode(ceremony["user_handle"])
        response = payload["credential"].get("response", {})
        device_name = str(payload.get("device_name") or "Passkey").strip()[:80] or "Passkey"
        with transaction.atomic():
            PasskeyCredential.objects.create(
                user=request.user, user_handle=handle,
                credential_id=verified.credential_id,
                public_key=verified.credential_public_key,
                sign_count=verified.sign_count,
                transports=response.get("transports") or [],
                device_name=device_name,
                device_type=getattr(verified.credential_device_type, "value", ""),
                backed_up=verified.credential_backed_up,
            )
    except (KeyError, TypeError, ValueError, IntegrityError, PasskeyCeremonyError, WebAuthnException) as exc:
        return _error(exc)
    return JsonResponse({"status": "registered"}, status=201)


@require_POST
def authentication_begin(request: HttpRequest) -> JsonResponse:
    try:
        payload = _payload(request)
        target = str(payload.get("next") or "/")
        request.session["passkey_next"] = target if url_has_allowed_host_and_scheme(
            target, {request.get_host()}, request.is_secure()
        ) else "/"
        return JsonResponse(json.loads(authentication_options(request)))
    except PasskeyCeremonyError as exc:
        return _error(exc)


@require_POST
def authentication_complete(request: HttpRequest) -> JsonResponse:
    try:
        payload = _payload(request)
        raw_id = payload["credential"].get("rawId") or payload["credential"].get("id")
        stored = PasskeyCredential.objects.select_related("user").get(credential_id=_decode(raw_id))
        asserted_handle = payload["credential"].get("response", {}).get("userHandle")
        if asserted_handle and _decode(asserted_handle) != bytes(stored.user_handle):
            raise PasskeyCeremonyError("Passkey user handle does not match the credential.")
        challenge, _ = consume_ceremony(request, "authenticate")
        verified = verify_authentication(request, payload["credential"], stored, challenge)
        stored.sign_count = verified.new_sign_count
        stored.last_used_at = timezone.now()
        stored.save(update_fields=["sign_count", "last_used_at"])
        login(request, stored.user)
        redirect_to = request.session.pop("passkey_next", reverse("accounts:login"))
    except PasskeyCredential.DoesNotExist:
        return _error(PasskeyCeremonyError("Passkey is not registered."))
    except (KeyError, TypeError, ValueError, PasskeyCeremonyError, WebAuthnException) as exc:
        return _error(exc)
    return JsonResponse({"status": "authenticated", "redirect": redirect_to})


def _decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise PasskeyCeremonyError("Credential identifier is missing.")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
