from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone
from apps.logs.models import VehicleLog


def broadcast_log(vehicle_log: VehicleLog):
    """Push a log entry to all connected WebSocket clients."""
    channel_layer = get_channel_layer()
    local_ts = timezone.localtime(vehicle_log.timestamp)
    async_to_sync(channel_layer.group_send)(
        'vehicle_logs',
        {
            'type': 'log_entry',
            'data': {
                'id': vehicle_log.pk,
                'plate_number': vehicle_log.plate_number,
                'entry_type': vehicle_log.entry_type,
                'status': vehicle_log.status,
                'source': vehicle_log.source,
                'camera_role': vehicle_log.camera_role,
                'display_name': vehicle_log.get_display_name(),
                'timestamp': local_ts.strftime('%b %d, %Y %I:%M:%S %p'),
            },
        }
    )


def broadcast_blacklist_alert(plate_number: str):
    """Push a high-visibility alert when a blacklisted plate is detected."""
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        'vehicle_logs',
        {
            'type': 'blacklist_alert',
            'data': {
                'event_type': 'blacklist_alert',
                'plate_number': plate_number,
                'title': 'Blacklisted Vehicle Detected',
                'message': f'Plate {plate_number} is in blacklist. Please cooperate and proceed to the guard for verification and proper action.',
            },
        }
    )
