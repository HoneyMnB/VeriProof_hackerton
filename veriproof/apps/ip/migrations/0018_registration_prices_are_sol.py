from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("ip", "0017_ipasset_target_price_sol")]

    operations = [
        migrations.AddField(
            model_name="ipasset",
            name="min_price_sol",
            field=models.DecimalField(
                blank=True,
                decimal_places=9,
                max_digits=16,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AlterField(
            model_name="ipasset",
            name="min_price_usdc",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AlterField(
            model_name="ipasset",
            name="target_price_usdc",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
    ]
