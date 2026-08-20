"""공통 셸이 필요한 계정 표시 데이터를 제공한다."""
from __future__ import annotations

from .models import UserPreference, WalletConfiguration


def account_preferences(request):
    """인증된 요청에만 설정을 주입하고 공개 화면에는 빈 값을 준다."""
    if not request.user.is_authenticated:
        return {"account_preferences": None, "account_wallets": []}
    preference = UserPreference.objects.filter(user=request.user).first()
    wallets = WalletConfiguration.objects.filter(user=request.user)
    return {"account_preferences": preference, "account_wallets": wallets}


def vp_language(request):
    """현재 UI 언어를 SSR 주입용(``vp_active_lang``)으로 해석한다.

    클라이언트 i18n 엔진(vp-i18n.js)이 <html lang> 과 초기 locale 을 읽을 수
    있도록 서버 사이드에서 미리 결정한다. 우선순위:
      1. ``veriproof_lang`` 쿠키 — 공개 화면의 언어 전환기가 기록한 현재 선택값.
      2. 인증 사용자의 DB 저장 언어(``UserPreference.language``).
      3. 어느 쪽도 없으면 ``en``.

    쿠키를 먼저 확인해야 공개 Discover에서 선택한 언어가 상세 화면의 새
    요청에서도 유지된다. 쿠키가 없는 다른 브라우저/새 기기에서는 저장된 계정
    언어가 계속 기본값으로 사용된다.
    """
    cookie_lang = request.COOKIES.get("veriproof_lang")
    if cookie_lang in {choice for choice, _ in UserPreference.LANGUAGE_CHOICES}:
        return {"vp_active_lang": cookie_lang}
    if request.user.is_authenticated:
        preference = UserPreference.objects.filter(user=request.user).first()
        if preference and preference.language:
            return {"vp_active_lang": preference.language}
    return {"vp_active_lang": "en"}
