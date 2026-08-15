from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ip", "0020_ipasset_amount_currency")]

    operations = [
        migrations.AddField(
            model_name="sponsoredpaymentintent",
            name="channel",
            field=models.CharField(
                choices=[("browser", "browser"), ("agent", "agent")],
                default="browser",
                max_length=12,
            ),
        ),
    ]
