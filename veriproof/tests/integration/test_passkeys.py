from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


@pytest.mark.django_db
def test_authenticated_user_can_register_public_passkey(client, monkeypatch):
    from django.contrib.auth.models import User
    from apps.accounts.models import PasskeyCredential

    user = User.objects.create_user("passkey@test.com", password="safe-password-123")
    client.force_login(user)
    options = client.post("/accounts/passkeys/register/options/")
    assert options.status_code == 200
    assert options.json()["rp"]["id"] == "testserver"
    assert options.json()["authenticatorSelection"]["userVerification"] == "required"

    verified = SimpleNamespace(
        credential_id=b"credential-id", credential_public_key=b"public-key",
        sign_count=0, credential_device_type=SimpleNamespace(value="multi_device"),
        credential_backed_up=True,
    )
    monkeypatch.setattr("apps.accounts.views_passkey.verify_registration", lambda *args, **kwargs: verified)
    response = client.post(
        "/accounts/passkeys/register/verify/",
        data={
            "device_name": "Studio laptop",
            "credential": {"id": _encoded(b"credential-id"), "response": {"transports": ["internal"]}},
        },
        content_type="application/json",
    )
    assert response.status_code == 201
    stored = PasskeyCredential.objects.get(user=user)
    assert bytes(stored.credential_id) == b"credential-id"
    assert stored.device_name == "Studio laptop"
    assert stored.transports == ["internal"]


@pytest.mark.django_db
def test_passkey_authentication_creates_django_session(client, monkeypatch):
    from django.contrib.auth.models import User
    from apps.accounts.models import PasskeyCredential

    user = User.objects.create_user("passkey-login@test.com", password="safe-password-123")
    credential = PasskeyCredential.objects.create(
        user=user, user_handle=b"u" * 64, credential_id=b"login-credential",
        public_key=b"public-key", sign_count=3,
    )
    options = client.post(
        "/accounts/passkeys/login/options/", data={"next": "/library/"},
        content_type="application/json",
    )
    assert options.status_code == 200
    assert options.json()["userVerification"] == "required"
    monkeypatch.setattr(
        "apps.accounts.views_passkey.verify_authentication",
        lambda *args, **kwargs: SimpleNamespace(new_sign_count=4),
    )
    response = client.post(
        "/accounts/passkeys/login/verify/",
        data={"credential": {"id": _encoded(b"login-credential"), "response": {"userHandle": _encoded(b"u" * 64)}}},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["redirect"] == "/library/"
    assert client.session["_auth_user_id"] == str(user.pk)
    credential.refresh_from_db()
    assert credential.sign_count == 4
    assert credential.last_used_at is not None


@pytest.mark.django_db
def test_passkey_challenge_is_single_use(client, monkeypatch):
    from django.contrib.auth.models import User

    user = User.objects.create_user("replay@test.com", password="safe-password-123")
    client.force_login(user)
    client.post("/accounts/passkeys/register/options/")
    monkeypatch.setattr(
        "apps.accounts.views_passkey.verify_registration",
        lambda *args, **kwargs: SimpleNamespace(
            credential_id=b"one", credential_public_key=b"key", sign_count=0,
            credential_device_type=SimpleNamespace(value="single_device"), credential_backed_up=False,
        ),
    )
    payload = {"credential": {"id": _encoded(b"one"), "response": {}}}
    assert client.post("/accounts/passkeys/register/verify/", data=payload, content_type="application/json").status_code == 201
    replay = client.post("/accounts/passkeys/register/verify/", data=payload, content_type="application/json")
    assert replay.status_code == 400


@pytest.mark.django_db
def test_passkey_management_lists_and_deletes_only_current_users_credentials(client):
    from django.contrib.auth.models import User
    from apps.accounts.models import PasskeyCredential

    user = User.objects.create_user("manage-passkey@test.com", password="safe-password-123")
    other = User.objects.create_user("other-passkey@test.com", password="safe-password-123")
    own = PasskeyCredential.objects.create(
        user=user,
        user_handle=b"u" * 64,
        credential_id=b"own-management-credential",
        public_key=b"own-public-key",
        transports=["hybrid", "internal"],
        device_name="My laptop",
        device_type="multi_device",
        backed_up=True,
    )
    foreign = PasskeyCredential.objects.create(
        user=other,
        user_handle=b"o" * 64,
        credential_id=b"foreign-management-credential",
        public_key=b"foreign-public-key",
        device_name="Other laptop",
    )
    client.force_login(user)

    response = client.get("/accounts/passkeys/")
    assert response.status_code == 200
    assert response.json()["items"] == [{
        "id": own.pk,
        "device_name": "My laptop",
        "created_at": own.created_at.isoformat(),
        "last_used_at": None,
        "transports": ["hybrid", "internal"],
        "device_type": "multi_device",
        "backed_up": True,
    }]
    assert client.delete(f"/accounts/passkeys/{foreign.pk}/").status_code == 404
    deleted = client.delete(f"/accounts/passkeys/{own.pk}/")
    assert deleted.status_code == 200
    assert not PasskeyCredential.objects.filter(pk=own.pk).exists()
    assert PasskeyCredential.objects.filter(pk=foreign.pk).exists()


@pytest.mark.django_db
def test_last_passkey_cannot_be_deleted_without_password_fallback(client):
    from django.contrib.auth.models import User
    from apps.accounts.models import PasskeyCredential

    user = User.objects.create_user("passkey-only@test.com")
    user.set_unusable_password()
    user.save(update_fields=["password"])
    credential = PasskeyCredential.objects.create(
        user=user,
        user_handle=b"u" * 64,
        credential_id=b"only-credential",
        public_key=b"public-key",
    )
    client.force_login(user)

    response = client.delete(f"/accounts/passkeys/{credential.pk}/")
    assert response.status_code == 409
    assert response.json()["error"] == "last_authenticator"
    assert PasskeyCredential.objects.filter(pk=credential.pk).exists()


@pytest.mark.django_db
def test_account_settings_exposes_passkey_management_container(client):
    from django.contrib.auth.models import User

    user = User.objects.create_user("passkey-ui@test.com", password="safe-password-123")
    client.force_login(user)
    response = client.get("/library")
    assert response.status_code == 200
    assert b'id="passkey-credential-list"' in response.content
    assert b'id="passkey-list-skeleton"' in response.content


def test_login_page_exposes_passkey_action(client):
    response = client.get("/accounts/login/")
    assert response.status_code == 200
    assert b'id="passkey-login-button"' in response.content
    assert b"js/passkeys." in response.content
    assert b".js" in response.content
