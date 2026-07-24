"""Unit tests for the ``vp_language`` SSR context processor (apps.accounts).

The processor resolves the active UI language injected as ``vp_active_lang``
so ``base.html`` can render a correct ``<html lang>`` and seed the client
engine (``window.__VP_LANG__``). Resolution priority:

    authenticated DB preference  >  anonymous cookie  >  default "en"

These tests pin that priority so the SSR layer never disagrees with the
client ``VP.i18n`` engine.
"""
from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory

from apps.accounts.context_processors import vp_language
from apps.accounts.models import UserPreference


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


def _request(rf: RequestFactory, user, cookies: dict[str, str] | None = None):
    request = rf.get("/")
    request.user = user
    request.COOKIES = cookies or {}
    return request


@pytest.mark.django_db
class TestVpLanguageResolution:
    def test_anonymous_default_is_english(self, rf):
        request = _request(rf, AnonymousUser())
        assert vp_language(request) == {"vp_active_lang": "en"}

    def test_anonymous_cookie_is_honoured(self, rf):
        request = _request(rf, AnonymousUser(), {"veriproof_lang": "ko"})
        assert vp_language(request)["vp_active_lang"] == "ko"

    def test_anonymous_invalid_cookie_falls_back(self, rf):
        request = _request(rf, AnonymousUser(), {"veriproof_lang": "fr"})
        assert vp_language(request)["vp_active_lang"] == "en"

    def test_authenticated_db_preference_wins_over_cookie(self, rf):
        user = User.objects.create_user(username="creator1")
        # A signal auto-creates the UserPreference on user creation, so update it
        # rather than insert (avoids a UNIQUE violation on user_id).
        UserPreference.objects.filter(user=user).update(language="ko")
        # Cookie says "en" but the saved DB preference ("ko") is authoritative.
        request = _request(rf, user, {"veriproof_lang": "en"})
        assert vp_language(request)["vp_active_lang"] == "ko"

    def test_authenticated_new_user_uses_model_default(self, rf):
        user = User.objects.create_user(username="creator2")
        request = _request(rf, user)
        # The model default is Korean; a freshly registered user has no explicit
        # choice yet, so SSR resolves to that default (not English).
        assert vp_language(request)["vp_active_lang"] == "ko"
