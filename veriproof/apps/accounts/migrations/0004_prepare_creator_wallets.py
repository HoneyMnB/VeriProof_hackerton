from django.db import migrations


BASE58_ALPHABET = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def _valid_solana_address(address):
    return 32 <= len(address) <= 44 and all(char in BASE58_ALPHABET for char in address)


def prepare_creator_wallets(apps, schema_editor):
    Creator = apps.get_model("ip", "Creator")
    UserPreference = apps.get_model("accounts", "UserPreference")
    WalletConfiguration = apps.get_model("accounts", "WalletConfiguration")

    addresses = set(
        WalletConfiguration.objects.exclude(address="")
        .values_list("address", flat=True)
    )
    addresses.update(
        UserPreference.objects.exclude(creator_wallet="")
        .values_list("creator_wallet", flat=True)
    )
    for address in sorted(address.strip() for address in addresses if address):
        if _valid_solana_address(address):
            Creator.objects.get_or_create(wallet_address=address)


class Migration(migrations.Migration):

    dependencies = [
        ("ip", "0016_asset_image"),
        ("accounts", "0003_walletconfiguration"),
    ]

    operations = [
        migrations.RunPython(prepare_creator_wallets, migrations.RunPython.noop),
    ]
