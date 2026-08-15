from django.core.validators import MinValueValidator
from django.db import migrations, models
from django.db.models import F


def copy_sol_prices_to_amounts(apps, schema_editor):
    """Copy legacy numeric values exactly; no SOL-to-USDC conversion occurs."""
    IpAsset = apps.get_model("ip", "IpAsset")
    IpAsset.objects.update(
        target_amount=F("target_price_sol"),
        min_amount=F("min_price_sol"),
        currency="USDC",
    )


class Migration(migrations.Migration):
    dependencies = [("ip", "0019_sponsoredpaymentintent")]

    operations = [
        migrations.AddField(
            model_name="ipasset",
            name="target_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=9,
                max_digits=16,
                null=True,
                validators=[MinValueValidator(0)],
            ),
        ),
        migrations.AddField(
            model_name="ipasset",
            name="min_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=9,
                max_digits=16,
                null=True,
                validators=[MinValueValidator(0)],
            ),
        ),
        migrations.AddField(
            model_name="ipasset",
            name="currency",
            field=models.CharField(default="USDC", max_length=8),
        ),
        migrations.RunPython(copy_sol_prices_to_amounts, migrations.RunPython.noop),
    ]
