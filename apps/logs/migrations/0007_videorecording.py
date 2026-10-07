from django.db import migrations, models
import apps.logs.storage


class Migration(migrations.Migration):

    dependencies = [
        ('logs', '0006_alter_vehiclelog_managers_vehiclelog_archived_at_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='VideoRecording',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_filename', models.CharField(max_length=255, unique=True)),
                ('video', models.FileField(storage=apps.logs.storage.VideoMediaStorage(), upload_to='recordings/')),
                ('camera_role', models.CharField(
                    choices=[('ENTRY_CAM', 'Entry Camera'), ('EXIT_CAM', 'Exit Camera')],
                    db_index=True,
                    max_length=20,
                )),
                ('started_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('duration_seconds', models.FloatField(blank=True, null=True)),
                ('size_bytes', models.PositiveBigIntegerField(default=0)),
                ('plates', models.JSONField(blank=True, default=list)),
                ('plate_search', models.TextField(blank=True, db_index=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'db_table': 'video_recordings',
                'ordering': ['-uploaded_at'],
            },
        ),
    ]
