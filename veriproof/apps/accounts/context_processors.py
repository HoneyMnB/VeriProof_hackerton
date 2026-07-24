"""공통 셸이 필요한 계정 표시 데이터를 제공한다."""
from __future__ import annotations

from .models import UserPreference


def account_preferences(request):
    """인증된 요청에만 설정을 주입하고 공개 화면에는 빈 값을 준다."""
    if not request.user.is_authenticated:
        return {"account_preferences": None}
    preference = UserPreference.objects.filter(user=request.user).first()
    return {"account_preferences": preference}


def vp_language(request):
    """현재 UI 언어를 SSR 주입용(``vp_active_lang``)으로 해석한다.

    클라이언트 i18n 엔진(vp-i18n.js)이 <html lang> 과 초기 locale 을 읽을 수
    있도록 서버 사이드에서 미리 결정한다. 우선순위:
      1. 인증 사용자의 DB 저장 언어(``UserPreference.language``) — 권위 값.
      2. 미인증(공개 화면)의 ``veriproof_lang`` 쿠키 — 엔진이 setLocale 시 기록.
      3. 어느 쪽도 없으면 ``en``.
    """
    if request.user.is_authenticated:
        preference = UserPreference.objects.filter(user=request.user).first()
        if preference and preference.language:
            return {"vp_active_lang": preference.language}
    cookie_lang = request.COOKIES.get("veriproof_lang")
    if cookie_lang in {choice for choice, _ in UserPreference.LANGUAGE_CHOICES}:
        return {"vp_active_lang": cookie_lang}
    return {"vp_active_lang": "en"}
