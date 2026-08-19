from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ip", "0022_sponsoredpaymentintent_buyer_user_nullable"), ("negotiation", "0003_negotiationsession_currency")]
    operations = [migrations.AddField(model_name="sponsoredpaymentintent", name="negotiation_session", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="sponsored_payment_intents", to="negotiation.negotiationsession"))]
