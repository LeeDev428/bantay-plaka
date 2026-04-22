from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logs', '0004_convert_unknown_entry_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='CameraFeedSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('camera_role', models.CharField(choices=[('ENTRY_CAM', 'Entry Camera'), ('EXIT_CAM', 'Exit Camera')], db_index=True, max_length=20, unique=True)),
                ('snapshot', models.ImageField(blank=True, null=True, upload_to='snapshots/live/')),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
            ],
            options={
                'db_table': 'camera_feed_snapshots',
                'ordering': ['-updated_at'],
            },
        ),
    ]
