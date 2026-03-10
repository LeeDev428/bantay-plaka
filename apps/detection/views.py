from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
import json

from apps.logs.models import VehicleLog
from apps.logs.services import broadcast_log
from apps.logs.views import resolve_plate


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


def _next_status_for_plate(plate_number: str) -> str:
    """
    Determine the next status for a plate based on its last log.
    If last log was TIME_IN  -> return TIME_OUT
    If last log was TIME_OUT -> return TIME_IN
    If no log exists         -> return TIME_IN (first visit)
    """
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
    POST JSON: { "plate_number": "ABC 1234" }
    Status (TIME_IN / TIME_OUT) is auto-determined based on the last log.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized — invalid or missing API key'}, status=401)

    try:
        data = json.loads(request.body)
        plate = data.get('plate_number', '').upper().strip()

        if not plate:
            return JsonResponse({'error': 'plate_number required'}, status=400)

        # Auto-toggle: check last log for this plate and assign the opposite
        status = _next_status_for_plate(plate)

        resolved = resolve_plate(plate)
        log = VehicleLog.objects.create(
            plate_number=plate,
            entry_type=resolved['entry_type'],
            status=status,
            source=VehicleLog.SOURCE_CAMERA,
            resident_name=resolved.get('resident_name', ''),
        )
        broadcast_log(log)
        return JsonResponse({'ok': True, 'log_id': log.pk, 'status': status})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
