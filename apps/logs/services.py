from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone

from apps.logs.models import VehicleLog


def get_active_blacklist_map(plates):
    from apps.visitors.models import BlacklistEntry

    normalized = {str(p).strip().upper() for p in plates if p}
    if not normalized:
        return {}

    entries = (
        BlacklistEntry.objects
        .filter(plate_number__in=normalized, is_active=True)
        .values('plate_number', 'tag', 'reason', 'remarks')
    )
    return {
        e['plate_number'].upper(): {
            'tag': e.get('tag', ''),
            'reason': e.get('reason', ''),
            'remarks': e.get('remarks', ''),
        }
        for e in entries
    }


def attach_blacklist_metadata(logs):
    logs_list = list(logs)
    bl_map = get_active_blacklist_map([log.plate_number for log in logs_list])
    for log in logs_list:
        info = bl_map.get((log.plate_number or '').upper())
        log.blacklist_tag = info.get('tag', '') if info else ''
        log.blacklist_reason = info.get('reason', '') if info else ''
        log.blacklist_remarks = info.get('remarks', '') if info else ''
    return logs_list


def broadcast_log(vehicle_log: VehicleLog):
    """Push a log entry to all connected WebSocket clients."""
    channel_layer = get_channel_layer()
    local_ts = timezone.localtime(vehicle_log.timestamp)
    bl_info = get_active_blacklist_map([vehicle_log.plate_number]).get(vehicle_log.plate_number.upper(), {})
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
                'snapshot_url': vehicle_log.snapshot.url if vehicle_log.snapshot else '',
                'display_name': vehicle_log.get_display_name(),
                'blacklist_tag': bl_info.get('tag', ''),
                'blacklist_remarks': bl_info.get('remarks', ''),
                'timestamp': local_ts.strftime('%b %d, %Y %I:%M:%S %p'),
            },
        }
    )


def broadcast_blacklist_alert(plate_number: str, tag: str = '', remarks: str = ''):
    """Push a high-visibility alert when a blacklisted plate is detected."""
    channel_layer = get_channel_layer()
    details = f' [{tag}]' if tag else ''
    async_to_sync(channel_layer.group_send)(
        'vehicle_logs',
        {
            'type': 'blacklist_alert',
            'data': {
                'event_type': 'blacklist_alert',
                'plate_number': plate_number,
                'title': 'Blacklisted Vehicle Detected',
                'message': (
                    f'Plate {plate_number}{details} is in blacklist. '
                    f'{remarks or "Please cooperate and proceed to the guard for verification and proper action."}'
                ),
                'tag': tag,
                'remarks': remarks,
            },
        }
    )
