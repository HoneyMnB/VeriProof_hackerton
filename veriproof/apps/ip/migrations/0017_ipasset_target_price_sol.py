from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ip", "0016_asset_image")]

    operations = [
        migrations.AddField(
            model_name="ipasset",
            name="target_price_sol",
            field=models.DecimalField(
                blank=True,
                decimal_places=9,
                max_digits=16,
                null=True,
                validators=[MinValueValidator(0)],
            ),
        ),
    ]
