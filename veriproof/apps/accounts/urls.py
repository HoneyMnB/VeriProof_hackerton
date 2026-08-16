from django.urls import path

from . import views, views_passkey

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("developer-login/", views.developer_login, name="developer-login"),
    path("logout/", views.logout_view, name="logout"),
    path("preferences/", views.preferences, name="preferences"),
    path("wallets/", views.wallet_configuration_list, name="wallet-list"),
    path("wallets/save/", views.wallet_configurations, name="wallet-save"),
    path("wallets/<int:wallet_id>/activate/", views.activate_wallet, name="wallet-activate"),
    path("wallets/<int:wallet_id>/", views.delete_wallet, name="wallet-delete"),
    path("password/", views.password, name="password"),
    path("passkeys/", views_passkey.credential_list, name="passkey-list"),
    path("passkeys/<int:credential_id>/", views_passkey.credential_delete, name="passkey-delete"),
    path("passkeys/register/options/", views_passkey.registration_begin, name="passkey-register-options"),
    path("passkeys/register/verify/", views_passkey.registration_complete, name="passkey-register-verify"),
    path("passkeys/login/options/", views_passkey.authentication_begin, name="passkey-login-options"),
    path("passkeys/login/verify/", views_passkey.authentication_complete, name="passkey-login-verify"),
]
