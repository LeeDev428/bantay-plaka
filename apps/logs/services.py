from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from apps.logs.models import VehicleLog


def broadcast_log(vehicle_log: VehicleLog):
    """Push a log entry to all connected WebSocket clients."""
    channel_layer = get_channel_layer()
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
                'display_name': vehicle_log.get_display_name(),
                'timestamp': vehicle_log.timestamp.strftime('%b %d, %Y %I:%M:%S %p'),
            },
        }
    )
