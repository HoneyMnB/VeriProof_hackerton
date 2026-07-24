"""Creator-shell account settings integration contracts."""
from __future__ import annotations

import pytest


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
