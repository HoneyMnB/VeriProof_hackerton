from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("ip", "0018_registration_prices_are_sol"),
    ]

    operations = [
        migrations.CreateModel(
            name="SponsoredPaymentIntent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("buyer_wallet", models.CharField(max_length=64)),
                ("recipient_wallet", models.CharField(max_length=64)),
                ("amount_usdc", models.DecimalField(decimal_places=6, max_digits=12)),
                ("memo", models.CharField(max_length=120, unique=True)),
                ("status", models.CharField(choices=[("created", "created"), ("submitted", "submitted"), ("settled", "settled"), ("expired", "expired")], db_index=True, default="created", max_length=12)),
                ("transaction_signature", models.CharField(blank=True, max_length=140, null=True, unique=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("settled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sponsored_payment_intents", to="ip.ipasset")),
                ("buyer_user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sponsored_payment_intents", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="sponsoredpaymentintent",
            index=models.Index(fields=["buyer_user", "asset", "status"], name="ip_sponsor_buyer_asset_idx"),
        ),
    ]
