from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_alter_user_role'),
        ('residents', '0005_vehicle_approval_notes_vehicle_approved_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehicle',
            name='is_archived',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='vehicle',
            name='archived_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='vehicle',
            name='archived_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='archived_vehicles', to='accounts.user'),
        ),
        migrations.CreateModel(
            name='VehicleDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.ImageField(upload_to='vehicle_documents/orcr/')),
                ('registration_year', models.PositiveSmallIntegerField(db_index=True)),
                ('expires_on', models.DateField(blank=True, null=True)),
                ('status', models.CharField(choices=[('OK', 'OK'), ('NEEDS_UPDATE', 'Needs Update')], db_index=True, default='OK', max_length=20)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='uploaded_vehicle_documents', to='accounts.user')),
                ('vehicle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='documents', to='residents.vehicle')),
            ],
            options={
                'db_table': 'vehicle_documents',
                'ordering': ['-created_at'],
            },
        ),
    ]
