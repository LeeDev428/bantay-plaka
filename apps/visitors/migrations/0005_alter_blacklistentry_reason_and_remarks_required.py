from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('visitors', '0004_visitor_vehicle_type_other'),
    ]

    operations = [
        migrations.AlterField(
            model_name='blacklistentry',
            name='reason',
            field=models.CharField(max_length=255),
        ),
        migrations.AlterField(
            model_name='blacklistentry',
            name='remarks',
            field=models.TextField(),
        ),
    ]
