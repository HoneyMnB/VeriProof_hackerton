from django.urls import path

from . import views

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
    path("password/", views.password, name="password"),
]
