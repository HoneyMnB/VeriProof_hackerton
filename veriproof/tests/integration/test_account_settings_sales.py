"""Creator-shell account settings integration contracts."""
from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile


def _wallet_credentials() -> tuple[str, str]:
    """Produce a matching public address and base58 private key for request tests."""
    from solders.keypair import Keypair

    keypair = Keypair()
    return str(keypair.pubkey()), str(keypair)


@pytest.mark.django_db
def test_settings_modal_contains_sales_overview_and_sidebar_has_no_dashboard(client):
    """판매 현황은 계정 설정에서 제공하고 별도 사이드바 메뉴는 두지 않는다."""
    from django.contrib.auth.models import User

    user = User.objects.create_user(username="creator@example.com", password="test-password-123")
    client.force_login(user)

    response = client.get("/")

    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-settings-tab="sales"' in content
    assert 'id="settings-sales-metrics"' in content
    assert 'id="settings-sales-list"' in content
    assert 'id="settings-sales-filters"' in content
    assert 'id="settings-sales-wallet-select"' in content
    assert 'id="settings-sales-detail"' not in content
    assert 'id="settings-sales-work-pagination"' in content
    assert 'id="account-wallet-cancel"' in content
    assert 'class="vp-modal-close"' in content
    assert 'id="history-search"' in content
    assert 'href="/dashboard"' not in content


@pytest.mark.django_db
def test_removed_dashboard_route_is_not_exposed(client):
    """판매자 대시보드 URL은 독립 화면으로 더 이상 제공하지 않는다."""
    assert client.get("/dashboard").status_code == 404


@pytest.mark.django_db
def test_sandbox_link_is_only_rendered_for_staff_in_debug(client, settings):
    from django.contrib.auth.models import User

    user = User.objects.create_user(username="staff@example.com", password="test-password-123", is_staff=True)
    client.force_login(user)
    settings.DEBUG = True

    response = client.get("/")

    assert response.status_code == 200
    content = response.content.decode()
    assert 'class="vp-settings-nav__developer"' in content
    assert 'href="/sandbox"' in content


@pytest.mark.django_db
def test_wallet_save_prepares_creator_for_registration_drafts(client):
    """공개 지갑 저장은 등록 초안이 참조할 Creator row까지 준비한다."""
    from django.contrib.auth.models import User

    from apps.ip.models import Creator

    wallet, private_address = _wallet_credentials()
    user = User.objects.create_user(username="creator-wallet@example.com", password="test-password-123")
    client.force_login(user)

    wallet_response = client.post(
        "/accounts/wallets/save/",
        json.dumps({
            "address": wallet,
            "private_address": private_address,
            "label": "Primary Solana",
        }),
        content_type="application/json",
    )

    assert wallet_response.status_code == 200
    assert Creator.objects.filter(wallet_address=wallet).exists()

    draft_response = client.post(
        "/api/v1/assistant/registration-drafts",
        {
            "creator_wallet": wallet,
            "file_name": "work.png",
            "fields": json.dumps({
                "asset_type": "image",
                "title": "Work",
                "min_price": "1",
                "target_price": "2",
                "visibility": "private",
            }),
            "files": SimpleUploadedFile("work.png", b"work", content_type="image/png"),
        },
    )

    assert draft_response.status_code == 201
    assert draft_response.json()["status"] == "collecting"


@pytest.mark.django_db
def test_wallet_save_returns_503_when_production_encryption_key_is_invalid(client, settings):
    """A malformed deployment secret must not surface as an unhandled server error."""
    from django.contrib.auth.models import User

    wallet, private_address = _wallet_credentials()
    user = User.objects.create_user(username="invalid-wallet-key@example.com", password="test-password-123")
    client.force_login(user)
    settings.DEBUG = False
    settings.WALLET_PRIVATE_KEY_ENCRYPTION_KEY = "not-a-fernet-key"

    response = client.post(
        "/accounts/wallets/save/",
        json.dumps({"address": wallet, "private_address": private_address, "label": "Primary Solana"}),
        content_type="application/json",
    )

    assert response.status_code == 503
    assert response.json()["error"] == "wallet_encryption_unavailable"


@pytest.mark.django_db
def test_registration_draft_uses_authenticated_active_wallet_not_request_wallet(client):
    from django.contrib.auth.models import User

    from apps.accounts.models import WalletConfiguration
    from apps.ip.models import RegistrationDraft

    active_address, private_address = _wallet_credentials()
    user = User.objects.create_user(username="draft-wallet-owner@example.com", password="test-password-123")
    client.force_login(user)
    assert client.post(
        "/accounts/wallets/save/",
        json.dumps({"address": active_address, "private_address": private_address, "label": "Active"}),
        content_type="application/json",
    ).status_code == 200

    response = client.post(
        "/api/v1/assistant/registration-drafts",
        {
            "creator_wallet": "11111111111111111111111111111111",
            "file_name": "work.png",
            "fields": json.dumps({"asset_type": "image", "title": "Work", "min_price": "1", "target_price": "2", "visibility": "private"}),
            "files": SimpleUploadedFile("work.png", b"work", content_type="image/png"),
        },
    )

    assert response.status_code == 201
    assert WalletConfiguration.objects.get(user=user).address == active_address
    assert RegistrationDraft.objects.get(id=response.json()["draft_id"]).creator.wallet_address == active_address


@pytest.mark.django_db
def test_wallet_secret_is_encrypted_and_never_returned_or_logged_in_wallet_list(client):
    from django.contrib.auth.models import User

    from apps.accounts.models import WalletConfiguration

    wallet, private_address = _wallet_credentials()
    user = User.objects.create_user(username="encrypted-wallet@example.com", password="test-password-123")
    client.force_login(user)

    response = client.post(
        "/accounts/wallets/save/",
        json.dumps({"address": wallet, "private_address": private_address, "label": "Encrypted wallet"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    stored = WalletConfiguration.objects.get(user=user, address=wallet)
    assert stored.private_address != private_address
    assert stored.private_address
    list_response = client.get("/accounts/wallets/")
    assert list_response.status_code == 200
    assert "private_address" not in list_response.json()["items"][0]
    assert list_response.json()["items"][0]["has_private_address"] is True
    assert private_address not in list_response.content.decode()


@pytest.mark.django_db
def test_updating_a_single_wallet_keeps_its_secret_when_private_key_is_omitted(client):
    from django.contrib.auth.models import User

    from apps.accounts.models import UserPreference, WalletConfiguration

    first_address, first_private_address = _wallet_credentials()
    user = User.objects.create_user(username="wallet-edit@example.com", password="test-password-123")
    client.force_login(user)
    assert client.post(
        "/accounts/wallets/save/",
        json.dumps({"address": first_address, "private_address": first_private_address, "label": "Original"}),
        content_type="application/json",
    ).status_code == 200
    wallet = WalletConfiguration.objects.get(user=user)
    original_secret = wallet.private_address

    update_response = client.post(
        "/accounts/wallets/save/",
        json.dumps({"address": first_address, "private_address": "", "label": "Renamed"}),
        content_type="application/json",
    )

    assert update_response.status_code == 200
    wallet.refresh_from_db()
    assert WalletConfiguration.objects.filter(user=user).count() == 1
    assert wallet.label == "Renamed"
    assert wallet.private_address == original_secret
    assert UserPreference.objects.get(user=user).creator_wallet == first_address


@pytest.mark.django_db
def test_deleting_the_only_wallet_clears_active_preference_and_preserves_creator(client):
    from django.contrib.auth.models import User

    from apps.accounts.models import UserPreference, WalletConfiguration
    from apps.ip.models import Creator

    address, private_address = _wallet_credentials()
    user = User.objects.create_user(username="wallet-delete@example.com", password="test-password-123")
    client.force_login(user)
    assert client.post(
        "/accounts/wallets/save/",
        json.dumps({"address": address, "private_address": private_address, "label": "Primary"}),
        content_type="application/json",
    ).status_code == 200
    wallet = WalletConfiguration.objects.get(user=user)

    response = client.delete(f"/accounts/wallets/{wallet.id}/")

    assert response.status_code == 200
    assert response.json()["creator_wallet"] == ""
    assert not WalletConfiguration.objects.filter(user=user).exists()
    assert UserPreference.objects.get(user=user).creator_wallet == ""
    assert Creator.objects.filter(wallet_address=address).exists()
