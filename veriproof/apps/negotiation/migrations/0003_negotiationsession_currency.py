from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("negotiation", "0002_sol_negotiation_prices")]

    operations = [
        migrations.AddField(
            model_name="negotiationsession",
            name="currency",
            field=models.CharField(choices=[("SOL", "SOL"), ("USDC", "USDC")], default="SOL", max_length=4),
        ),
    ]
