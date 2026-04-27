from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('visitors', '0005_alter_blacklistentry_reason_and_remarks_required'),
    ]

    operations = [
        migrations.AddField(
            model_name='visitor',
            name='visitor_type',
            field=models.CharField(
                choices=[('VISITOR', 'Visitor'), ('VERIFIED_VISITOR', 'Verified Visitor')],
                db_index=True,
                default='VISITOR',
                max_length=20,
            ),
        ),
    ]
