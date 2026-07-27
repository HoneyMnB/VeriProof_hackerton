"""이메일 로그인·가입과 DEBUG 개발자 로그인 계약."""
from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_signup_uses_normalized_email_as_username(client):
    """이메일 ID는 대소문자를 정규화해 Django username과 동일하게 저장한다."""
    response = client.post(
        "/accounts/signup/",
        {"email": "Creator@Test.com", "password1": "safe-password-123", "password2": "safe-password-123"},
    )
    assert response.status_code == 302
    from django.contrib.auth.models import User

    user = User.objects.get(username="creator@test.com")
    assert user.email == "creator@test.com"


@pytest.mark.django_db
def test_developer_login_only_exists_in_debug(client, settings):
    """고정 개발자 자격증명은 로컬 DEBUG 환경에서만 세션 로그인을 허용한다."""
    settings.DEBUG = True
    response = client.post("/accounts/developer-login/")
    assert response.status_code == 302
    assert "_auth_user_id" in client.session

    client.logout()
    settings.DEBUG = False
    response = client.post("/accounts/developer-login/")
    assert response.status_code == 302
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_account_preferences_persist_language_and_workspace_controls(client):
    """설정 모달의 언어·지갑 값은 저장되고 입력 컨트롤은 렌더링에 남는다."""
    from django.contrib.auth.models import User

    from apps.ip.models import Creator

    wallet = "11111111111111111111111111111111"
    user = User.objects.create_user("seller@test.com", "seller@test.com", "safe-password-123")
    client.force_login(user)

    response = client.post(
        "/accounts/preferences/",
        data=f'{{"display_name":"Seller","language":"en","recovery_email":"recovery@example.com","contact_phone":"+82 10 1234 5678","creator_wallet":"{wallet}"}}',
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["language"] == "en"
    assert response.json()["recovery_email"] == "recovery@example.com"
    assert Creator.objects.filter(wallet_address=wallet).exists()

    workspace = client.get("/")
    content = workspace.content.decode()
    assert 'id="account-language"' in content
    assert 'id="account-creator-wallet"' in content
    assert 'id="account-password-form"' in content
    assert 'id="settings-wallet-monitor"' in content


@pytest.mark.django_db
def test_password_change_requires_current_password_and_keeps_session(client):
    from django.contrib.auth.models import User

    user = User.objects.create_user("password@test.com", "password@test.com", "safe-password-123")
    client.force_login(user)
    rejected = client.post(
        "/accounts/password/",
        data='{"current_password":"wrong","new_password":"new-safe-password-456","confirmation":"new-safe-password-456"}',
        content_type="application/json",
    )
    assert rejected.status_code == 400
    changed = client.post(
        "/accounts/password/",
        data='{"current_password":"safe-password-123","new_password":"new-safe-password-456","confirmation":"new-safe-password-456"}',
        content_type="application/json",
    )
    assert changed.status_code == 200
    user.refresh_from_db()
    assert user.check_password("new-safe-password-456")
    assert "_auth_user_id" in client.session
