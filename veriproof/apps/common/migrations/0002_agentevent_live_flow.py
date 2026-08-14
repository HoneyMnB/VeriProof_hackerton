import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="agentevent",
            name="account_owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="agent_events",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="agentevent",
            name="correlation_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddIndex(
            model_name="agentevent",
            index=models.Index(
                fields=["account_owner", "created_at"],
                name="common_age_account_672e11_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="agentevent",
            index=models.Index(
                fields=["correlation_id", "created_at"],
                name="common_age_correla_14dbbc_idx",
            ),
        ),
    ]
