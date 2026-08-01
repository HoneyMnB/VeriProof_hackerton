from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("negotiation", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="negotiationsession",
            name="initial_offer_usdc",
            field=models.DecimalField(
                blank=True, decimal_places=6, max_digits=12, null=True
            ),
        ),
        migrations.AddField(
            model_name="negotiationsession",
            name="initial_offer_sol",
            field=models.DecimalField(
                blank=True, decimal_places=9, max_digits=16, null=True
            ),
        ),
        migrations.AddField(
            model_name="negotiationsession",
            name="final_price_sol",
            field=models.DecimalField(
                blank=True, decimal_places=9, max_digits=16, null=True
            ),
        ),
    ]
