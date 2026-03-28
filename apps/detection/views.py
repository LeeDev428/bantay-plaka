from django.http import JsonResponse
from django.http import StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.core.files.base import ContentFile
from django.contrib.auth.decorators import login_required
import json
import base64
import binascii
import time
import os

import cv2

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


def _camera_rtsp_for_role(camera_role: str) -> str:
    if camera_role == VehicleLog.CAMERA_ROLE_ENTRY:
        return getattr(settings, 'ENTRY_CAMERA_RTSP', '').strip()
    if camera_role == VehicleLog.CAMERA_ROLE_EXIT:
        return getattr(settings, 'EXIT_CAMERA_RTSP', '').strip()
    return ''


def _mjpeg_frame_stream(rtsp_url: str):
    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    fail_count = 0
    try:
        if not cap.isOpened():
            return

        while True:
            ok, frame = cap.read()
            if not ok:
                fail_count += 1
                if fail_count >= 15:
                    cap.release()
                    time.sleep(0.5)
                    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    fail_count = 0
                time.sleep(0.2)
                continue
            fail_count = 0

            max_width = 960
            if frame.shape[1] > max_width:
                scale = max_width / frame.shape[1]
                frame = cv2.resize(
                    frame,
                    (max_width, int(frame.shape[0] * scale)),
                    interpolation=cv2.INTER_AREA,
                )

            encoded_ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if not encoded_ok:
                continue

            payload = buffer.tobytes()
            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + payload + b'\r\n'
            )
    finally:
        cap.release()


@login_required
def camera_preview(request, camera_role: str):
    role = _normalize_camera_role(camera_role)
    rtsp_url = _camera_rtsp_for_role(role)
    if role not in {VehicleLog.CAMERA_ROLE_ENTRY, VehicleLog.CAMERA_ROLE_EXIT}:
        return JsonResponse({'error': 'Invalid camera role'}, status=400)
    if not rtsp_url:
        return JsonResponse({'error': f'RTSP URL not configured for {role}'}, status=400)

    return StreamingHttpResponse(
        _mjpeg_frame_stream(rtsp_url),
        content_type='multipart/x-mixed-replace; boundary=frame',
    )


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
        snapshot_b64 = (data.get('snapshot_b64', '') or '').strip()

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

        if snapshot_b64:
            try:
                image_bytes = base64.b64decode(snapshot_b64, validate=True)
                safe_plate = ''.join(c for c in plate if c.isalnum())[:12] or 'plate'
                filename = f'{camera_role.lower()}_{safe_plate}_{log.pk}.jpg'
                log.snapshot.save(filename, ContentFile(image_bytes), save=True)
            except (binascii.Error, ValueError):
                pass

        broadcast_log(log)
        return JsonResponse({'ok': True, 'log_id': log.pk, 'status': status, 'camera_role': camera_role})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
