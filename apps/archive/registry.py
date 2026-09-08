from apps.residents.models import Resident, Vehicle
from apps.logs.models import VehicleLog
from apps.visitors.models import BlacklistEntry

ARCHIVE_REGISTRY = {
    "residents": {
        "model": Resident,
        "label": "Residents",
        "display_fields": ["full_name", "address", "contact_number"],
    },
    "vehicles": {
        "model": Vehicle,
        "label": "Vehicles",
        "display_fields": ["plate_number", "resident.full_name", "vehicle_type"],
    },
    "logs": {
        "model": VehicleLog,
        "label": "Vehicle / Gate Logs",
        "display_fields": ["plate_number", "camera_role", "timestamp"],
    },
    "blacklist": {
        "model": BlacklistEntry,
        "label": "Blacklist Entries",
        "display_fields": ["plate_number", "tag", "reason"],
    },
}
