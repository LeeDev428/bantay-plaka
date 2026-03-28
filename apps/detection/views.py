from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
import json

from apps.logs.models import VehicleLog
from apps.logs.services import broadcast_log, broadcast_blacklist_alert
from apps.logs.views import resolve_plate
from apps.visitors.models import BlacklistEntry


def _check_api_key(request):
    """
    Validate the ANPR engine API key from the X-Api-Key header.
    The key is set via ANPR_API_KEY in .env / settings.py.
    """
    expected_key = getattr(settings, 'ANPR_API_KEY', None)
    if not expected_key:
        return False
    incoming_key = request.headers.get('X-Api-Key', '')
    return incoming_key == expected_key


def _normalize_camera_role(camera_role: str) -> str:
    role = (camera_role or '').strip().upper()
    if role in {
        VehicleLog.CAMERA_ROLE_ENTRY,
        VehicleLog.CAMERA_ROLE_EXIT,
        VehicleLog.CAMERA_ROLE_UNKNOWN,
    }:
        return role
    return VehicleLog.CAMERA_ROLE_UNKNOWN


def _next_status_for_plate(plate_number: str, camera_role: str = VehicleLog.CAMERA_ROLE_UNKNOWN) -> str:
    """
    Determine status from camera role when provided.
    Fallback to plate-history auto-toggle for backward compatibility.
    """
    if camera_role == VehicleLog.CAMERA_ROLE_ENTRY:
        return VehicleLog.STATUS_IN
    if camera_role == VehicleLog.CAMERA_ROLE_EXIT:
        return VehicleLog.STATUS_OUT

    last_log = (
        VehicleLog.objects
        .filter(plate_number__iexact=plate_number)
        .order_by('-timestamp')
        .values_list('status', flat=True)
        .first()
    )
    if last_log == VehicleLog.STATUS_IN:
        return VehicleLog.STATUS_OUT
    return VehicleLog.STATUS_IN


@csrf_exempt
def ingest_plate(request):
    """
    Endpoint called by the ANPR engine when a plate is detected.
    Requires header:  X-Api-Key: <ANPR_API_KEY from .env>
    POST JSON: { "plate_number": "ABC 1234", "camera_role": "ENTRY_CAM|EXIT_CAM|UNKNOWN" }
    Status is camera-role based when role is provided, otherwise auto-toggle fallback is used.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized — invalid or missing API key'}, status=401)

    try:
        data = json.loads(request.body)
        plate = data.get('plate_number', '').upper().strip()
        camera_role = _normalize_camera_role(data.get('camera_role', ''))

        if not plate:
            return JsonResponse({'error': 'plate_number required'}, status=400)

        if BlacklistEntry.objects.filter(plate_number__iexact=plate, is_active=True).exists():
            broadcast_blacklist_alert(plate)
            return JsonResponse({'ok': False, 'blocked': True, 'error': 'Plate is blacklisted'}, status=403)

        status = _next_status_for_plate(plate, camera_role)

        resolved = resolve_plate(plate)
        log = VehicleLog.objects.create(
            plate_number=plate,
            entry_type=resolved['entry_type'],
            status=status,
            source=VehicleLog.SOURCE_CAMERA,
            camera_role=camera_role,
            resident_name=resolved.get('resident_name', ''),
        )
        broadcast_log(log)
        return JsonResponse({'ok': True, 'log_id': log.pk, 'status': status, 'camera_role': camera_role})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
