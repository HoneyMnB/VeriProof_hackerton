import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("ip", "0011_assistant_message_conversation")]
    operations = [
        migrations.CreateModel(
            name="AssetComponent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("file_name", models.CharField(max_length=255)),
                ("content_mime_type", models.CharField(max_length=100)),
                ("content_sha256", models.CharField(db_index=True, max_length=64)),
                ("storage_url", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="components", to="ip.ipasset")),
            ],
        )
    ]
