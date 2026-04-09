from django.db import migrations


def migrate_unknown_to_visitor(apps, schema_editor):
    VehicleLog = apps.get_model('logs', 'VehicleLog')
    VehicleLog.objects.filter(entry_type='UNKNOWN').update(entry_type='VISITOR')


class Migration(migrations.Migration):

    dependencies = [
        ('logs', '0003_alter_vehiclelog_entry_type'),
    ]

    operations = [
        migrations.RunPython(migrate_unknown_to_visitor, migrations.RunPython.noop),
    ]
