from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("settlement", "0002_license_buyer_user")]

    operations = [
        migrations.AddField(
            model_name="license",
            name="payment_currency",
            field=models.CharField(default="USDC", max_length=8),
        ),
        migrations.AlterField(
            model_name="license",
            name="price_usdc",
            field=models.DecimalField(
                blank=True, decimal_places=6, max_digits=12, null=True
            ),
        ),
        migrations.AddField(
            model_name="license",
            name="price_sol",
            field=models.DecimalField(
                blank=True, decimal_places=9, max_digits=16, null=True
            ),
        ),
    ]
