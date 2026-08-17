"""Delete all IP assets or only the explicitly listed asset IDs.

Set exactly one deletion mode below, then run this file from the Django
container or project environment. It has no command-line arguments.
"""

import os
import sys
from pathlib import Path


DELETE_ALL = True
ASSET_IDS: tuple[str, ...] = ()
DATABASE_HOST = "localhost"

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def delete_assets() -> int:
    if not DELETE_ALL and not ASSET_IDS:
        return 0

    sys.path.insert(0, str(PROJECT_ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ["POSTGRES_HOST"] = DATABASE_HOST

    import django

    django.setup()

    from django.db import connection, transaction

    with transaction.atomic(), connection.cursor() as cursor:
        if DELETE_ALL:
            cursor.execute("SELECT id FROM ip_ipasset")
        else:
            cursor.execute("SELECT id FROM ip_ipasset WHERE id = ANY(%s)", [list(ASSET_IDS)])
        asset_ids = [str(row[0]) for row in cursor.fetchall()]
        if not asset_ids:
            return 0

        cursor.execute(
            "DELETE FROM common_agentevent WHERE asset_id = ANY(%s) "
            "OR session_id IN (SELECT id FROM negotiation_negotiationsession WHERE asset_id = ANY(%s))",
            [asset_ids, asset_ids],
        )
        cursor.execute(
            "DELETE FROM settlement_royaltydistribution WHERE license_id IN "
            "(SELECT id FROM settlement_license WHERE asset_id = ANY(%s))",
            [asset_ids],
        )
        cursor.execute(
            "DELETE FROM settlement_batchitem WHERE asset_id = ANY(%s) OR license_id IN "
            "(SELECT id FROM settlement_license WHERE asset_id = ANY(%s))",
            [asset_ids, asset_ids],
        )
        cursor.execute("DELETE FROM settlement_license WHERE asset_id = ANY(%s)", [asset_ids])
        cursor.execute("DELETE FROM ip_sponsoredpaymentintent WHERE asset_id = ANY(%s)", [asset_ids])
        cursor.execute("DELETE FROM negotiation_negotiationsession WHERE asset_id = ANY(%s)", [asset_ids])
        cursor.execute("DELETE FROM ip_registrationcharge WHERE asset_id = ANY(%s)", [asset_ids])
        cursor.execute("UPDATE ip_registrationdraft SET executed_asset_id = NULL WHERE executed_asset_id = ANY(%s)", [asset_ids])
        cursor.execute("DELETE FROM ip_assetcomponent WHERE asset_id = ANY(%s)", [asset_ids])
        cursor.execute("DELETE FROM ip_assetimage WHERE asset_id = ANY(%s)", [asset_ids])
        cursor.execute(
            "UPDATE ip_ipasset SET parent_asset_id = NULL, royalty_share_bps = NULL "
            "WHERE parent_asset_id = ANY(%s)",
            [asset_ids],
        )
        cursor.execute("DELETE FROM ip_ipasset WHERE id = ANY(%s)", [asset_ids])

    return len(asset_ids)


if __name__ == "__main__":
    print(f"Deleted {delete_assets()} ip_ipasset rows.")
