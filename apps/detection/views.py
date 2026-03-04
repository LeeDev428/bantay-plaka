from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import json

from apps.logs.models import VehicleLog
from apps.logs.services import broadcast_log
from apps.logs.views import resolve_plate


@csrf_exempt
@login_required
def ingest_plate(request):
    """
    Endpoint called by the ANPR engine when a plate is detected.
    POST JSON: { "plate_number": "ABC 1234", "status": "TIME_IN" | "TIME_OUT" }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        plate = data.get('plate_number', '').upper().strip()
        status = data.get('status', VehicleLog.STATUS_IN)

        if not plate:
            return JsonResponse({'error': 'plate_number required'}, status=400)

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

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
