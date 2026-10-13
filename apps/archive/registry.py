from apps.residents.models import Resident, Vehicle
from apps.logs.models import VehicleLog, VideoRecording, UnrecognizedPlateAlert
from apps.visitors.models import BlacklistEntry, Visitor

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
    "snapshots": {
        "model": VehicleLog,
        "label": "Snapshots",
        "display_fields": ["plate_number", "camera_role", "timestamp"],
    },
    "videos": {
        "model": VideoRecording,
        "label": "Recorded Videos",
        "display_fields": ["source_filename", "camera_role", "started_at"],
    },
    "alerts": {
        "model": UnrecognizedPlateAlert,
        "label": "Alerts",
        "display_fields": ["camera_role", "reason", "created_at"],
    },
    "blacklist": {
        "model": BlacklistEntry,
        "label": "Blacklist Entries",
        "display_fields": ["plate_number", "tag", "reason"],
    },
    "visitors": {
        "model": Visitor,
        "label": "Visitors",
        "display_fields": ["full_name", "plate_number", "host_name"],
    },
}
