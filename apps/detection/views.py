from django.http import JsonResponse
from django.http import StreamingHttpResponse
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.core.files.base import ContentFile
from django.contrib.auth.decorators import login_required
import json
import base64
import binascii
import time
import os
import threading
from urllib.parse import urlparse, unquote

import cv2
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth

from apps.logs.models import VehicleLog
from apps.logs.services import broadcast_log, broadcast_blacklist_alert
from apps.logs.views import resolve_plate
from apps.visitors.models import BlacklistEntry


_FRAME_CACHE: dict[str, tuple[bytes, float]] = {}
_FRAME_CACHE_LOCK = threading.Lock()
_CAMERA_WORKERS: dict[str, threading.Thread] = {}
_CAMERA_WORKERS_LOCK = threading.Lock()


def _open_capture_fast(rtsp_url: str):
    """Open RTSP capture with short open/read timeouts to avoid request hangs."""
    open_timeout = int(getattr(cv2, 'CAP_PROP_OPEN_TIMEOUT_MSEC', 53))
    read_timeout = int(getattr(cv2, 'CAP_PROP_READ_TIMEOUT_MSEC', 54))
    try:
        return cv2.VideoCapture(
            rtsp_url,
            cv2.CAP_FFMPEG,
            [open_timeout, 2000, read_timeout, 2000],
        )
    except Exception:
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        try:
            cap.set(open_timeout, 2000)
            cap.set(read_timeout, 2000)
        except Exception:
            pass
        return cap


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
    def _normalize_rtsp_url(rtsp_url: str) -> str:
        if not rtsp_url or '://' not in rtsp_url:
            return rtsp_url
        scheme, rest = rtsp_url.split('://', 1)
        if rest.count('@') <= 1:
            return rtsp_url

        userinfo, hostpart = rest.rsplit('@', 1)
        userinfo = userinfo.replace('@', '%40')
        return f'{scheme}://{userinfo}@{hostpart}'

    if camera_role == VehicleLog.CAMERA_ROLE_ENTRY:
        return _normalize_rtsp_url(getattr(settings, 'ENTRY_CAMERA_RTSP', '').strip())
    if camera_role == VehicleLog.CAMERA_ROLE_EXIT:
        return _normalize_rtsp_url(getattr(settings, 'EXIT_CAMERA_RTSP', '').strip())
    return ''


def _rtsp_candidates(rtsp_url: str) -> list[str]:
    urls = [rtsp_url]
    if '/Streaming/Channels/101' in rtsp_url:
        urls.append(rtsp_url.replace('/Streaming/Channels/101', '/Streaming/Channels/102'))
    elif '/Streaming/Channels/102' in rtsp_url:
        urls.append(rtsp_url.replace('/Streaming/Channels/102', '/Streaming/Channels/101'))

    seen = set()
    unique = []
    for u in urls:
        if u and u not in seen:
            unique.append(u)
            seen.add(u)
    return unique


def _mjpeg_frame_stream(rtsp_url: str):
    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp|max_delay;500000|stimeout;5000000'
    candidate_urls = _rtsp_candidates(rtsp_url)
    candidate_idx = 0
    cap = cv2.VideoCapture(candidate_urls[candidate_idx], cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    fail_count = 0
    try:
        if not cap.isOpened():
            switched = False
            for idx, candidate in enumerate(candidate_urls[1:], start=1):
                probe = cv2.VideoCapture(candidate, cv2.CAP_FFMPEG)
                probe.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if probe.isOpened():
                    cap = probe
                    candidate_idx = idx
                    switched = True
                    break
                probe.release()
            if not switched:
                return

        while True:
            ok, frame = cap.read()
            if not ok:
                fail_count += 1
                if fail_count >= 15:
                    cap.release()
                    time.sleep(0.5)
                    candidate_idx = (candidate_idx + 1) % len(candidate_urls)
                    cap = cv2.VideoCapture(candidate_urls[candidate_idx], cv2.CAP_FFMPEG)
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


def _cached_mjpeg_stream(camera_role: str, rtsp_url: str):
    _ensure_camera_worker(camera_role, rtsp_url)
    last_ts = 0.0
    while True:
        with _FRAME_CACHE_LOCK:
            cached = _FRAME_CACHE.get(camera_role)

        if cached:
            frame_bytes, ts = cached
            if ts != last_ts:
                last_ts = ts
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n'
                )

        time.sleep(0.06)


def _camera_worker_loop(camera_role: str, rtsp_url: str):
    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp|max_delay;500000|stimeout;5000000'
    candidate_urls = _rtsp_candidates(rtsp_url)
    candidate_idx = 0
    cap = _open_capture_fast(candidate_urls[candidate_idx])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    fail_count = 0

    while True:
        if not cap.isOpened():
            cap.release()
            time.sleep(0.5)
            candidate_idx = (candidate_idx + 1) % len(candidate_urls)
            cap = _open_capture_fast(candidate_urls[candidate_idx])
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            continue

        ok, frame = cap.read()
        if not ok or frame is None:
            fail_count += 1
            if fail_count >= 12:
                cap.release()
                time.sleep(0.3)
                candidate_idx = (candidate_idx + 1) % len(candidate_urls)
                cap = _open_capture_fast(candidate_urls[candidate_idx])
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                fail_count = 0
            time.sleep(0.03)
            continue

        fail_count = 0

        max_width = 720
        if frame.shape[1] > max_width:
            scale = max_width / frame.shape[1]
            frame = cv2.resize(
                frame,
                (max_width, int(frame.shape[0] * scale)),
                interpolation=cv2.INTER_AREA,
            )

        encoded_ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        if encoded_ok:
            with _FRAME_CACHE_LOCK:
                _FRAME_CACHE[camera_role] = (buffer.tobytes(), time.time())

        time.sleep(0.03)


def _ensure_camera_worker(camera_role: str, rtsp_url: str):
    with _CAMERA_WORKERS_LOCK:
        thread = _CAMERA_WORKERS.get(camera_role)
        if thread and thread.is_alive():
            return

        worker = threading.Thread(
            target=_camera_worker_loop,
            args=(camera_role, rtsp_url),
            daemon=True,
            name=f'camera-worker-{camera_role.lower()}',
        )
        worker.start()
        _CAMERA_WORKERS[camera_role] = worker


def _read_single_frame(rtsp_url: str):
    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp|max_delay;500000|stimeout;5000000'
    for candidate in _rtsp_candidates(rtsp_url):
        cap = _open_capture_fast(candidate)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        try:
            if not cap.isOpened():
                continue

            # Skip a couple of frames to improve chance of clean decode.
            for _ in range(3):
                cap.read()

            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            max_width = 960
            if frame.shape[1] > max_width:
                scale = max_width / frame.shape[1]
                frame = cv2.resize(
                    frame,
                    (max_width, int(frame.shape[0] * scale)),
                    interpolation=cv2.INTER_AREA,
                )

            encoded_ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
            if encoded_ok:
                return buffer.tobytes()
        finally:
            cap.release()
    return None


def _read_camera_http_snapshot(rtsp_url: str):
    parsed = urlparse(rtsp_url)
    if not parsed.hostname:
        return None

    username = unquote(parsed.username or 'admin')
    password = unquote(parsed.password or '')
    channel = '101'
    if '/Streaming/Channels/102' in rtsp_url:
        channel = '102'

    snapshot_url = f'http://{parsed.hostname}/ISAPI/Streaming/channels/{channel}/picture'
    timeout = 1.2

    try:
        resp = requests.get(snapshot_url, auth=HTTPDigestAuth(username, password), timeout=timeout)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:
        pass

    try:
        resp = requests.get(snapshot_url, auth=HTTPBasicAuth(username, password), timeout=timeout)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:
        pass

    return None


@login_required
def camera_preview(request, camera_role: str):
    role = _normalize_camera_role(camera_role)
    rtsp_url = _camera_rtsp_for_role(role)
    if role not in {VehicleLog.CAMERA_ROLE_ENTRY, VehicleLog.CAMERA_ROLE_EXIT}:
        return JsonResponse({'error': 'Invalid camera role'}, status=400)
    if not rtsp_url:
        return JsonResponse({'error': f'RTSP URL not configured for {role}'}, status=400)

    return StreamingHttpResponse(
        _cached_mjpeg_stream(role, rtsp_url),
        content_type='multipart/x-mixed-replace; boundary=frame',
    )


@login_required
def camera_frame(request, camera_role: str):
    role = _normalize_camera_role(camera_role)
    rtsp_url = _camera_rtsp_for_role(role)
    if role not in {VehicleLog.CAMERA_ROLE_ENTRY, VehicleLog.CAMERA_ROLE_EXIT}:
        return JsonResponse({'error': 'Invalid camera role'}, status=400)
    if not rtsp_url:
        return JsonResponse({'error': f'RTSP URL not configured for {role}'}, status=400)

    _ensure_camera_worker(role, rtsp_url)

    with _FRAME_CACHE_LOCK:
        cached = _FRAME_CACHE.get(role)
    if cached:
        frame_bytes, ts = cached
        if (time.time() - ts) <= 4:
            resp = HttpResponse(frame_bytes, content_type='image/jpeg')
            resp['Cache-Control'] = 'no-store'
            return resp

    frame = _read_camera_http_snapshot(rtsp_url)
    if frame is None:
        return JsonResponse({'error': 'Camera frame unavailable'}, status=503)
    resp = HttpResponse(frame, content_type='image/jpeg')
    resp['Cache-Control'] = 'no-store'
    return resp


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
