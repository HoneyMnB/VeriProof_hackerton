"""ASGI config for the VeriProof AI project."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_application = get_asgi_application()

from agent_a.application import build_application  # noqa: E402

application = build_application(django_application)
