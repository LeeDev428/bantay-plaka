from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0002_alter_user_role'),
    ]

    operations = [
        migrations.CreateModel(
            name='ArchivedItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('entity_type', models.CharField(choices=[('USER', 'User'), ('RESIDENT', 'Resident'), ('VEHICLE', 'Vehicle'), ('VISITOR', 'Visitor'), ('LOG', 'Vehicle Log')], db_index=True, max_length=20)),
                ('title', models.CharField(db_index=True, max_length=255)),
                ('source_app', models.CharField(blank=True, db_index=True, max_length=50)),
                ('source_pk', models.PositiveBigIntegerField(blank=True, db_index=True, null=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('archived_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('archived_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='archived_items', to='accounts.user')),
            ],
            options={
                'db_table': 'archived_items',
                'ordering': ['-archived_at'],
                'indexes': [models.Index(fields=['source_app', 'source_pk'], name='archived_it_source__05309f_idx')],
            },
        ),
    ]
