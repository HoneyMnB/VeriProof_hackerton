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


def test_login_page_exposes_passkey_action(client):
    response = client.get("/accounts/login/")
    assert response.status_code == 200
    assert b'id="passkey-login-button"' in response.content
    assert b"js/passkeys." in response.content
    assert b".js" in response.content
