"""Django settings for the VeriProof AI project.

Single source of truth: ``docs/00-architecture-and-data-model.md`` (SSOT).

Design notes:
- PostgreSQL 16 is the production system of record, but the **default** local
  database is SQLite so the full pytest suite runs offline with zero infra.
  Setting ``DATABASE_URL`` switches to PostgreSQL (or any URL-supported DB).
- Every external dependency (Gemini, Solana, GCP) is import-guarded and
  degrades to a no-op when its feature flag is disabled. The local-fallback
  flags below are the DEFAULT so the app boots with no real external keys.
- ``models.JSONField`` is used everywhere (maps to JSONB on PG, TEXT on
  SQLite) so no raw JSONB-only features leak into the offline test path.
"""
from __future__ import annotations

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

# --- Paths -------------------------------------------------------------------

# Project package root: veriproof/  (parent of this config/ package).
BASE_DIR = Path(__file__).resolve().parent.parent

# 로컬 실행은 저장소 루트의 .env를 읽는다. 배포 환경의 이미 설정된 환경변수는
# 덮어쓰지 않으며, .env 파일의 값이나 경로는 로그로 남기지 않는다.
load_dotenv(BASE_DIR.parent / ".env")

# --- Security ----------------------------------------------------------------

# SECURITY WARNING: keep the secret key used in production secret.
# Local default is only safe for dev/TDD; override via env in production.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-veriproof-local-dev-only-do-not-use-in-prod",
)

# TDD/local defaults to DEBUG=True. Production must set DEBUG=False.
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"

# Wallet private keys are encrypted before persistence. This must remain stable
# while any stored wallet ciphertext exists.
WALLET_PRIVATE_KEY_ENCRYPTION_KEY = "MDEyMzQ1Njc4OUFCQ0RFRjAxMjM0NTY3ODlBQkNERUY="

# 로컬 기본값에는 Docker 내부 Seller 별칭을 포함하고 운영은 환경 변수로 제한한다.
_ALLOWED_HOSTS_RAW = os.environ.get("VERIPROOF_ALLOWED_HOSTS", "")
ALLOWED_HOSTS = ["*"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# [h.strip() for h in _ALLOWED_HOSTS_RAW.split(",") if h.strip()] or [
#     "localhost",
#     "127.0.0.1",
#     "0.0.0.0",
#     "[::1]",
#     "web",
# ]

# --- Applications ------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",  # enables the admin/ placeholder route (§5 deliverable)
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # VeriProof apps (label in parentheses is the app_label used in FK refs)
    "apps.common",        # label: common   -> AgentEvent, shared base
    "apps.accounts",      # label: accounts  -> UserPreference
    "apps.ip",            # label: ip        -> Creator, IpAsset
    "apps.negotiation",   # label: negotiation -> NegotiationSession
    "apps.settlement",    # label: settlement -> License, RoyaltyDistribution,
    #                                    BatchOrder, BatchItem
    "apps.sandbox",       # label: sandbox   -> (no models yet)
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.agent_discovery.AgentDiscoveryMiddleware",
    # NOTE: X402InterceptorMiddleware (SPEC-002) is wired here once implemented.
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.account_preferences",
                "apps.accounts.context_processors.vp_language",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ----------------------------------------------------------------

# DEFAULT: SQLite (zero-infra local TDD). DATABASE_URL -> PostgreSQL in prod.
_DEFAULT_SQLITE = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

_DATABASE_URL = os.environ.get("POSTGRES_DB")
if _DATABASE_URL:
    # PostgreSQL 설정이 있는 경우 설정
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB"),
            "USER": os.environ.get("POSTGRES_USER"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD"),
            "HOST": os.environ.get("POSTGRES_HOST"),
            "PORT": os.environ.get("POSTGRES_PORT"),
        }
    }
else:
    DATABASES = _DEFAULT_SQLITE

# The default_auto_field of BigAutoField matches Creator/AgentEvent id fields.
# Models that use a UUID PK declare it explicitly on the field.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Auth / Internationalisation ---------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
# Supported UI languages for the client-side i18n layer (vp-i18n.js). Codes
# mirror UserPreference.LANGUAGE_CHOICES. No LocaleMiddleware / i18n_patterns —
# rendering is client-side; this only informs Django utils if ever queried.
LANGUAGES = [
    ("en", "English"),
    ("ko", "한국어"),
]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"

# --- Static / Media ----------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# MEDIA_ROOT holds the local backend image store (thumbnails, watermarks,
# temporary originals) when STORAGE_BACKEND=local.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- VeriProof feature flags (local-fallback DEFAULTS per architecture 1.4) ---

# All flags default to the OFF / local / mock state so the app boots with no
# real external keys. Cloud deployments flip these on.
FIRESTORE_ENABLED = os.environ.get("FIRESTORE_ENABLED", "false").lower() == "true"
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")  # 'gcs' | 'local'
AP2_ENABLED = os.environ.get("AP2_ENABLED", "false").lower() == "true"

# --- A2A / ADK --------------------------------------------------------------
# 공개 URL은 에이전트 A의 탐색 카드에 사용한다. Cloud Run에 배포할 때는
# 웹 서비스에 할당된 HTTPS URL로 설정해야 한다.
A2A_PUBLIC_BASE_URL = os.environ.get(
    "A2A_PUBLIC_BASE_URL", "http://localhost:8000"
).rstrip("/")
ADK_MODEL = os.environ.get("ADK_MODEL", "gemini-2.5-flash")

# --- AI (Gemini / Vertex) ----------------------------------------------------
# Import-guarded: google-genai is never imported at module import time.
GEMINI_API_KEYS = os.environ.get("GEMINI_API_KEYS", "")
GEMINI_VISION_MODEL = os.environ.get("GEMINI_VISION_MODEL", ADK_MODEL)
GEMINI_REASONING_MODEL = os.environ.get("GEMINI_REASONING_MODEL", ADK_MODEL)
GEMINI_BATCH_MODEL = os.environ.get("GEMINI_BATCH_MODEL", ADK_MODEL)
# 창작자 권리 비서의 대화 모델은 이미지 분석·협상 모델과 독립적으로 관리한다.
GEMINI_ASSISTANT_MODEL = os.environ.get(
    "GEMINI_ASSISTANT_MODEL", ADK_MODEL
)
VERTEX_ENABLED = os.environ.get("VERTEX_ENABLED", "false").lower() == "true"
VERTEX_PROJECT = os.environ.get("VERTEX_PROJECT", "")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "")
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

# --- Solana / payments -------------------------------------------------------
SOLANA_RPC_URL = os.environ.get(
    "SOLANA_RPC_URL", "https://api.devnet.solana.com"
)
USDC_MINT_ADDRESS = os.environ.get(
    "USDC_MINT_ADDRESS", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
)
DEMO_CREATOR_WALLET = os.environ.get("DEMO_CREATOR_WALLET", "")
X402_FACILITATOR_URL = os.environ.get(
    "X402_FACILITATOR_URL", "https://x402.org/facilitator"
)
X402_NETWORK = os.environ.get(
    "X402_NETWORK", "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
)
# Empty by default in local; cloud deployment sets the escrow pubkey.
PLATFORM_ESCROW_PUBKEY = os.environ.get("PLATFORM_ESCROW_PUBKEY", "")
PLATFORM_ESCROW_SECRET_KEY = os.environ.get("PLATFORM_ESCROW_SECRET_KEY", "")
# Runtime Solana paths are real-only. Tests use ``tests.fakes`` through
# constructor/factory injection; application settings do not select mock
# adapters or accept fabricated transaction identifiers.
# 해커톤 로컬 정책: 플랫폼 수수료는 0bps. 실제 수수료 분배 구현 전에는 0만 허용한다.
PLATFORM_FEE_BPS = int(os.environ.get("PLATFORM_FEE_BPS", "0"))
KMS_KEY_NAME = os.environ.get("KMS_KEY_NAME", "")
PAYSH_WEBHOOK_SECRET = os.environ.get("PAYSH_WEBHOOK_SECRET", "")

# --- GCP pipeline / data (all degrade to no-op when disabled) ----------------
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
PUBSUB_PAYMENTS_TOPIC = os.environ.get("PUBSUB_PAYMENTS_TOPIC", "veriproof-payments")
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "(default)")
BIGQUERY_DATASET = os.environ.get("BIGQUERY_DATASET", "")  # empty => BigQuerySink no-op
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")

# --- App-level tunable constants (env override per architecture 1.4) ---------
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", "10485760"))  # 10 MB
MAX_WORK_IMAGES = int(os.environ.get("MAX_WORK_IMAGES", "10"))
MAX_NEGOTIATION_ROUNDS = int(os.environ.get("MAX_NEGOTIATION_ROUNDS", "5"))
MICRO_FLOOR_USDC = os.environ.get("MICRO_FLOOR_USDC", "0.05")  # Decimal string
BATCH_MAX_ITEMS = int(os.environ.get("BATCH_MAX_ITEMS", "200"))
ORIGINAL_RETENTION_DAYS = int(os.environ.get("ORIGINAL_RETENTION_DAYS", "7"))
DOWNLOAD_TOKEN_TTL_SECONDS = int(os.environ.get("DOWNLOAD_TOKEN_TTL_SECONDS", "604800"))

# --- Logging -----------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "veriproof": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# --- Default primary key field for auto-PK models ----------------------------
# (BigAutoField) is already declared above via DEFAULT_AUTO_FIELD.
