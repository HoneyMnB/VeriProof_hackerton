"""로그인·가입·계정 설정 HTTP 경계."""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from .forms import EmailAuthenticationForm, EmailSignUpForm
from .models import UserPreference, WalletConfiguration
from .services import (
    encrypt_wallet_private_address,
    ensure_creator_wallet,
    is_valid_solana_address,
    private_address_matches_wallet,
)

logger = logging.getLogger(__name__)


def _safe_next(request: HttpRequest, fallback: str = "/") -> str:
    """외부 리디렉션을 허용하지 않고 내부 다음 화면만 돌려준다."""
    candidate = request.POST.get("next") or request.GET.get("next") or fallback
    if url_has_allowed_host_and_scheme(candidate, {request.get_host()}, request.is_secure()):
        return candidate
    return fallback


def login_view(request: HttpRequest) -> HttpResponse:
    """세션 로그인을 처리하고 안전한 내부 화면으로 이동한다."""
    if request.user.is_authenticated:
        return redirect(_safe_next(request))
    form = EmailAuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        login(request, user)
        logger.info("account login user=%s", user.get_username())
        return redirect(_safe_next(request))
    return render(request, "accounts/login.html", {"form": form, "next": _safe_next(request), "debug": settings.DEBUG})


def signup_view(request: HttpRequest) -> HttpResponse:
    """창작자 계정을 만든 뒤 즉시 로그인한다."""
    if request.user.is_authenticated:
        return redirect("/")
    form = EmailSignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        authenticated = authenticate(request, username=user.email, password=form.cleaned_data["password1"])
        if authenticated is not None:
            login(request, authenticated)
        logger.info("account created user=%s", user.get_username())
        return redirect(_safe_next(request))
    return render(request, "accounts/signup.html", {"form": form, "next": _safe_next(request)})


@require_POST
def developer_login(request: HttpRequest) -> HttpResponse:
    """DEBUG 로컬 시연에서만 고정 개발자 계정을 만들어 빠르게 로그인한다."""
    if not settings.DEBUG:
        return redirect(reverse("accounts:login"))
    from .services import ensure_developer_account

    user = ensure_developer_account()
    login(request, user)
    logger.info("developer quick login user=%s", user.email)
    return redirect(_safe_next(request))


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """세션을 종료해 다른 판매자 계정으로 안전하게 전환한다."""
    username = request.user.get_username() if request.user.is_authenticated else "anonymous"
    logout(request)
    logger.info("account logout user=%s", username)
    return redirect(reverse("accounts:login"))


@login_required
@require_POST
def preferences(request: HttpRequest) -> JsonResponse:
    """계정 모달에서 전달한 표시명·복구 연락처·언어·지갑을 원자적으로 저장한다."""
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid_json", "detail": "Invalid settings payload."}, status=400)
    language = str(payload.get("language") or "").strip()
    if language not in {choice for choice, _ in UserPreference.LANGUAGE_CHOICES}:
        return JsonResponse({"error": "invalid_language", "detail": "Unsupported language."}, status=400)
    display_name = str(payload.get("display_name") or "").strip()
    recovery_email = str(payload.get("recovery_email") or "").strip().lower()
    contact_phone = str(payload.get("contact_phone") or "").strip()
    wallet = str(payload.get("creator_wallet") or "").strip()
    if len(display_name) > 80 or len(wallet) > 64 or len(contact_phone) > 30:
        return JsonResponse({"error": "invalid_value", "detail": "A settings value is too long."}, status=400)
    if wallet and not is_valid_solana_address(wallet):
        return JsonResponse({"error": "invalid_wallet", "detail": "Enter a valid Solana public address."}, status=400)
    if recovery_email:
        try:
            validate_email(recovery_email)
        except ValidationError:
            return JsonResponse({"error": "invalid_recovery_email", "detail": "Enter a valid recovery email."}, status=400)
    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    preference.display_name = display_name or request.user.get_username()
    preference.language = language
    preference.recovery_email = recovery_email
    preference.contact_phone = contact_phone
    preference.creator_wallet = wallet
    preference.save(update_fields=["display_name", "language", "recovery_email", "contact_phone", "creator_wallet", "updated_at"])
    if wallet:
        ensure_creator_wallet(wallet)
    logger.info("account settings updated user=%s language=%s", request.user.get_username(), language)
    return JsonResponse({"display_name": preference.display_name, "language": preference.language, "recovery_email": preference.recovery_email, "contact_phone": preference.contact_phone, "creator_wallet": preference.creator_wallet})


@login_required
@require_POST
def wallet_configurations(request: HttpRequest) -> JsonResponse:
    """Store a verified wallet secret and make the first address active."""
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid_json", "detail": "Invalid wallet payload."}, status=400)
    address = str(payload.get("address") or "").strip()
    label = str(payload.get("label") or "").strip()
    private_address = str(payload.get("private_address") or "").strip()
    if not is_valid_solana_address(address):
        return JsonResponse({"error": "invalid_wallet", "detail": "Enter a valid Solana public address."}, status=400)
    if not label or len(label) > 40:
        return JsonResponse({"error": "invalid_label", "detail": "Wallet name must be between 1 and 40 characters."}, status=400)
    with transaction.atomic():
        wallet = WalletConfiguration.objects.select_for_update().filter(user=request.user).first()
        created = wallet is None
        is_address_change = wallet is not None and wallet.address != address
        if (wallet is None or is_address_change) and not private_address_matches_wallet(private_address, address):
            return JsonResponse({"error": "invalid_private_address", "detail": "Enter the private key for this Solana public address."}, status=400)
        if private_address and not private_address_matches_wallet(private_address, address):
            return JsonResponse({"error": "invalid_private_address", "detail": "Enter the private key for this Solana public address."}, status=400)
        if wallet is not None and is_address_change and WalletConfiguration.objects.filter(user=request.user, address=address).exclude(pk=wallet.pk).exists():
            return JsonResponse({"error": "wallet_already_registered", "detail": "This wallet is already registered."}, status=400)
        if wallet is not None and not private_address and not wallet.private_address:
            return JsonResponse({"error": "private_address_required", "detail": "Enter the private key to update this wallet."}, status=400)
        encrypted_private_address = wallet.private_address if wallet is not None else ""
        if private_address:
            try:
                encrypted_private_address = encrypt_wallet_private_address(private_address)
            except RuntimeError:
                logger.error("wallet private-key encryption is not configured")
                return JsonResponse({"error": "wallet_encryption_unavailable", "detail": "Wallet storage is temporarily unavailable."}, status=503)
        if wallet is None:
            wallet = WalletConfiguration.objects.create(
                user=request.user,
                label=label,
                address=address,
                private_address=encrypted_private_address,
                accepts_deposits=True,
                receives_payouts=True,
                is_active=True,
            )
        else:
            wallet.label = label
            wallet.address = address
            wallet.private_address = encrypted_private_address
            wallet.save(update_fields=["label", "address", "private_address"])
        if not WalletConfiguration.objects.filter(user=request.user, is_active=True).exists():
            wallet.is_active = True
            wallet.save(update_fields=["is_active"])
        if wallet.is_active:
            preference, _ = UserPreference.objects.get_or_create(user=request.user)
            preference.creator_wallet = wallet.address
            preference.save(update_fields=["creator_wallet", "updated_at"])
    ensure_creator_wallet(wallet.address)
    return JsonResponse({"id": wallet.id, "created": created})


@login_required
@require_POST
def activate_wallet(request: HttpRequest, wallet_id: int) -> JsonResponse:
    """지정한 지갑을 활성 지갑으로 바꾸고 환경설정의 창작자 지갑 값을 동기화한다."""
    wallet = WalletConfiguration.objects.filter(pk=wallet_id, user=request.user).first()
    if wallet is None:
        return JsonResponse({"error": "wallet_not_found"}, status=404)
    WalletConfiguration.objects.filter(user=request.user, is_active=True).update(is_active=False)
    wallet.is_active = True
    wallet.save(update_fields=["is_active"])
    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    preference.creator_wallet = wallet.address
    preference.save(update_fields=["creator_wallet", "updated_at"])
    ensure_creator_wallet(wallet.address)
    return JsonResponse({"creator_wallet": wallet.address})


@login_required
@require_http_methods(["DELETE"])
def delete_wallet(request: HttpRequest, wallet_id: int) -> JsonResponse:
    """Delete an account-owned wallet and atomically replace its active pointer."""
    with transaction.atomic():
        wallet = WalletConfiguration.objects.select_for_update().filter(pk=wallet_id, user=request.user).first()
        if wallet is None:
            return JsonResponse({"error": "wallet_not_found"}, status=404)
        was_active = wallet.is_active
        wallet.delete()
        replacement = None
        if was_active:
            replacement = WalletConfiguration.objects.filter(user=request.user).order_by("-created_at").first()
            if replacement is not None:
                replacement.is_active = True
                replacement.save(update_fields=["is_active"])
        if was_active:
            preference, _ = UserPreference.objects.get_or_create(user=request.user)
            preference.creator_wallet = replacement.address if replacement else ""
            preference.save(update_fields=["creator_wallet", "updated_at"])
    return JsonResponse({"creator_wallet": replacement.address if replacement else ""})


@login_required
@require_http_methods(["GET"])
def wallet_configuration_list(request: HttpRequest) -> JsonResponse:
    """현재 사용자가 등록한 지갑을 비밀값 없이 반환한다."""
    return JsonResponse({"items": [
        {
            "id": wallet.id,
            "label": wallet.label,
            "address": wallet.address,
            "is_active": wallet.is_active,
            "has_private_address": bool(wallet.private_address),
        }
        for wallet in WalletConfiguration.objects.filter(user=request.user)
    ]})


@login_required
@require_POST
def password(request: HttpRequest) -> JsonResponse:
    """현재 비밀번호를 검증한 뒤 세션을 유지하며 새 비밀번호로 교체한다."""
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid_json", "detail": "Invalid password payload."}, status=400)
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    confirmation = str(payload.get("confirmation") or "")
    if not request.user.check_password(current_password):
        return JsonResponse({"error": "invalid_current_password", "detail": "Current password is incorrect."}, status=400)
    if new_password != confirmation:
        return JsonResponse({"error": "password_mismatch", "detail": "New passwords do not match."}, status=400)
    try:
        validate_password(new_password, request.user)
    except ValidationError as exc:
        return JsonResponse({"error": "invalid_password", "detail": " ".join(exc.messages)}, status=400)
    request.user.set_password(new_password)
    request.user.save(update_fields=["password"])
    update_session_auth_hash(request, request.user)
    logger.info("account password updated user=%s", request.user.get_username())
    return JsonResponse({"ok": True})
