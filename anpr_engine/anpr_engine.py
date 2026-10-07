#!/usr/bin/env python
"""
BantayPlaka ANPR Engine  (merged: ANPR + vehicle-triggered recording)
=====================================================================
Reads frames from an IP camera (or webcam) via RTSP/OpenCV and:

  1. VEHICLE GATE (trigger):  a local YOLOv8 COCO model (yolov8n.pt) checks
     "is a vehicle in view?" (car, motorcycle, bus, truck). Plate detection and
     OCR only run while a vehicle is present, and only inside the vehicle region.
  2. RECORDING (goal):  when a vehicle appears, a video clip starts recording.
     It stops after the vehicle has been gone for GRACE seconds, followed by a
     short cooldown. Each clip gets a .json sidecar listing the plates read.
  3. PLATE READING:  plate box (Roboflow model or custom YOLO weights) -> crop ->
     EasyOCR -> clean/validate -> vote -> debounce -> POST to Django.
     A clean plate-crop screenshot is also saved locally for every accepted plate.

DETECTION MODES (plate localisation, --mode):
  roboflow (default)  Roboflow "Plate Number Detection" model (needs API key).
  yolo                Custom plate-trained YOLO weights via --model path.
  ocr                 No plate detector: OCR on the vehicle crop only.
                      (Also the automatic fallback if the Roboflow model fails.)

Note: yolov8n.pt is ONLY used as the vehicle detector now. It cannot find plates.

USAGE:
  python anpr_engine/anpr_engine.py --rtsp 0
  python anpr_engine/anpr_engine.py --rtsp "rtsp://user:pass@192.168.1.108:554/Streaming/Channels/101" --camera-role ENTRY_CAM
  python anpr_engine/anpr_engine.py --rtsp 0 --no-record            # plates only
  python anpr_engine/anpr_engine.py --rtsp 0 --no-vehicle-gate      # old behaviour (no gate, no clips)
  python anpr_engine/anpr_engine.py --rtsp 0 --no-preview           # headless

  Put the camera URL in .env as ANPR_RTSP_URL to avoid typing credentials.

NOTE: TIME_IN / TIME_OUT is decided by Django. Pass --camera-role ENTRY_CAM or
      EXIT_CAM to enforce a fixed status per camera.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import queue
import re
import signal
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import cv2
import easyocr
import numpy as np
import requests
from dotenv import load_dotenv

try:
    import torch
except Exception:
    torch = None

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

os.environ.setdefault('CORE_MODEL_SAM_ENABLED', 'False')
os.environ.setdefault('CORE_MODEL_SAM3_ENABLED', 'False')
os.environ.setdefault('CORE_MODEL_GAZE_ENABLED', 'False')


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name, '') or '').strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name, '') or '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name, '') or '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on'}


def _resolve_runtime_device(requested: str) -> str:
    """Resolve runtime device safely: 'auto' -> cuda when available, else cpu."""
    choice = (requested or 'auto').strip().lower()
    if choice not in {'auto', 'cpu', 'cuda'}:
        choice = 'auto'

    cuda_ready = bool(
        torch
        and torch.cuda.is_available()
        and getattr(getattr(torch, 'version', None), 'cuda', None)
    )

    if choice == 'cpu':
        return 'cpu'
    if choice == 'cuda':
        if cuda_ready:
            return 'cuda:0'
        log.warning("CUDA was explicitly requested but is unavailable. Falling back to CPU.")
        return 'cpu'
    return 'cuda:0' if cuda_ready else 'cpu'


_BASE_DIR = Path(__file__).resolve().parent

DJANGO_API_KEY = os.getenv('ANPR_API_KEY', '')
ROBOFLOW_API_KEY = os.getenv('ROBOFLOW_API_KEY', '')
DEFAULT_RF_MODEL_ID = os.getenv('ROBOFLOW_MODEL_ID', 'plate-number-detection/5')
DEFAULT_INGEST_URL = (os.getenv('ANPR_INGEST_URL', '') or '').strip() or 'http://127.0.0.1:8000/detection/ingest/'

# Camera source can come from .env so credentials never live in code or shell history.
DEFAULT_CAMERA_SOURCE = (os.getenv('ANPR_RTSP_URL', '') or '').strip()

# Custom plate-trained YOLO weights (only for --mode yolo). yolov8n.pt is NOT valid here.
DEFAULT_PLATE_YOLO_MODEL = (os.getenv('ANPR_PLATE_YOLO_MODEL', '') or '').strip()

# ---------------------------------------------------------------------------
# Vehicle gate + recording settings (merged from vehicle_triggered_recorder.py)
# ---------------------------------------------------------------------------
VEHICLE_GATE_DEFAULT = _env_bool('ANPR_VEHICLE_GATE', True)
RECORD_CLIPS_DEFAULT = _env_bool('ANPR_RECORD_CLIPS', True)
VEHICLE_MODEL_PATH = (os.getenv('ANPR_VEHICLE_MODEL', '') or '').strip() or 'yolov8n.pt'
# COCO class IDs: 2=car, 3=motorcycle, 5=bus, 7=truck
VEHICLE_CLASS_IDS = {2, 3, 5, 7}
VEHICLE_CONFIDENCE = _env_float('ANPR_VEHICLE_CONFIDENCE', 0.50)
# Consecutive vehicle detections required before a clip starts (filters one-frame false positives).
VEHICLE_MIN_HITS = max(1, _env_int('ANPR_VEHICLE_MIN_HITS', 2))
# A vehicle sighting must be this recent to start a clip (covers ML queue latency).
VEHICLE_START_WINDOW_SECONDS = _env_float('ANPR_VEHICLE_START_WINDOW', 1.5)
# Vehicle gone this long -> clip stops AND the "vehicle episode" ends.
# Keep this larger than your worst-case ML latency per frame on CPU.
GRACE_PERIOD_SECONDS = _env_float('ANPR_GRACE_SECONDS', 3.0)
COOLDOWN_SECONDS = _env_float('ANPR_COOLDOWN_SECONDS', 5.0)
RECORD_FPS = max(1, _env_int('ANPR_RECORD_FPS', 15))  # used only if the camera does not report FPS
RECORDINGS_DIR = Path(
    os.getenv('ANPR_RECORDINGS_DIR', '') or (_BASE_DIR / 'recordings')
).expanduser()
SNAPSHOT_DIR = Path(
    os.getenv('ANPR_SNAPSHOT_DIR', '') or (_BASE_DIR.parent / 'media' / 'snapshots' / 'plates')
).expanduser()
SAVE_PLATE_SNAPSHOTS = _env_bool('ANPR_SAVE_PLATE_SNAPSHOTS', True)
# Save a few "unread plate" crops per clip for manual review.
MAX_REVIEW_CANDIDATES_PER_CLIP = max(0, _env_int('ANPR_REVIEW_CANDIDATES', 3))
REVIEW_CANDIDATE_MIN_INTERVAL_SECONDS = 2.0
# One accepted plate per vehicle episode. Stops a single car's OCR misreads becoming
# extra log entries (and phantom TIME_OUTs). Needs the vehicle gate.
ONE_PLATE_PER_EPISODE = _env_bool('ANPR_ONE_PLATE_PER_EPISODE', True)
# Pad around the vehicle box before looking for the plate.
VEHICLE_REGION_PAD_RATIO = 0.05
OVERLAY_TTL_SECONDS = 1.5

# ---------------------------------------------------------------------------
# Plate reading settings
# ---------------------------------------------------------------------------
DEBOUNCE_SECONDS = 30
MIN_OCR_CONFIDENCE = _env_float('ANPR_MIN_OCR_CONFIDENCE', 0.36)
DETECTOR_MIN_VOTE_CONFIDENCE = _env_float('ANPR_DETECTOR_VOTE_CONFIDENCE', 0.50)
FALLBACK_MIN_OCR_CONFIDENCE = _env_float('ANPR_FALLBACK_MIN_OCR_CONFIDENCE', 0.58)

VOTE_WINDOW_SECONDS = 1.4
MIN_VOTE_COUNT = 2
HIGH_CONF_SINGLE_SHOT = 0.80
DETECTOR_QUICK_ACCEPT_CONFIDENCE = _env_float('ANPR_DETECTOR_QUICK_ACCEPT_CONFIDENCE', 0.66)
FALLBACK_QUICK_ACCEPT_CONFIDENCE = _env_float('ANPR_FALLBACK_QUICK_ACCEPT_CONFIDENCE', 0.86)
FALLBACK_EVERY_N_FRAMES = max(1, _env_int('ANPR_FALLBACK_EVERY_N_FRAMES', 6))

HEARTBEAT_SNAPSHOT_SECONDS = max(0.10, _env_float('ANPR_HEARTBEAT_SECONDS', 1.0))
HEARTBEAT_SNAPSHOT_MAX_WIDTH = max(320, _env_int('ANPR_HEARTBEAT_MAX_WIDTH', 640))
HEARTBEAT_SNAPSHOT_JPEG_QUALITY = min(85, max(30, _env_int('ANPR_HEARTBEAT_JPEG_QUALITY', 45)))
# Plate-event evidence sent to Django is higher quality than the live-feed heartbeat.
EVENT_SNAPSHOT_MAX_WIDTH = max(320, _env_int('ANPR_EVENT_SNAPSHOT_MAX_WIDTH', 1280))
EVENT_SNAPSHOT_JPEG_QUALITY = min(95, max(40, _env_int('ANPR_EVENT_SNAPSHOT_JPEG_QUALITY', 80)))

# Demo profile for RTSP presentations.
DEMO_RTSP_MODE = _env_bool('ANPR_DEMO_RTSP_MODE', False)
DEMO_FORCE_FULLFRAME_OCR = _env_bool('ANPR_DEMO_FORCE_FULLFRAME_OCR', False)
DEMO_FOCUS_ROI_ONLY = _env_bool('ANPR_DEMO_FOCUS_ROI_ONLY', True)
DEMO_SKIP_RF_DETECTOR = _env_bool('ANPR_DEMO_SKIP_RF_DETECTOR', False)
DEMO_MIN_OCR_CONFIDENCE = _env_float('ANPR_DEMO_MIN_OCR_CONFIDENCE', 0.34)
DEMO_FALLBACK_MIN_OCR_CONFIDENCE = _env_float('ANPR_DEMO_FALLBACK_MIN_OCR_CONFIDENCE', 0.56)
DEMO_DETECTOR_MIN_VOTE_CONFIDENCE = _env_float('ANPR_DEMO_DETECTOR_VOTE_CONFIDENCE', 0.48)
DEMO_MIN_VOTE_COUNT = _env_int('ANPR_DEMO_MIN_VOTE_COUNT', 2)
DEMO_VOTE_WINDOW_SECONDS = _env_float('ANPR_DEMO_VOTE_WINDOW_SECONDS', 1.3)
DEMO_HIGH_CONF_SINGLE_SHOT = _env_float('ANPR_DEMO_HIGH_CONF_SINGLE_SHOT', 0.78)
DEMO_DETECTOR_QUICK_ACCEPT_CONFIDENCE = _env_float('ANPR_DEMO_DETECTOR_QUICK_ACCEPT_CONFIDENCE', 0.62)
DEMO_FALLBACK_QUICK_ACCEPT_CONFIDENCE = _env_float('ANPR_DEMO_FALLBACK_QUICK_ACCEPT_CONFIDENCE', 0.84)
DEMO_FALLBACK_EVERY_N_FRAMES = max(1, _env_int('ANPR_DEMO_FALLBACK_EVERY_N_FRAMES', 4))

DETECTION_CONFIDENCE = _env_float('ANPR_DETECTION_CONFIDENCE', 0.34)
DEFAULT_ANPR_DEVICE = (os.getenv('ANPR_DEVICE', 'auto') or 'auto').strip().lower()
VALID_CAMERA_ROLES = {'ENTRY_CAM', 'EXIT_CAM', 'UNKNOWN'}
DEFAULT_RTSP_DRAIN_GRABS = 0

# Runtime diagnostics + RTSP resilience.
DIAGNOSTIC_INTERVAL_SECONDS = 5.0
# RTSP reads may each block until their configured timeout; reconnect promptly
# rather than spending tens of seconds retrying a dead/stalled stream.
MAX_CONSECUTIVE_READ_FAILS = 3
MAX_CONSECUTIVE_INVALID_FRAMES = 12
MIN_VALID_FRAME_WIDTH = 160
MIN_VALID_FRAME_HEIGHT = 120
RECONNECT_DELAY_START = 0.8
RECONNECT_DELAY_MAX = 15.0
# Alternate stream paths can lock accounts and silently change resolution, so opt-in only.
RTSP_TRY_FALLBACK_PATHS = _env_bool('ANPR_RTSP_FALLBACKS', False)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('bantayplaka.anpr')

_DIGIT_LIKE_MAP = {'O': '0', 'Q': '0', 'I': '1', 'L': '1', 'B': '8'}
_LETTER_LIKE_MAP = {'0': 'O', '1': 'I', '8': 'B'}

_PLATE_PATTERNS = (
    re.compile(r'^[A-Z]{2,4}\d{3,4}$'),
    re.compile(r'^\d{3,4}[A-Z]{2,4}$'),
)

ALLOWED_PLATE_FORMAT = (os.getenv('ANPR_ALLOWED_PLATE_FORMAT', 'PH_STRICT') or 'PH_STRICT').strip().upper()
FALLBACK_MIN_NO_BOX_STREAK = max(1, _env_int('ANPR_FALLBACK_MIN_NO_BOX_STREAK', 10))


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _redact_url(url) -> str:
    """Hide credentials in RTSP URLs before they reach logs."""
    text = str(url)
    if '://' not in text:
        return text
    parsed = urlparse(text)
    if '@' not in parsed.netloc:
        return text
    host = parsed.netloc.rsplit('@', 1)[1]
    return urlunparse(parsed._replace(netloc=f'***:***@{host}'))


def _normalize_rtsp_url(rtsp_url: str) -> str:
    """Normalize RTSP URL and safely encode userinfo so special chars don't break OpenCV/FFmpeg."""
    if not rtsp_url or '://' not in rtsp_url:
        return rtsp_url
    parsed = urlparse(rtsp_url)
    if parsed.scheme.lower() != 'rtsp':
        return rtsp_url
    netloc = parsed.netloc
    if '@' not in netloc:
        return rtsp_url

    userinfo, host = netloc.rsplit('@', 1)
    userinfo = userinfo.replace('@', '%40')
    return urlunparse(parsed._replace(netloc=f'{userinfo}@{host}'))


def _rtsp_candidates(rtsp_url: str) -> list[str]:
    """Candidate URLs. Only the given URL unless ANPR_RTSP_FALLBACKS is enabled."""
    source = _normalize_rtsp_url((rtsp_url or '').strip())
    if not source or '://' not in source or not RTSP_TRY_FALLBACK_PATHS:
        return [source]

    candidates: list[str] = [source]
    if '/Streaming/Channels/101' in source:
        candidates.append(source.replace('/Streaming/Channels/101', '/Streaming/Channels/102'))
    elif '/Streaming/Channels/102' in source:
        candidates.append(source.replace('/Streaming/Channels/102', '/Streaming/Channels/101'))

    parsed = urlparse(source)
    for path in ('/stream1', '/live', '/h264'):
        alt = urlunparse(parsed._replace(path=path, params='', query='', fragment=''))
        if alt not in candidates:
            candidates.append(alt)
    return candidates


def _derive_frame_ingest_url(ingest_url: str) -> str:
    normalized = (ingest_url or '').strip()
    if not normalized:
        return ''
    if '/detection/ingest/' in normalized:
        return normalized.replace('/detection/ingest/', '/detection/ingest-frame/')
    if normalized.endswith('/ingest/'):
        return normalized[:-len('ingest/')] + 'ingest-frame/'
    if normalized.endswith('/ingest'):
        return normalized + '-frame'
    return normalized


def _derive_recording_ingest_url(ingest_url: str) -> str:
    normalized = (ingest_url or '').strip()
    if not normalized:
        return ''
    if '/detection/ingest/' in normalized:
        return normalized.replace('/detection/ingest/', '/detection/ingest-recording/')
    if normalized.endswith('/ingest/'):
        return normalized[:-len('ingest/')] + 'ingest-recording/'
    if normalized.endswith('/ingest'):
        return normalized + '-recording'
    return ''


def validate_decoded_frame(frame: np.ndarray | None) -> tuple[bool, str]:
    """Validate decoded frames so None/corrupt frames are visible in logs and recovery flow."""
    if frame is None:
        return False, 'frame=None'
    if not isinstance(frame, np.ndarray):
        return False, f'invalid-type={type(frame).__name__}'
    if frame.size == 0:
        return False, 'empty-frame'
    if frame.ndim < 2:
        return False, f'invalid-ndim={frame.ndim}'

    h, w = frame.shape[:2]
    if w < MIN_VALID_FRAME_WIDTH or h < MIN_VALID_FRAME_HEIGHT:
        return False, f'too-small={w}x{h}'
    if frame.dtype != np.uint8:
        return False, f'unexpected-dtype={frame.dtype}'
    return True, f'{w}x{h}'


def detect_plate_like_rectangles(frame: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Fast contour fallback for demo: find plate-like rectangles in the lower image area."""
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.bilateralFilter(gray, 7, 60, 60)
        edges = cv2.Canny(blur, 70, 180)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    except Exception:
        return []

    fh, fw = frame.shape[:2]
    frame_area = float(fh * fw)
    boxes: list[tuple[int, int, int, int]] = []

    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:40]:
        area = cv2.contourArea(contour)
        if area < frame_area * 0.008:
            continue

        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.03 * peri, True)
        if len(approx) != 4:
            continue

        x, y, w, h = cv2.boundingRect(approx)
        if w <= 0 or h <= 0:
            continue

        ar = w / float(h)
        if ar < 1.7 or ar > 7.0:
            continue
        if (w * h) > frame_area * 0.45:
            continue
        if y + (h / 2.0) < fh * 0.35:
            continue

        boxes.append((x, y, x + w, y + h))

    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    return boxes[:4]


# ---------------------------------------------------------------------------
# Plate text cleaning
# ---------------------------------------------------------------------------

def clean_plate_text(raw_text: str) -> str | None:
    """
    Normalize raw OCR output into a Philippine license plate format.
    Format: ABC 1234 (3 letters + space + 4 digits)
            AB 1234  (motorcycle: 2 letters + space + 4 digits)
    Returns None if the text looks like garbage.
    """
    compact = ''.join(c for c in str(raw_text).upper() if c.isalnum())
    if len(compact) < 5:
        return None

    def format_compact(value: str) -> str | None:
        if _PLATE_PATTERNS[0].fullmatch(value):
            split_at = next((i for i, ch in enumerate(value) if ch.isdigit()), len(value))
            return f"{value[:split_at]} {value[split_at:]}"
        if _PLATE_PATTERNS[1].fullmatch(value):
            split_at = next((i for i, ch in enumerate(value) if ch.isalpha()), len(value))
            return f"{value[:split_at]} {value[split_at:]}"
        return None

    direct = format_compact(compact)
    if direct:
        return direct

    slices: list[str] = []
    n = len(compact)
    for size in range(8, 4, -1):
        if size > n:
            continue
        for start in range(0, n - size + 1):
            slices.append(compact[start:start + size])

    best_value: str | None = None
    best_score: tuple[int, int] | None = None

    def try_orientation(value: str, left_is_letters: bool):
        nonlocal best_value, best_score
        length = len(value)
        left_range = range(2, 5) if left_is_letters else range(3, 5)
        for left_len in left_range:
            right_len = length - left_len
            if left_is_letters and not (3 <= right_len <= 4):
                continue
            if (not left_is_letters) and not (2 <= right_len <= 4):
                continue

            left = value[:left_len]
            right = value[left_len:]

            if left_is_letters:
                fixed_left = ''.join(_LETTER_LIKE_MAP.get(c, c) for c in left)
                fixed_right = ''.join(_DIGIT_LIKE_MAP.get(c, c) for c in right)
                valid = fixed_left.isalpha() and fixed_right.isdigit()
                candidate_compact = fixed_left + fixed_right
            else:
                fixed_left = ''.join(_DIGIT_LIKE_MAP.get(c, c) for c in left)
                fixed_right = ''.join(_LETTER_LIKE_MAP.get(c, c) for c in right)
                valid = fixed_left.isdigit() and fixed_right.isalpha()
                candidate_compact = fixed_left + fixed_right

            if not valid:
                continue

            formatted = format_compact(candidate_compact)
            if not formatted:
                continue

            substitutions = sum(1 for a, b in zip(left + right, candidate_compact) if a != b)
            score = (substitutions, -len(candidate_compact))
            if best_score is None or score < best_score:
                best_score = score
                best_value = formatted

    for value in slices:
        try_orientation(value, left_is_letters=True)
        try_orientation(value, left_is_letters=False)

    return best_value


def is_strict_plate(plate: str) -> bool:
    return bool(re.fullmatch(r'(?:[A-Z]{2,4} \d{3,4}|\d{3,4} [A-Z]{2,4})', plate))


def is_demo_strict_plate(plate: str) -> bool:
    # Accept only 3x3 formats to suppress random text hits.
    return bool(re.fullmatch(r'(?:[A-Z]{3} \d{3}|\d{3} [A-Z]{3})', plate))


def is_allowed_plate_format(plate: str) -> bool:
    """Apply runtime-selectable plate format filter to suppress non-plate text."""
    if ALLOWED_PLATE_FORMAT == 'PH_STRICT':
        return is_strict_plate(plate)
    if ALLOWED_PLATE_FORMAT == 'PH_3X3':
        return is_demo_strict_plate(plate)
    return is_strict_plate(plate)


def is_plausible_plate_bbox(bbox_xyxy: tuple[int, int, int, int] | None) -> bool:
    if not bbox_xyxy:
        return False
    x1, y1, x2, y2 = bbox_xyxy
    w = max(1, x2 - x1)
    h = max(1, y2 - y1)
    ratio = w / float(h)
    return 1.6 <= ratio <= 8.0


def normalize_plate_variant_noise(plate: str) -> str:
    """Collapse common OCR one-character prefix noise for stable dedupe/voting."""
    if not is_strict_plate(plate):
        return plate

    try:
        left, right = plate.split(' ', 1)
    except ValueError:
        return plate

    confusable_prefixes = {'I', 'L', 'G', 'T', 'J'}

    if left.isalpha() and right.isdigit() and len(left) == 4 and left[0] in confusable_prefixes:
        return f'{left[1:]} {right}'
    if left.isdigit() and right.isalpha() and len(right) == 4 and right[0] in confusable_prefixes:
        return f'{left} {right[1:]}'
    return plate


def build_ocr_variants(plate_crop: np.ndarray) -> list[np.ndarray]:
    """Create multiple image variants to improve OCR hit rate under blur/lighting noise."""
    variants: list[np.ndarray] = [plate_crop]

    h0, w0 = plate_crop.shape[:2]
    if w0 < 420:
        scale = max(1.0, 420.0 / max(1, w0))
        upscaled = cv2.resize(
            plate_crop,
            (int(w0 * scale), int(h0 * scale)),
            interpolation=cv2.INTER_CUBIC,
        )
        variants.append(upscaled)
        plate_crop = upscaled

    gray = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY)
    variants.append(gray)

    _, th_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(th_otsu)
    return variants


def build_fast_fullframe_ocr_variants(frame: np.ndarray) -> list[np.ndarray]:
    """Low-cost region OCR variants to keep RTSP processing responsive."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    variants: list[np.ndarray] = [gray]

    normalized = cv2.normalize(gray, gray.copy(), 0, 255, cv2.NORM_MINMAX)
    variants.append(normalized)

    _, otsu = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(otsu)
    return variants


def extract_plate_candidates_from_ocr(ocr_results) -> list[tuple[str, float, tuple[int, int, int, int] | None]]:
    """Build plate candidates from OCR results, including split-token combinations like 'AB' + '123'."""
    items: list[dict] = []
    for row in ocr_results or []:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        bbox, text, conf = row[0], row[1], row[2]
        if text is None:
            continue

        raw = ''.join(c for c in str(text).upper() if c.isalnum())
        if not raw:
            continue

        try:
            xs = [int(p[0]) for p in bbox]
            ys = [int(p[1]) for p in bbox]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        except Exception:
            x1 = y1 = x2 = y2 = 0

        items.append(
            {
                'raw': raw,
                'conf': float(conf),
                'bbox': (x1, y1, x2, y2),
                'cx': (x1 + x2) / 2,
                'cy': (y1 + y2) / 2,
                'h': max(1, y2 - y1),
            }
        )

    candidates: list[tuple[str, float, tuple[int, int, int, int] | None]] = []
    seen: set[tuple[str, int, int, int, int]] = set()

    def add_candidate(text_value: str, conf_value: float, bbox_value: tuple[int, int, int, int] | None):
        plate = clean_plate_text(text_value)
        if not plate:
            return
        key_bbox = bbox_value or (0, 0, 0, 0)
        key = (plate, key_bbox[0], key_bbox[1], key_bbox[2], key_bbox[3])
        if key in seen:
            return
        seen.add(key)
        candidates.append((plate, conf_value, bbox_value))

    for item in items:
        add_candidate(item['raw'], item['conf'], item['bbox'])

    for i, left in enumerate(items):
        for j, right in enumerate(items):
            if i == j:
                continue
            if left['cx'] >= right['cx']:
                continue

            same_line = abs(left['cy'] - right['cy']) <= max(left['h'], right['h']) * 0.8
            if not same_line:
                continue

            alpha_left = left['raw'].isalpha() and 2 <= len(left['raw']) <= 4
            digit_right = right['raw'].isdigit() and 3 <= len(right['raw']) <= 4
            digit_left = left['raw'].isdigit() and 3 <= len(left['raw']) <= 4
            alpha_right = right['raw'].isalpha() and 2 <= len(right['raw']) <= 4
            if not ((alpha_left and digit_right) or (digit_left and alpha_right)):
                continue

            x1 = min(left['bbox'][0], right['bbox'][0])
            y1 = min(left['bbox'][1], right['bbox'][1])
            x2 = max(left['bbox'][2], right['bbox'][2])
            y2 = max(left['bbox'][3], right['bbox'][3])
            merged_bbox = (x1, y1, x2, y2)
            merged_conf = min(left['conf'], right['conf'])

            add_candidate(f"{left['raw']} {right['raw']}", merged_conf, merged_bbox)
            add_candidate(f"{left['raw']}{right['raw']}", merged_conf, merged_bbox)

    return candidates


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

class VehicleDetector:
    """
    Pre-filter: pretrained YOLOv8 (COCO) answering "is a vehicle in this frame?".
    Returns the largest confident car/motorcycle/bus/truck box.
    """

    def __init__(self, model_path: str, device: str = 'cpu'):
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("The 'ultralytics' package is not installed. Run: pip install ultralytics") from exc

        log.info("Loading vehicle detector: %s (first run auto-downloads yolov8n.pt)", model_path)
        self._model = YOLO(model_path)
        self._device = device if device in {'cpu', 'cuda:0'} else 'cpu'
        self._infer_calls = 0
        if self._device.startswith('cuda'):
            try:
                self._model.to(self._device)
            except Exception as exc:
                log.warning("Vehicle detector CUDA init failed (%s). Falling back to CPU.", exc)
                self._device = 'cpu'
        log.info("Vehicle detector ready on %s.", self._device)

    def _predict(self, frame: np.ndarray, device: str):
        return self._model.predict(
            frame,
            conf=VEHICLE_CONFIDENCE,
            classes=sorted(VEHICLE_CLASS_IDS),
            device=device,
            verbose=False,
        )[0]

    def detect_largest(self, frame: np.ndarray) -> tuple[int, int, int, int] | None:
        self._infer_calls += 1
        try:
            result = self._predict(frame, self._device)
        except Exception as exc:
            if self._device.startswith('cuda'):
                log.warning("Vehicle CUDA inference failed (%s). Switching to CPU.", exc)
                self._device = 'cpu'
                try:
                    result = self._predict(frame, 'cpu')
                except Exception as cpu_exc:
                    log.warning("Vehicle detection error: %s", cpu_exc)
                    return None
            else:
                log.warning("Vehicle detection error: %s", exc)
                return None

        best_box = None
        best_area = 0
        for box in (result.boxes or []):
            class_id = int(box.cls[0])
            if class_id not in VEHICLE_CLASS_IDS:
                continue
            if float(box.conf[0]) < VEHICLE_CONFIDENCE:
                continue
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area > best_area:
                best_box = (x1, y1, x2, y2)
                best_area = area
        return best_box


class NullDetector:
    """No plate localisation: the engine OCRs the vehicle crop directly (--mode ocr, or Roboflow failure)."""

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        return []

    def get_debug_stats(self) -> dict[str, object]:
        return {'infer_calls': 0, 'zero_box_calls': None, 'no_box_streak': None, 'last_variant': 'ocr-only'}


class RoboflowDetector:
    """
    Detects license plates using the Roboflow-trained model (Plate Number Detection v5).
    First run downloads and caches the model; after that it runs offline.
    Raises RuntimeError on setup problems so the engine can decide how to degrade.
    """

    def __init__(self, model_id: str, api_key: str, device: str = 'cpu'):
        try:
            from inference import get_model
        except ImportError as exc:
            raise RuntimeError("The 'inference' package is not installed. Run: pip install inference") from exc

        if not api_key:
            raise RuntimeError(
                "ROBOFLOW_API_KEY is not set in .env (Roboflow -> Settings -> API Keys)."
            )

        self._device = device
        log.info(f"Loading Roboflow model: {model_id}")
        log.info("First run downloads and caches the model (~30 sec). Next runs are instant.")
        try:
            self._model = get_model(model_id=model_id, api_key=api_key, device=self._device)
        except TypeError:
            self._model = get_model(model_id=model_id, api_key=api_key)
        self._infer_calls = 0
        self._zero_box_calls = 0
        self._last_variant = 'none'
        self._no_box_streak = 0
        log.info("Roboflow model ready.")

    def _infer_variant(self, image: np.ndarray, scale_back: float, parse_predictions, variant_label: str):
        self._infer_calls += 1
        results = self._model.infer(image, confidence=DETECTION_CONFIDENCE)
        boxes = parse_predictions(results, scale_back=scale_back)
        if boxes:
            self._last_variant = variant_label
        return boxes

    def get_debug_stats(self) -> dict[str, object]:
        return {
            'infer_calls': self._infer_calls,
            'zero_box_calls': self._zero_box_calls,
            'no_box_streak': self._no_box_streak,
            'last_variant': self._last_variant,
        }

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Returns list of (x1, y1, x2, y2) bounding boxes for detected plates."""
        boxes: list[tuple[int, int, int, int]] = []

        def parse_predictions(results_obj, scale_back: float = 1.0) -> list[tuple[int, int, int, int]]:
            parsed: list[tuple[int, int, int, int]] = []
            if not results_obj:
                return parsed

            raw_predictions = []
            first = results_obj[0]
            if hasattr(first, 'predictions'):
                raw_predictions = first.predictions or []
            elif isinstance(first, dict):
                raw_predictions = first.get('predictions') or first.get('objects') or []

            for prediction in raw_predictions:
                if isinstance(prediction, dict):
                    x = prediction.get('x')
                    y = prediction.get('y')
                    w = prediction.get('width')
                    h = prediction.get('height')
                else:
                    x = getattr(prediction, 'x', None)
                    y = getattr(prediction, 'y', None)
                    w = getattr(prediction, 'width', None)
                    h = getattr(prediction, 'height', None)

                if x is None or y is None or w is None or h is None:
                    continue

                parsed.append((
                    int((x - w / 2) * scale_back),
                    int((y - h / 2) * scale_back),
                    int((x + w / 2) * scale_back),
                    int((y + h / 2) * scale_back),
                ))
            return parsed

        try:
            boxes = self._infer_variant(frame, 1.0, parse_predictions, 'bgr')

            if not boxes:
                self._no_box_streak += 1
                h, w = frame.shape[:2]
                max_dim = max(h, w)
                # Vehicle crops are small, so probe an upscaled copy on no-box streaks.
                if self._no_box_streak % 3 == 0 and max_dim < 1600:
                    scale = min(2.0, 1600.0 / max(1.0, float(max_dim)))
                    upscaled = cv2.resize(
                        frame,
                        (int(w * scale), int(h * scale)),
                        interpolation=cv2.INTER_CUBIC,
                    )
                    boxes = self._infer_variant(upscaled, 1.0 / scale, parse_predictions, 'upscaled-bgr')
            else:
                self._no_box_streak = 0

            if not boxes:
                self._zero_box_calls += 1
        except Exception as e:
            log.warning(f"Roboflow detection error: {e}")
        return boxes


class YOLODetector:
    """Plate detector using CUSTOM plate-trained YOLO weights (--mode yolo --model plates.pt)."""

    def __init__(self, model_path: str, device: str = 'cpu'):
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("The 'ultralytics' package is not installed. Run: pip install ultralytics") from exc

        if not model_path:
            raise RuntimeError(
                "--mode yolo needs plate-trained weights (--model path/to/plates.pt). "
                "yolov8n.pt is only used as the VEHICLE detector and cannot find plates."
            )
        if not os.path.exists(model_path):
            raise RuntimeError(f"YOLO plate model file not found: {model_path}")

        log.info(f"Loading YOLO plate model: {model_path}")
        self._model = YOLO(model_path)
        self._device = device if device in {'cpu', 'cuda:0'} else 'cpu'
        self._infer_calls = 0
        if self._device.startswith('cuda'):
            try:
                self._model.to(self._device)
            except Exception as exc:
                log.warning("YOLO CUDA init failed (%s). Falling back to CPU.", exc)
                self._device = 'cpu'
        log.info("YOLO plate model loaded on %s.", self._device)

    def get_debug_stats(self) -> dict[str, object]:
        return {
            'infer_calls': self._infer_calls,
            'zero_box_calls': None,
            'no_box_streak': None,
            'last_variant': 'yolo',
        }

    def _run(self, frame: np.ndarray, device: str) -> list[tuple[int, int, int, int]]:
        boxes = []
        results = self._model(frame, conf=DETECTION_CONFIDENCE, verbose=False, device=device)
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                boxes.append((x1, y1, x2, y2))
        return boxes

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        self._infer_calls += 1
        try:
            return self._run(frame, self._device)
        except Exception as e:
            if self._device.startswith('cuda'):
                log.warning("YOLO CUDA inference failed (%s). Retrying on CPU.", e)
                self._device = 'cpu'
                try:
                    return self._run(frame, 'cpu')
                except Exception as cpu_exc:
                    log.warning(f"YOLO detection error: {cpu_exc}")
            else:
                log.warning(f"YOLO detection error: {e}")
        return []


# ---------------------------------------------------------------------------
# Clip recorder (merged from vehicle_triggered_recorder.py)
# ---------------------------------------------------------------------------

class ClipRecorder:
    """Writes one vehicle clip (.mp4) plus a .json sidecar listing plates read during it."""

    def __init__(self, output_dir: Path, camera_role: str):
        self.output_dir = Path(output_dir)
        self.camera_role = camera_role
        self._writer = None
        self._path: Path | None = None
        self._size: tuple[int, int] | None = None
        self._fps = float(RECORD_FPS)
        self._started_wall = 0.0
        self._started_iso = ''
        self._frames = 0

    @property
    def active(self) -> bool:
        return self._writer is not None

    @property
    def started_at(self) -> float:
        return self._started_wall

    def start(self, frame: np.ndarray, fps: float) -> Path | None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        h, w = frame.shape[:2]
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        path = self.output_dir / f'vehicle_{stamp}_{self.camera_role.lower()}.mp4'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
        if not writer.isOpened():
            writer.release()
            log.error("Could not open video writer for %s", path)
            return None
        self._writer = writer
        self._path = path
        self._size = (w, h)
        self._fps = fps
        self._started_wall = time.time()
        self._started_iso = datetime.now().isoformat(timespec='seconds')
        self._frames = 0
        snapshot_path = path.with_suffix('.jpg')
        if not cv2.imwrite(str(snapshot_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
            log.error("Could not save vehicle alert snapshot for %s", path)
        return path

    def write(self, frame: np.ndarray) -> bool:
        if self._writer is None:
            return False
        h, w = frame.shape[:2]
        if self._size != (w, h):
            return False
        self._writer.write(frame)
        self._frames += 1
        return True

    def stop(self, events: list[dict], reason: str) -> tuple[Path | None, float]:
        if self._writer is None:
            return None, 0.0
        self._writer.release()
        path = self._path
        duration = time.time() - self._started_wall
        meta = {
            'clip': path.name if path else None,
            'camera_role': self.camera_role,
            'started_at': self._started_iso,
            'ended_at': datetime.now().isoformat(timespec='seconds'),
            'duration_seconds': round(duration, 1),
            'fps': self._fps,
            'frames_written': self._frames,
            'stop_reason': reason,
            'plates': events,
            'unrecognized_plate_alert': not bool(events),
        }
        try:
            if path:
                path.with_suffix('.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
        except Exception as exc:
            log.warning("Could not write clip sidecar: %s", exc)
        self._writer = None
        self._path = None
        self._size = None
        return path, duration


# ---------------------------------------------------------------------------
# Main ANPR Engine
# ---------------------------------------------------------------------------

class ANPREngine:

    def __init__(
        self,
        rtsp_url: str,
        ingest_url: str,
        mode: str = 'roboflow',
        camera_role: str = 'UNKNOWN',
        device: str = DEFAULT_ANPR_DEVICE,
        rf_model_id: str = DEFAULT_RF_MODEL_ID,
        yolo_model_path: str = DEFAULT_PLATE_YOLO_MODEL,
        debounce_seconds: int = DEBOUNCE_SECONDS,
        frame_skip: int = 2,
        rtsp_drain_grabs: int = DEFAULT_RTSP_DRAIN_GRABS,
        heartbeat_seconds: float = HEARTBEAT_SNAPSHOT_SECONDS,
        vehicle_gate: bool = VEHICLE_GATE_DEFAULT,
        record_clips: bool = RECORD_CLIPS_DEFAULT,
        vehicle_model_path: str = VEHICLE_MODEL_PATH,
        recordings_dir: Path = RECORDINGS_DIR,
        snapshot_dir: Path = SNAPSHOT_DIR,
        grace_seconds: float = GRACE_PERIOD_SECONDS,
        cooldown_seconds: float = COOLDOWN_SECONDS,
    ):
        self.ingest_url = ingest_url
        self.ingest_frame_url = _derive_frame_ingest_url(ingest_url)
        self.ingest_recording_url = _derive_recording_ingest_url(ingest_url)
        self._is_rtsp_source = isinstance(rtsp_url, str) and '://' in rtsp_url
        requested_role = (camera_role or 'UNKNOWN').strip().upper()
        self.camera_role = requested_role if requested_role in VALID_CAMERA_ROLES else 'UNKNOWN'
        self.runtime_device = _resolve_runtime_device(device)
        self.debounce_seconds = debounce_seconds
        self.frame_skip = max(1, int(frame_skip))
        self.rtsp_drain_grabs = max(0, int(rtsp_drain_grabs))
        self.heartbeat_seconds = max(0.10, float(heartbeat_seconds))
        self.demo_mode = bool(self._is_rtsp_source and DEMO_RTSP_MODE)
        self.min_ocr_confidence = MIN_OCR_CONFIDENCE
        self.detector_vote_confidence = DETECTOR_MIN_VOTE_CONFIDENCE
        self.fallback_min_ocr_confidence = FALLBACK_MIN_OCR_CONFIDENCE
        self.vote_window_seconds = VOTE_WINDOW_SECONDS
        self.min_vote_count = MIN_VOTE_COUNT
        self.high_conf_single_shot = HIGH_CONF_SINGLE_SHOT
        self.detector_quick_accept_confidence = DETECTOR_QUICK_ACCEPT_CONFIDENCE
        self.fallback_quick_accept_confidence = FALLBACK_QUICK_ACCEPT_CONFIDENCE
        self.fallback_every_n_frames = FALLBACK_EVERY_N_FRAMES

        if self.demo_mode:
            self.min_ocr_confidence = min(MIN_OCR_CONFIDENCE, DEMO_MIN_OCR_CONFIDENCE)
            self.detector_vote_confidence = min(DETECTOR_MIN_VOTE_CONFIDENCE, DEMO_DETECTOR_MIN_VOTE_CONFIDENCE)
            self.fallback_min_ocr_confidence = min(FALLBACK_MIN_OCR_CONFIDENCE, DEMO_FALLBACK_MIN_OCR_CONFIDENCE)
            self.vote_window_seconds = min(VOTE_WINDOW_SECONDS, DEMO_VOTE_WINDOW_SECONDS)
            self.min_vote_count = max(1, DEMO_MIN_VOTE_COUNT)
            self.high_conf_single_shot = min(HIGH_CONF_SINGLE_SHOT, DEMO_HIGH_CONF_SINGLE_SHOT)
            self.detector_quick_accept_confidence = min(
                DETECTOR_QUICK_ACCEPT_CONFIDENCE, DEMO_DETECTOR_QUICK_ACCEPT_CONFIDENCE)
            self.fallback_quick_accept_confidence = min(
                FALLBACK_QUICK_ACCEPT_CONFIDENCE, DEMO_FALLBACK_QUICK_ACCEPT_CONFIDENCE)
            self.fallback_every_n_frames = max(1, DEMO_FALLBACK_EVERY_N_FRAMES)

        # Vehicle gate / recording configuration
        self.grace_seconds = max(0.5, float(grace_seconds))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.vehicle_min_hits = VEHICLE_MIN_HITS
        self.vehicle_start_window = VEHICLE_START_WINDOW_SECONDS
        self.snapshot_dir = Path(snapshot_dir)
        self.save_plate_snapshots = SAVE_PLATE_SNAPSHOTS
        self.record_enabled = bool(record_clips)
        self.vehicle_detector: VehicleDetector | None = None

        # Counters / state
        self._last_logged: dict[str, float] = {}
        self._vote_history: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self._frames_no_box = 0
        self._processed_frames = 0
        self._frames_read = 0
        self._read_failures = 0
        self._invalid_frames = 0
        self._detector_frames = 0
        self._detector_box_frames = 0
        self._detector_box_count = 0
        self._ocr_candidates = 0
        self._accepted_plates = 0
        self._dropped_by_confidence = 0
        self._dropped_by_debounce = 0
        self._dropped_by_episode = 0
        self._vehicle_frames = 0
        self._gate_skipped = 0
        self._clips_saved = 0
        self._last_heartbeat_post_ts = 0.0
        self._active_source = str(rtsp_url)
        self._last_diag_ts = time.time()
        self._source_fps = 0.0
        self._post_backoff_until = 0.0

        # Shared between the ML worker thread and the capture loop.
        self._state_lock = threading.Lock()
        self._vehicle_last_seen: float | None = None
        self._vehicle_hits = 0
        self._episode_id = 0
        self._episode_logged = False
        self._overlays: dict[str, tuple[float, list[tuple[tuple[int, int, int, int], str, tuple[int, int, int]]]]] = {}
        self._clip_events: list[dict] = []
        self._review_saved_this_clip = 0
        self._last_review_ts = 0.0
        self._cooldown_until = 0.0

        self._recorder = ClipRecorder(Path(recordings_dir), self.camera_role)
        self._recording_upload_queue: queue.Queue[Path] = queue.Queue(maxsize=10)
        self._recording_upload_enabled = bool(self.ingest_recording_url and DJANGO_API_KEY)
        if self._recording_upload_enabled:
            threading.Thread(
                target=self._recording_upload_loop,
                name=f'{self.camera_role}-recording-uploader',
                daemon=True,
            ).start()

        # --- Vehicle gate (YOLO COCO pre-filter) ---
        if vehicle_gate:
            try:
                self.vehicle_detector = VehicleDetector(vehicle_model_path, device=self.runtime_device)
            except Exception as exc:
                log.error("Vehicle detector failed to initialize: %s", exc)
                sys.exit(1)
        else:
            log.warning("Vehicle gate DISABLED: plate OCR runs on every frame region and no clips are recorded.")
            if self.record_enabled:
                log.warning("Recording needs the vehicle gate as its trigger, so recording is turned off.")
                self.record_enabled = False

        # --- Plate detector ---
        if mode == 'roboflow':
            try:
                self.detector = RoboflowDetector(rf_model_id, ROBOFLOW_API_KEY, device=self.runtime_device)
            except Exception as exc:
                log.error("Roboflow detector failed to initialize: %s", exc)
                self.detector = self._fallback_plate_detector(yolo_model_path)
        elif mode == 'yolo':
            try:
                self.detector = YOLODetector(yolo_model_path, device=self.runtime_device)
            except Exception as exc:
                log.error("YOLO plate detector failed to initialize: %s", exc)
                sys.exit(1)
        elif mode == 'ocr':
            self.detector = NullDetector()
            log.warning("OCR-only mode: no plate localisation. Accuracy will be lower.")
        else:
            log.error(f"Unknown mode '{mode}'. Use 'roboflow', 'yolo' or 'ocr'.")
            sys.exit(1)

        # --- EasyOCR ---
        use_gpu = self.runtime_device.startswith('cuda')
        log.info("Runtime device: %s. EasyOCR %s mode.", self.runtime_device, 'GPU' if use_gpu else 'CPU')
        log.info("Loading EasyOCR (first run downloads ~200 MB, then cached locally)...")
        self.ocr = easyocr.Reader(['en'], gpu=use_gpu)
        log.info("EasyOCR ready.")
        log.info(
            "Thresholds: detector=%.2f ocr=%.2f fallback_ocr=%.2f vote=%.2f | gate=%s record=%s grace=%.1fs cooldown=%.1fs",
            DETECTION_CONFIDENCE, self.min_ocr_confidence, self.fallback_min_ocr_confidence,
            self.detector_vote_confidence, bool(self.vehicle_detector), self.record_enabled,
            self.grace_seconds, self.cooldown_seconds,
        )
        if self.demo_mode:
            log.warning("DEMO RTSP MODE ENABLED: aggressive OCR and relaxed vote thresholds are active.")
        if not DJANGO_API_KEY:
            log.warning("ANPR_API_KEY is not set in .env: plates will be read/recorded but NOT sent to Django.")

    def _fallback_plate_detector(self, yolo_model_path: str):
        """Roboflow failed: use custom plate weights if given, otherwise OCR on the vehicle crop."""
        if yolo_model_path:
            try:
                log.warning("Falling back to custom YOLO plate weights: %s", yolo_model_path)
                return YOLODetector(yolo_model_path, device=self.runtime_device)
            except Exception as exc:
                log.error("YOLO plate fallback failed: %s", exc)
        log.warning(
            "*** NO PLATE DETECTOR ACTIVE *** Falling back to OCR-only on the vehicle crop. "
            "Fix the Roboflow setup for full accuracy."
        )
        return NullDetector()

    # ------------------------------------------------------------------
    # Diagnostics / housekeeping
    # ------------------------------------------------------------------

    def _prune_state(self, now: float):
        horizon = max(self.debounce_seconds * 4, 300)
        for plate in [p for p, ts in self._last_logged.items() if now - ts > horizon]:
            self._last_logged.pop(plate, None)
        for plate in list(self._vote_history.keys()):
            votes = [(ts, c) for (ts, c) in self._vote_history[plate] if now - ts <= self.vote_window_seconds]
            if votes:
                self._vote_history[plate] = votes
            else:
                self._vote_history.pop(plate, None)
        with self._state_lock:
            self._clip_events = self._clip_events[-50:]

    def _maybe_log_diagnostics(self, force: bool = False):
        now = time.time()
        if not force and (now - self._last_diag_ts) < DIAGNOSTIC_INTERVAL_SECONDS:
            return

        detector_debug = {}
        if hasattr(self.detector, 'get_debug_stats'):
            try:
                detector_debug = self.detector.get_debug_stats() or {}
            except Exception:
                detector_debug = {}

        log.info(
            "[DIAG] role=%s source=%s vehicle_gate=%s frames_read=%d processed=%d read_fail=%d invalid=%d "
            "vehicle_frames=%d gate_skipped=%d recording=%s clips=%d "
            "detector_frames=%d detector_box_frames=%d detector_boxes=%d ocr_candidates=%d "
            "accepted=%d drop_conf=%d drop_debounce=%d drop_episode=%d rf_calls=%s rf_zero_box=%s rf_variant=%s",
            self.camera_role, _redact_url(self._active_source),
            'on' if self.vehicle_detector is not None else 'off',
            self._frames_read, self._processed_frames,
            self._read_failures, self._invalid_frames, self._vehicle_frames, self._gate_skipped,
            self._recorder.active, self._clips_saved,
            self._detector_frames, self._detector_box_frames, self._detector_box_count,
            self._ocr_candidates, self._accepted_plates, self._dropped_by_confidence,
            self._dropped_by_debounce, self._dropped_by_episode,
            detector_debug.get('infer_calls', '?'), detector_debug.get('zero_box_calls', '?'),
            detector_debug.get('last_variant', '?'),
        )
        self._prune_state(now)
        self._last_diag_ts = now

    # ------------------------------------------------------------------
    # Debounce / voting
    # ------------------------------------------------------------------

    def _is_debounced(self, plate: str) -> bool:
        return (time.time() - self._last_logged.get(plate, 0)) < self.debounce_seconds

    def _record_logged(self, plate: str):
        self._last_logged[plate] = time.time()

    def _has_vote_consensus(self, plate: str, confidence: float, min_conf: float) -> bool:
        now = time.time()
        votes = self._vote_history[plate]
        votes.append((now, float(confidence)))
        votes[:] = [(ts, conf) for (ts, conf) in votes if (now - ts) <= self.vote_window_seconds]

        if confidence >= self.high_conf_single_shot:
            return True
        if len(votes) < self.min_vote_count:
            return False
        avg_conf = sum(conf for _, conf in votes) / len(votes)
        return avg_conf >= min_conf

    def _passes_consensus(self, plate: str, confidence: float, min_conf: float, quick_accept_conf: float) -> bool:
        if confidence >= quick_accept_conf:
            return True
        return self._has_vote_consensus(plate, confidence, min_conf)

    # ------------------------------------------------------------------
    # Snapshots / Django posts
    # ------------------------------------------------------------------

    def _build_snapshot_b64(
        self,
        frame: np.ndarray,
        max_width: int = HEARTBEAT_SNAPSHOT_MAX_WIDTH,
        quality: int = HEARTBEAT_SNAPSHOT_JPEG_QUALITY,
    ) -> str:
        try:
            image = frame
            if frame.shape[1] > max_width:
                scale = max_width / frame.shape[1]
                image = cv2.resize(frame, (max_width, int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode('.jpg', image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if ok:
                return base64.b64encode(encoded.tobytes()).decode('ascii')
        except Exception:
            pass
        return ''

    def _post_to_django(self, plate: str, snapshot_b64: str = '') -> bool:
        """POST the detected plate to Django. Returns True on success."""
        if not DJANGO_API_KEY:
            log.error("ANPR_API_KEY is not set in .env -- cannot send plate to Django.")
            return False
        try:
            resp = requests.post(
                self.ingest_url,
                json={'plate_number': plate, 'camera_role': self.camera_role, 'snapshot_b64': snapshot_b64},
                headers={'Content-Type': 'application/json', 'X-Api-Key': DJANGO_API_KEY},
                timeout=5,
            )
            if resp.status_code == 200:
                result = resp.json()
                log.info(
                    f"[LOGGED] '{plate}' ({self.camera_role}) -> {result.get('status', '?')} "
                    f"(Log ID {result.get('log_id')})"
                )
                return True
            log.error(f"[REJECTED] Django returned {resp.status_code}: {resp.text}")
        except requests.exceptions.ConnectionError:
            log.error("Cannot reach Django at %s -- is Daphne running?", self.ingest_url)
        except requests.exceptions.Timeout:
            log.error("Django request timed out.")
        except Exception as e:
            log.error("Unexpected error posting to Django: %s", e)
        return False

    def _post_frame_heartbeat(self, snapshot_b64: str, camera_source: str) -> bool:
        if (
            self.camera_role not in {'ENTRY_CAM', 'EXIT_CAM'}
            or not snapshot_b64
            or not DJANGO_API_KEY
            or not self.ingest_frame_url
        ):
            return False
        try:
            resp = requests.post(
                self.ingest_frame_url,
                json={
                    'camera_role': self.camera_role,
                    'camera_source': camera_source,
                    'snapshot_b64': snapshot_b64,
                },
                headers={'Content-Type': 'application/json', 'X-Api-Key': DJANGO_API_KEY},
                timeout=5,
            )
        except requests.exceptions.RequestException as exc:
            log.warning("Camera heartbeat request failed: %s", exc)
            return False
        if resp.status_code != 200:
            log.warning("Camera heartbeat rejected by Django (HTTP %d): %s", resp.status_code, resp.text[:300])
            return False
        return True

    def _save_image(self, prefix: str, label: str, image: np.ndarray | None) -> str | None:
        """Save a local JPEG evidence image. Returns the path or None."""
        if image is None or image.size == 0:
            return None
        try:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            safe = re.sub(r'[^A-Z0-9]+', '_', label.upper()).strip('_')
            name = f'{prefix}_{stamp}_{safe}.jpg' if safe else f'{prefix}_{stamp}.jpg'
            path = self.snapshot_dir / name
            if cv2.imwrite(str(path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
                return str(path)
        except Exception as exc:
            log.warning("Could not save %s image: %s", prefix, exc)
        return None

    def _maybe_save_review_candidate(self, plate_crop: np.ndarray):
        """Plate box found but nothing readable: keep a few crops per clip for manual review."""
        if MAX_REVIEW_CANDIDATES_PER_CLIP <= 0 or not self.save_plate_snapshots:
            return
        now = time.time()
        with self._state_lock:
            if self._review_saved_this_clip >= MAX_REVIEW_CANDIDATES_PER_CLIP:
                return
            if (now - self._last_review_ts) < REVIEW_CANDIDATE_MIN_INTERVAL_SECONDS:
                return
            self._review_saved_this_clip += 1
            self._last_review_ts = now
        path = self._save_image('unread', '', plate_crop)
        if path:
            log.info("[PLATE NUMBER NOT READ] Candidate saved for review -> %s", path)

    # ------------------------------------------------------------------
    # Vehicle state, overlays, clip events
    # ------------------------------------------------------------------

    def _set_overlay(self, key: str, items: list[tuple[tuple[int, int, int, int], str, tuple[int, int, int]]]):
        with self._state_lock:
            self._overlays[key] = (time.time(), items)

    def _update_vehicle_state(self, vbox: tuple[int, int, int, int] | None):
        now = time.time()
        with self._state_lock:
            if vbox is None:
                self._vehicle_hits = 0
                return
            # A vehicle returning after the grace period starts a NEW episode.
            if self._vehicle_last_seen is None or (now - self._vehicle_last_seen) > self.grace_seconds:
                self._episode_id += 1
                self._episode_logged = False
            self._vehicle_last_seen = now
            self._vehicle_hits += 1
            self._vehicle_frames += 1
        self._set_overlay('vehicle', [(vbox, 'VEHICLE', (255, 160, 0))])

    def _episode_blocks_logging(self) -> bool:
        if not (ONE_PLATE_PER_EPISODE and self.vehicle_detector is not None):
            return False
        with self._state_lock:
            return self._episode_logged

    def _mark_episode_logged(self):
        with self._state_lock:
            self._episode_logged = True

    def _note_clip_event(self, plate: str, confidence: float, snapshot_path: str | None, sent: bool, source: str):
        with self._state_lock:
            self._clip_events.append({
                'time': datetime.now().isoformat(timespec='seconds'),
                'ts': time.time(),
                'plate': plate,
                'ocr_confidence': round(float(confidence), 3),
                'source': source,
                'sent_to_django': sent,
                'snapshot': snapshot_path,
            })

    def _padded_region(self, frame: np.ndarray, vbox: tuple[int, int, int, int]):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = vbox
        pad_x = int((x2 - x1) * VEHICLE_REGION_PAD_RATIO)
        pad_y = int((y2 - y1) * VEHICLE_REGION_PAD_RATIO)
        x1 = max(0, x1 - pad_x)
        y1 = max(0, y1 - pad_y)
        x2 = min(w, x2 + pad_x)
        y2 = min(h, y2 + pad_y)
        return frame[y1:y2, x1:x2], x1, y1

    # ------------------------------------------------------------------
    # Plate logging (shared by detector path and fallback path)
    # ------------------------------------------------------------------

    def _try_log_plate(
        self,
        frame: np.ndarray,
        plate: str,
        confidence: float,
        bbox: tuple[int, int, int, int] | None,
        plate_crop: np.ndarray | None,
        source: str,
    ) -> str:
        """Returns one of: 'posted', 'failed', 'debounced', 'episode', 'backoff'."""
        if self._episode_blocks_logging():
            self._dropped_by_episode += 1
            return 'episode'

        if self._is_debounced(plate):
            # Refresh so a car sitting in view is not re-logged when the window lapses.
            self._record_logged(plate)
            self._dropped_by_debounce += 1
            log.info("Skipping '%s' -- debounced (%ss).", plate, self.debounce_seconds)
            return 'debounced'

        if time.time() < self._post_backoff_until:
            return 'backoff'

        log.info("%s plate: '%s' (OCR conf: %.2f)", 'Fallback' if source == 'fallback' else 'Detector',
                 plate, confidence)

        annotated = frame.copy()
        try:
            if bbox:
                x1, y1, x2, y2 = bbox
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(annotated, plate, (int(x1), max(20, int(y1) - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            else:
                cv2.putText(annotated, plate, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        except Exception:
            pass

        if bbox:
            self._set_overlay('plate', [(bbox, plate, (0, 255, 0))])

        snapshot_path = None
        if self.save_plate_snapshots:
            snapshot_path = self._save_image('plate', plate, plate_crop if plate_crop is not None else annotated)
            if snapshot_path:
                log.info("[PLATE CAPTURED] '%s' -> saved %s", plate, snapshot_path)

        snapshot_b64 = self._build_snapshot_b64(
            annotated, max_width=EVENT_SNAPSHOT_MAX_WIDTH, quality=EVENT_SNAPSHOT_JPEG_QUALITY)

        sent = self._post_to_django(plate, snapshot_b64=snapshot_b64)
        self._note_clip_event(plate, confidence, snapshot_path, sent, source)
        if sent:
            self._record_logged(plate)
            self._accepted_plates += 1
            self._mark_episode_logged()
            return 'posted'

        self._post_backoff_until = time.time() + 3.0
        return 'failed'

    # ------------------------------------------------------------------
    # Per-frame ML pipeline (runs on the ML worker thread)
    # ------------------------------------------------------------------

    def _process_frame(self, frame: np.ndarray):
        """Vehicle gate -> plate detection inside the vehicle region -> OCR -> validate -> post."""
        self._processed_frames += 1
        ox = oy = 0
        region = frame

        if self.vehicle_detector is not None:
            vbox = self.vehicle_detector.detect_largest(frame)
            self._update_vehicle_state(vbox)
            if vbox is None:
                self._gate_skipped += 1
                self._frames_no_box = 0  # no vehicle means no meaningful no-box streak
                return
            region, ox, oy = self._padded_region(frame, vbox)
            if region.size == 0:
                return

        self._detector_frames += 1
        self._read_plates(frame, region, ox, oy)

    def _read_plates(self, frame: np.ndarray, region: np.ndarray, ox: int, oy: int):
        h, w = frame.shape[:2]
        pad = 10

        if self.demo_mode and DEMO_SKIP_RF_DETECTOR:
            raw_boxes = detect_plate_like_rectangles(region)
        else:
            raw_boxes = self.detector.detect(region)
            if self.demo_mode and not raw_boxes:
                raw_boxes = detect_plate_like_rectangles(region)

        # Convert region coordinates back to full-frame coordinates.
        boxes: list[tuple[int, int, int, int]] = []
        for (bx1, by1, bx2, by2) in raw_boxes:
            fx1 = max(0, min(w, bx1 + ox))
            fy1 = max(0, min(h, by1 + oy))
            fx2 = max(0, min(w, bx2 + ox))
            fy2 = max(0, min(h, by2 + oy))
            if fx2 - fx1 > 1 and fy2 - fy1 > 1:
                boxes.append((fx1, fy1, fx2, fy2))

        if boxes:
            self._frames_no_box = 0
            self._detector_box_frames += 1
            self._detector_box_count += len(boxes)
            self._set_overlay('plate_raw', [(b, 'plate?', (0, 215, 255)) for b in boxes])
        else:
            self._frames_no_box += 1
            if self._frames_no_box % 30 == 0:
                log.info("No plate boxes detected in last %s processed frames.", self._frames_no_box)

        for (x1, y1, x2, y2) in boxes:
            x1 = max(0, x1 - pad)
            y1 = max(0, y1 - pad)
            x2 = min(w, x2 + pad)
            y2 = min(h, y2 + pad)

            # Clean crop taken from the untouched frame (no drawn overlays inside it).
            plate_crop = frame[y1:y2, x1:x2].copy()
            if plate_crop.size == 0:
                continue

            if self.demo_mode:
                gray_crop = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY)
                _, th_crop = cv2.threshold(gray_crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                ocr_variants = [gray_crop, th_crop]
            else:
                ocr_variants = build_ocr_variants(plate_crop)

            best_conf_by_plate: dict[str, float] = {}
            for candidate in ocr_variants:
                ocr_results = self.ocr.readtext(
                    candidate,
                    allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ',
                    detail=1,
                    paragraph=False,
                )
                for (plate, confidence, _) in extract_plate_candidates_from_ocr(ocr_results):
                    self._ocr_candidates += 1
                    plate = normalize_plate_variant_noise(plate)
                    if not is_allowed_plate_format(plate):
                        continue
                    if confidence < self.min_ocr_confidence:
                        self._dropped_by_confidence += 1
                        continue
                    # Keep the best read across variants (a weak early read must not block a strong later one).
                    if confidence > best_conf_by_plate.get(plate, 0.0):
                        best_conf_by_plate[plate] = confidence

                # Early exit once a variant gives a confident read: saves OCR passes.
                if best_conf_by_plate and max(best_conf_by_plate.values()) >= self.detector_quick_accept_confidence:
                    break

            if not best_conf_by_plate:
                self._maybe_save_review_candidate(plate_crop)
                continue

            ranked = sorted(best_conf_by_plate.items(), key=lambda item: item[1], reverse=True)
            for plate, confidence in ranked:
                if not self._passes_consensus(
                    plate, confidence, self.detector_vote_confidence, self.detector_quick_accept_confidence
                ):
                    continue
                # Act on the top consensus candidate only; lower-ranked ones are usually OCR variants of it.
                self._try_log_plate(frame, plate, confidence, (x1, y1, x2, y2), plate_crop, 'detector')
                break

        # Fallback: detector found no plate box, so OCR the region itself every few processed frames.
        # With the vehicle gate on, this is limited to the vehicle crop (far fewer false hits).
        fallback_every = 1 if (self.demo_mode and DEMO_FORCE_FULLFRAME_OCR) else self.fallback_every_n_frames
        if (
            not boxes
            and self._frames_no_box >= FALLBACK_MIN_NO_BOX_STREAK
            and self._processed_frames % fallback_every == 0
        ):
            self._fallback_region_ocr(frame, region, ox, oy)

    def _fallback_region_ocr(self, frame: np.ndarray, region: np.ndarray, ox: int, oy: int):
        h, w = frame.shape[:2]
        variants: list[tuple[np.ndarray, int, int]] = []

        if self.demo_mode:
            fh, fw = region.shape[:2]
            rx1 = max(0, min(fw - 1, int(fw * 0.22)))
            ry1 = max(0, min(fh - 1, int(fh * 0.52)))
            rx2 = max(rx1 + 1, min(fw, int(fw * 0.82)))
            ry2 = max(ry1 + 1, min(fh, int(fh * 0.93)))
            roi = region[ry1:ry2, rx1:rx2]
            if roi.size:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                variants.append((gray, rx1, ry1))
                _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                variants.append((th, rx1, ry1))

            # Skip the top timestamp overlay strip when scanning the full (ungated) frame.
            safe_top = int(fh * 0.22) if self.vehicle_detector is None else 0
            if safe_top < fh - 10:
                safe_region = region[safe_top:, :]
                safe_gray = cv2.cvtColor(safe_region, cv2.COLOR_BGR2GRAY)
                variants.append((safe_gray, 0, safe_top))
                _, safe_th = cv2.threshold(safe_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                variants.append((safe_th, 0, safe_top))

        if not (self.demo_mode and DEMO_FOCUS_ROI_ONLY):
            for variant in build_fast_fullframe_ocr_variants(region):
                variants.append((variant, 0, 0))

        frame_seen: set[str] = set()
        for candidate, off_x, off_y in variants:
            ocr_results = self.ocr.readtext(
                candidate,
                allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ',
                detail=1,
                paragraph=False,
            )
            for (plate, confidence, bbox_xyxy) in extract_plate_candidates_from_ocr(ocr_results):
                if bbox_xyxy:
                    bx1, by1, bx2, by2 = bbox_xyxy
                    bbox_xyxy = (bx1 + off_x + ox, by1 + off_y + oy, bx2 + off_x + ox, by2 + off_y + oy)

                self._ocr_candidates += 1
                plate = normalize_plate_variant_noise(plate)
                if plate in frame_seen:
                    continue
                if not is_allowed_plate_format(plate):
                    continue
                if not is_plausible_plate_bbox(bbox_xyxy):
                    continue
                if confidence < self.fallback_min_ocr_confidence:
                    self._dropped_by_confidence += 1
                    continue
                # Count one vote per frame per plate, and only for reads that cleared every check.
                frame_seen.add(plate)
                if not self._passes_consensus(
                    plate, confidence, self.fallback_min_ocr_confidence, self.fallback_quick_accept_confidence
                ):
                    continue

                plate_crop = None
                if bbox_xyxy:
                    cx1, cy1, cx2, cy2 = bbox_xyxy
                    cx1, cy1 = max(0, cx1), max(0, cy1)
                    cx2, cy2 = min(w, cx2), min(h, cy2)
                    if cx2 > cx1 and cy2 > cy1:
                        plate_crop = frame[cy1:cy2, cx1:cx2].copy()

                self._try_log_plate(frame, plate, confidence, bbox_xyxy, plate_crop, 'fallback')
                return

    # ------------------------------------------------------------------
    # Recording control (runs on the capture loop thread)
    # ------------------------------------------------------------------

    def _clip_fps(self) -> float:
        fps = self._source_fps
        return fps if 5.0 <= fps <= 60.0 else float(RECORD_FPS)

    def _manage_recording(self, frame: np.ndarray, now: float):
        if not self.record_enabled:
            return

        with self._state_lock:
            last_seen = self._vehicle_last_seen
            hits = self._vehicle_hits

        if not self._recorder.active:
            fresh = last_seen is not None and (now - last_seen) <= self.vehicle_start_window
            if not (fresh and hits >= self.vehicle_min_hits and now >= self._cooldown_until):
                return
            path = self._recorder.start(frame, self._clip_fps())
            if path is None:
                self._cooldown_until = now + self.cooldown_seconds
                return
            with self._state_lock:
                self._review_saved_this_clip = 0
            log.info("[RECORDING STARTED] Vehicle detected -> %s", path)

        if not self._recorder.write(frame):
            self._stop_clip('frame size changed or write failed')
            return

        gone_for = (now - last_seen) if last_seen else 0.0
        if gone_for > self.grace_seconds:
            self._stop_clip('vehicle left')

    def _stop_clip(self, reason: str):
        if not self._recorder.active:
            return
        started = self._recorder.started_at
        with self._state_lock:
            events = [{k: v for k, v in e.items() if k != 'ts'} for e in self._clip_events if e['ts'] >= started]
        path, duration = self._recorder.stop(events, reason)
        self._cooldown_until = time.time() + self.cooldown_seconds
        if path:
            self._clips_saved += 1
            log.info("[RECORDING STOPPED] %s (%.1fs, %d plate event(s)) -> %s",
                     reason, duration, len(events), path)
            if self._recording_upload_enabled:
                try:
                    self._recording_upload_queue.put_nowait(path)
                except queue.Full:
                    log.error("Recording upload queue is full; clip remains on disk and was not uploaded: %s", path)

    def _recording_upload_loop(self):
        while True:
            path = self._recording_upload_queue.get()
            try:
                self._upload_recording(path)
            finally:
                self._recording_upload_queue.task_done()

    def _upload_recording(self, path: Path):
        metadata_path = path.with_suffix('.json')
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Cannot upload recording without valid metadata (%s): %s", metadata_path, exc)
            return

        for attempt in range(1, 3):
            try:
                with path.open('rb') as video_file:
                    upload_files = {'recording': (path.name, video_file, 'video/mp4')}
                    snapshot_path = path.with_suffix('.jpg')
                    if metadata.get('unrecognized_plate_alert') and snapshot_path.is_file():
                        with snapshot_path.open('rb') as snapshot_file:
                            upload_files['alert_snapshot'] = (
                                snapshot_path.name,
                                snapshot_file,
                                'image/jpeg',
                            )
                            response = requests.post(
                                self.ingest_recording_url,
                                data={
                                    'camera_role': self.camera_role,
                                    'metadata': json.dumps(metadata),
                                },
                                files=upload_files,
                                headers={'X-Api-Key': DJANGO_API_KEY},
                                timeout=(10, 180),
                            )
                    else:
                        response = requests.post(
                            self.ingest_recording_url,
                            data={
                                'camera_role': self.camera_role,
                                'metadata': json.dumps(metadata),
                            },
                            files=upload_files,
                            headers={'X-Api-Key': DJANGO_API_KEY},
                            timeout=(10, 180),
                        )
                if response.status_code == 200:
                    log.info("Uploaded recording to hosted gallery: %s", path.name)
                    return
                log.error(
                    "Recording upload rejected (HTTP %d, attempt %d/2): %s",
                    response.status_code,
                    attempt,
                    response.text[:300],
                )
                if response.status_code < 500:
                    return
            except (OSError, requests.exceptions.RequestException) as exc:
                log.error("Recording upload failed (attempt %d/2): %s", attempt, exc)
        log.error("Recording upload exhausted retries; local copy retained: %s", path)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _draw_preview(self, frame: np.ndarray):
        now = time.time()
        with self._state_lock:
            overlays = [items for (ts, items) in self._overlays.values() if (now - ts) <= OVERLAY_TTL_SECONDS]
        for items in overlays:
            for (bbox, label, color) in items:
                x1, y1, x2, y2 = bbox
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, label, (x1, max(18, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        if self._recorder.active:
            cv2.circle(frame, (24, 24), 8, (0, 0, 255), -1)
            cv2.putText(frame, 'REC', (40, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    def run(self, show_preview: bool = True):
        """Open the camera and run the vehicle-gated ANPR loop until stopped."""
        source: str | int = self._active_source
        is_rtsp = isinstance(source, str) and '://' in source
        webcam_sources: list[int] = []

        if is_rtsp:
            os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
                'rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|'
                'max_delay;500000|stimeout;7000000|reorder_queue_size;0'
            )
            source = _normalize_rtsp_url(str(source))
            rtsp_candidates = _rtsp_candidates(str(source))
        else:
            rtsp_candidates = [source]

        if str(source).isdigit():
            source = int(source)
            webcam_sources = [source]
            if source != 1:
                webcam_sources.append(1)
            if source != 0:
                webcam_sources.append(0)
            webcam_sources = list(dict.fromkeys(webcam_sources))
            log.info(f"Opening webcam index {source} (your laptop/PC built-in camera)")
        else:
            log.info(f"Connecting to RTSP stream: {_redact_url(source)}")

        def _open_capture(src):
            backend_attempts = [('FFMPEG', lambda: cv2.VideoCapture(src, cv2.CAP_FFMPEG))] if is_rtsp else []
            if not is_rtsp:
                backend_attempts.extend([
                    ('DSHOW', lambda: cv2.VideoCapture(src, cv2.CAP_DSHOW)),
                    ('MSMF', lambda: cv2.VideoCapture(src, cv2.CAP_MSMF)),
                ])
            backend_attempts.append(('DEFAULT', lambda: cv2.VideoCapture(src)))

            for backend_name, factory in backend_attempts:
                cap_local = factory()
                try:
                    cap_local.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass

                open_timeout_prop = getattr(cv2, 'CAP_PROP_OPEN_TIMEOUT_MSEC', None)
                read_timeout_prop = getattr(cv2, 'CAP_PROP_READ_TIMEOUT_MSEC', None)
                try:
                    if open_timeout_prop is not None:
                        cap_local.set(open_timeout_prop, 2500)
                    if read_timeout_prop is not None:
                        cap_local.set(read_timeout_prop, 2500)
                except Exception:
                    pass

                if cap_local.isOpened():
                    width = int(cap_local.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                    height = int(cap_local.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                    fps = float(cap_local.get(cv2.CAP_PROP_FPS) or 0.0)
                    self._source_fps = fps
                    log.info("Opened source using backend=%s (%sx%s @ %.2f fps)", backend_name, width, height, fps)
                    return cap_local

                cap_local.release()

            return cv2.VideoCapture()

        cap = None
        active_source = source
        candidate_sources = webcam_sources if webcam_sources else rtsp_candidates
        for candidate in candidate_sources:
            active_source = candidate
            self._active_source = str(candidate)
            log.info("Trying camera source: %s", _redact_url(candidate))
            cap = _open_capture(candidate)
            if cap.isOpened():
                break
            cap.release()

        if not cap or not cap.isOpened():
            log.error(
                "Cannot open camera!\n"
                "  Webcam:    Make sure no other app is using it. Try index 1 if 0 fails.\n"
                "  IP camera: Check RTSP URL + username/password. Test in VLC first."
            )
            sys.exit(1)

        log.info("Camera open. ANPR running. Press Ctrl+C to stop.")
        if show_preview:
            log.info("Preview window open. Press 'q' inside it to quit.")

        _ml_queue: queue.Queue = queue.Queue(maxsize=1)
        _hb_queue: queue.Queue = queue.Queue(maxsize=1)

        def _ml_worker():
            while True:
                item = _ml_queue.get()
                if item is None:
                    break
                try:
                    self._process_frame(item)
                except Exception as _exc:
                    log.warning("ML worker error: %s", _exc)

        def _hb_worker():
            while True:
                item = _hb_queue.get()
                if item is None:
                    break
                try:
                    snapshot_b64, camera_source = item
                    self._post_frame_heartbeat(snapshot_b64, camera_source)
                except Exception as _exc:
                    log.warning("Heartbeat worker error: %s", _exc)

        def _stop_worker(q: queue.Queue):
            # Never block on shutdown: clear any pending item, then send the stop signal.
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

        ml_thread = threading.Thread(target=_ml_worker, daemon=True, name='anpr-ml')
        ml_thread.start()
        hb_thread = threading.Thread(target=_hb_worker, daemon=True, name='anpr-hb')
        hb_thread.start()
        log.info("Background ML + heartbeat worker threads started.")

        frame_interval = self.frame_skip
        frame_count = 0
        consecutive_read_fails = 0
        consecutive_invalid_frames = 0
        reconnect_delay = RECONNECT_DELAY_START

        try:
            while True:
                if is_rtsp and not self._recorder.active:
                    # Optional extra grabs are synchronous; keep them disabled by default
                    # so slow RTSP reads do not stall the capture loop.
                    for _ in range(self.rtsp_drain_grabs):
                        cap.grab()

                ret, frame = cap.read()
                if not ret:
                    self._read_failures += 1
                    consecutive_read_fails += 1
                    if consecutive_read_fails < MAX_CONSECUTIVE_READ_FAILS:
                        time.sleep(0.03)
                        self._maybe_log_diagnostics()
                        continue

                    log.warning("Lost camera feed after %d failed reads. Reconnecting...", consecutive_read_fails)
                    self._stop_clip('camera feed lost')
                    cap.release()
                    time.sleep(reconnect_delay)

                    reopened = False
                    for candidate in candidate_sources:
                        active_source = candidate
                        self._active_source = str(candidate)
                        log.info("Reconnecting with source: %s", _redact_url(candidate))
                        cap = _open_capture(candidate)
                        if cap.isOpened():
                            reopened = True
                            break
                        cap.release()

                    if reopened:
                        reconnect_delay = RECONNECT_DELAY_START
                    else:
                        log.warning("All camera source candidates failed; retrying in %.1fs.", reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, RECONNECT_DELAY_MAX)
                    consecutive_read_fails = 0
                    self._maybe_log_diagnostics()
                    continue

                consecutive_read_fails = 0
                self._frames_read += 1

                is_valid, frame_info = validate_decoded_frame(frame)
                if not is_valid:
                    self._invalid_frames += 1
                    consecutive_invalid_frames += 1
                    log.warning("Invalid decoded frame (%s)", frame_info)

                    if consecutive_invalid_frames >= MAX_CONSECUTIVE_INVALID_FRAMES:
                        log.warning(
                            "Too many consecutive invalid frames (%d). Reinitializing source %s",
                            consecutive_invalid_frames, _redact_url(active_source),
                        )
                        self._stop_clip('invalid frames')
                        cap.release()
                        cap = _open_capture(active_source)
                        consecutive_invalid_frames = 0

                    self._maybe_log_diagnostics()
                    continue

                consecutive_invalid_frames = 0

                if frame.ndim == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

                frame_count += 1
                now_ts = time.time()

                if frame_count % frame_interval == 0:
                    try:
                        _ml_queue.put_nowait(frame.copy())
                    except queue.Full:
                        pass  # ML still busy; skip this frame, no stall

                # Record the CLEAN frame (before any overlay is drawn on it).
                self._manage_recording(frame, now_ts)

                if (now_ts - self._last_heartbeat_post_ts) >= self.heartbeat_seconds and not _hb_queue.full():
                    self._last_heartbeat_post_ts = now_ts
                    heartbeat_b64 = self._build_snapshot_b64(frame)
                    try:
                        _hb_queue.put_nowait((heartbeat_b64, str(active_source)))
                    except queue.Full:
                        pass

                self._maybe_log_diagnostics()

                if show_preview:
                    self._draw_preview(frame)
                    cv2.imshow('BantayPlaka ANPR  [Q = quit]', frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break

        except KeyboardInterrupt:
            log.info("Stopped by user (Ctrl+C / SIGTERM).")
        finally:
            self._stop_clip('engine stopped')
            cap.release()
            _stop_worker(_ml_queue)
            _stop_worker(_hb_queue)
            ml_thread.join(timeout=5)
            hb_thread.join(timeout=3)
            self._maybe_log_diagnostics(force=True)
            if show_preview:
                cv2.destroyAllWindows()
            log.info("ANPR engine stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _sigterm_handler(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(
        description='BantayPlaka ANPR Engine (vehicle-gated recording + plate reading)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quick start (no camera hardware needed):
  python anpr_engine/anpr_engine.py --rtsp 0

With IP camera (credentials via .env ANPR_RTSP_URL, or pass --rtsp):
  python anpr_engine/anpr_engine.py --rtsp "rtsp://user:pass@192.168.1.108:554/Streaming/Channels/101" --camera-role ENTRY_CAM

TIME_IN / TIME_OUT is decided by Django unless --camera-role is ENTRY_CAM or EXIT_CAM.
        """
    )
    parser.add_argument('--rtsp', default=DEFAULT_CAMERA_SOURCE,
        help='Camera source: RTSP URL, or "0" for webcam. Default: ANPR_RTSP_URL from .env')
    parser.add_argument('--mode', choices=['roboflow', 'yolo', 'ocr'], default='roboflow',
        help='Plate localisation: roboflow (default), yolo (custom plate weights via --model), ocr (vehicle-crop OCR only)')
    parser.add_argument('--model-id', default=DEFAULT_RF_MODEL_ID,
        help=f'Roboflow model ID (project-slug/version). Default: {DEFAULT_RF_MODEL_ID}')
    parser.add_argument('--model', default=DEFAULT_PLATE_YOLO_MODEL,
        help='Custom plate-trained YOLO .pt file (for --mode yolo, or as Roboflow fallback). Not yolov8n.pt.')
    parser.add_argument('--vehicle-model', default=VEHICLE_MODEL_PATH,
        help=f'Vehicle pre-filter weights (COCO). Default: {VEHICLE_MODEL_PATH}')
    parser.add_argument('--no-vehicle-gate', action='store_true',
        help='Disable the vehicle pre-filter (also disables clip recording).')
    parser.add_argument('--no-record', action='store_true',
        help='Do not record video clips (plates are still read and sent).')
    parser.add_argument('--record-dir', default=str(RECORDINGS_DIR),
        help=f'Where clips are saved. Default: {RECORDINGS_DIR}')
    parser.add_argument('--snapshot-dir', default=str(SNAPSHOT_DIR),
        help=f'Where plate screenshots are saved. Default: {SNAPSHOT_DIR}')
    parser.add_argument('--grace', type=float, default=GRACE_PERIOD_SECONDS,
        help=f'Seconds without a vehicle before a clip stops. Default: {GRACE_PERIOD_SECONDS}')
    parser.add_argument('--cooldown', type=float, default=COOLDOWN_SECONDS,
        help=f'Seconds after a clip before a new one may start. Default: {COOLDOWN_SECONDS}')
    parser.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default=DEFAULT_ANPR_DEVICE,
        help='Runtime device for OCR/YOLO. auto=prefer CUDA when available.')
    parser.add_argument('--url', default=DEFAULT_INGEST_URL,
        help='Django ingest URL. Default: ANPR_INGEST_URL env or http://127.0.0.1:8000/detection/ingest/')
    parser.add_argument('--camera-role', choices=['ENTRY_CAM', 'EXIT_CAM', 'UNKNOWN'], default='UNKNOWN',
        help='Camera role for status mapping. ENTRY_CAM -> TIME_IN, EXIT_CAM -> TIME_OUT')
    parser.add_argument('--no-preview', action='store_true', help='Run without any GUI window.')
    parser.add_argument('--debounce', type=int, default=DEBOUNCE_SECONDS,
        help=f'Seconds before the same plate can be logged again. Default: {DEBOUNCE_SECONDS}')
    parser.add_argument('--frame-skip', type=int, default=2,
        help='Send every Nth frame to the ML worker. Lower = faster detection, higher CPU/GPU use. Default: 2')
    parser.add_argument('--rtsp-drain-grabs', type=int, default=DEFAULT_RTSP_DRAIN_GRABS,
        help=f'Buffered RTSP frames dropped before each read (not used while recording). Default: {DEFAULT_RTSP_DRAIN_GRABS}')
    parser.add_argument('--heartbeat-seconds', type=float, default=HEARTBEAT_SNAPSHOT_SECONDS,
        help=f'Seconds between live-frame heartbeat uploads. Default: {HEARTBEAT_SNAPSHOT_SECONDS}')

    args = parser.parse_args()

    if not str(args.rtsp).strip():
        parser.error("No camera source. Pass --rtsp or set ANPR_RTSP_URL in .env.")

    signal.signal(signal.SIGTERM, _sigterm_handler)

    engine = ANPREngine(
        rtsp_url=args.rtsp,
        ingest_url=args.url,
        mode=args.mode,
        camera_role=args.camera_role,
        device=args.device,
        rf_model_id=args.model_id,
        yolo_model_path=args.model,
        debounce_seconds=args.debounce,
        frame_skip=args.frame_skip,
        rtsp_drain_grabs=args.rtsp_drain_grabs,
        heartbeat_seconds=args.heartbeat_seconds,
        vehicle_gate=(VEHICLE_GATE_DEFAULT and not args.no_vehicle_gate),
        record_clips=(RECORD_CLIPS_DEFAULT and not args.no_record),
        vehicle_model_path=args.vehicle_model,
        recordings_dir=Path(args.record_dir),
        snapshot_dir=Path(args.snapshot_dir),
        grace_seconds=args.grace,
        cooldown_seconds=args.cooldown,
    )
    engine.run(show_preview=not args.no_preview)


if __name__ == '__main__':
    main()