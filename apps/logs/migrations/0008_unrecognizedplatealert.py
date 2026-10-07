from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('logs', '0007_videorecording'),
    ]

    operations = [
        migrations.CreateModel(
            name='UnrecognizedPlateAlert',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('snapshot', models.ImageField(blank=True, null=True, upload_to='alerts/')),
                ('camera_role', models.CharField(
                    choices=[('ENTRY_CAM', 'Entry Camera'), ('EXIT_CAM', 'Exit Camera')],
                    db_index=True,
                    max_length=20,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('reason', models.CharField(
                    default='Vehicle detected but plate could not be recognized',
                    max_length=255,
                )),
                ('recording', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='unrecognized_alert',
                    to='logs.videorecording',
                )),
            ],
            options={
                'db_table': 'unrecognized_plate_alerts',
                'ordering': ['-created_at'],
            },
        ),
    ]
