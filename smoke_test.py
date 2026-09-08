from apps.residents.models import Resident, Vehicle
from apps.logs.models import VehicleLog
from apps.visitors.models import BlacklistEntry


def check(label, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")


def run_for(model_name, obj, manager_active, manager_archived):
    print(f"\n--- {model_name} (pk={obj.pk}) ---")
    obj.archive()
    check("removed from active manager after archive()",
          not manager_active.filter(pk=obj.pk).exists())
    check("present in archived manager after archive()",
          manager_archived.filter(pk=obj.pk).exists())

    obj.restore()
    check("present in active manager after restore()",
          manager_active.filter(pk=obj.pk).exists())


print("Creating throwaway test rows...")

r = Resident.objects.create(
    first_name="Test", last_name="Resident", address="123 Test St"
)
print("Created Resident pk=", r.pk)

v = Vehicle.objects.create(
    resident=r, plate_number="TEST-0001"
)
print("Created Vehicle pk=", v.pk)

vl = VehicleLog.objects.create(
    plate_number="TEST-0001", status=VehicleLog.STATUS_IN
)
print("Created VehicleLog pk=", vl.pk)

bl = BlacklistEntry.objects.create(
    plate_number="TEST-0001", reason="Smoke test entry", remarks="Created by smoke_test.py"
)
print("Created BlacklistEntry pk=", bl.pk)

run_for("Resident", r, Resident.objects, Resident.archived_objects)
run_for("Vehicle", v, Vehicle.objects, Vehicle.archived_objects)
run_for("VehicleLog", vl, VehicleLog.objects, VehicleLog.archived_objects)
run_for("BlacklistEntry", bl, BlacklistEntry.objects, BlacklistEntry.archived_objects)

print("\nCleaning up test rows...")
# delete in FK-safe order: Vehicle before Resident
for obj in [bl, vl, v, r]:
    obj.delete()
print("Done. All test rows removed.")
