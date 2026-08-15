"""Backfill IpAsset amount columns after applying migration ip.0020.

This utility intentionally preserves the legacy SOL numeric values verbatim and
labels every row as USDC. It does not perform any currency conversion.
"""

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def configure_django() -> None:
    """Make the Django project importable when this file is run directly."""
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def backfill() -> int:
    configure_django()

    import django

    django.setup()

    from django.db.models import F

    from apps.ip.models import IpAsset

    target_count = IpAsset.objects.filter(
        target_amount__isnull=True,
        target_price_sol__isnull=False,
    ).update(target_amount=F("target_price_sol"))
    min_count = IpAsset.objects.filter(
        min_amount__isnull=True,
        min_price_sol__isnull=False,
    ).update(min_amount=F("min_price_sol"))
    currency_count = IpAsset.objects.exclude(currency="USDC").update(currency="USDC")
    return target_count + min_count + currency_count


if __name__ == "__main__":
    print(f"Updated {backfill()} ip_ipasset rows.")
