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
        # No key configured — deny all requests for safety
        return False
    incoming_key = request.headers.get('X-Api-Key', '')
    return incoming_key == expected_key


@csrf_exempt
def ingest_plate(request):
    """
    Endpoint called by the ANPR engine when a plate is detected.
    Requires header:  X-Api-Key: <ANPR_API_KEY from .env>
    POST JSON: { "plate_number": "ABC 1234", "status": "TIME_IN" | "TIME_OUT" }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if not _check_api_key(request):
        return JsonResponse({'error': 'Unauthorized — invalid or missing API key'}, status=401)

    try:
        data = json.loads(request.body)
        plate = data.get('plate_number', '').upper().strip()
        status = data.get('status', VehicleLog.STATUS_IN)

        if not plate:
            return JsonResponse({'error': 'plate_number required'}, status=400)

        if status not in (VehicleLog.STATUS_IN, VehicleLog.STATUS_OUT):
            return JsonResponse({'error': 'status must be TIME_IN or TIME_OUT'}, status=400)

        resolved = resolve_plate(plate)
        log = VehicleLog.objects.create(
            plate_number=plate,
            entry_type=resolved['entry_type'],
            status=status,
            source=VehicleLog.SOURCE_CAMERA,
            resident_name=resolved.get('resident_name', ''),
        )
        broadcast_log(log)
        return JsonResponse({'ok': True, 'log_id': log.pk})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
