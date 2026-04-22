#!/usr/bin/env python
"""
BantayPlaka ANPR Engine
=======================
Reads frames from a real IP camera (or webcam) via RTSP/OpenCV,
detects license plates, reads the plate text with EasyOCR, then
POSTs the result to the running Django application.

TWO DETECTION MODES:
  1. roboflow  (DEFAULT, RECOMMENDED)
     Uses your friend's Roboflow "Plate Number Detection" v5 model
     (98.8% mAP accuracy). Requires ROBOFLOW_API_KEY in .env.
     On first run it downloads and caches the model locally.
     After that it runs 100% offline -- no internet needed.

  2. yolo
     Uses a local YOLO .pt weights file. Less accurate but works
     without a Roboflow account. Good as a fallback.

REQUIREMENTS:
  pip install -r anpr_engine/anpr_requirements.txt

USAGE EXAMPLES:

  # Recommended -- Roboflow mode with webcam test (no camera hardware needed yet):
  python anpr_engine/anpr_engine.py --rtsp 0

  # Roboflow mode with real IP camera:
  python anpr_engine/anpr_engine.py --rtsp "rtsp://admin:admin@192.168.1.108:554/stream1"

  # YOLO fallback mode:
  python anpr_engine/anpr_engine.py --rtsp 0 --mode yolo

  # Headless (no GUI window, background service):
  python anpr_engine/anpr_engine.py --rtsp "rtsp://..." --no-preview

NOTE: TIME_IN / TIME_OUT is auto-determined by Django.
      First scan = TIME_IN, second scan = TIME_OUT, and so on.
    To enforce strict per-camera status, pass --camera-role ENTRY_CAM or EXIT_CAM.
"""

import argparse
import base64
from collections import defaultdict
import logging
import os
import queue
import re
import sys
import threading
import time
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
# Load environment variables from Django project's .env
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

# We only use plate detection; disable optional Inference model families to avoid noisy warnings.
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

    cuda_ready = bool(torch and torch.cuda.is_available() and torch.version.cuda)

    if choice == 'cpu':
        return 'cpu'

    if choice == 'cuda':
        if cuda_ready:
            return 'cuda:0'
        log.warning("CUDA was explicitly requested but is unavailable. Falling back to CPU.")
        return 'cpu'

    # auto
    return 'cuda:0' if cuda_ready else 'cpu'

# Key used to authenticate with your Django app's /detection/ingest/ endpoint
DJANGO_API_KEY = os.getenv('ANPR_API_KEY', '')

# Your Roboflow API key -- get it from: Roboflow -> Settings -> API Keys
ROBOFLOW_API_KEY = os.getenv('ROBOFLOW_API_KEY', '')

# Roboflow model ID: "project-slug/version"
# Your friend's model: workspace=kurt-4w5dv, project=plate-number-detection, version=5
DEFAULT_RF_MODEL_ID = os.getenv('ROBOFLOW_MODEL_ID', 'plate-number-detection/5')

DEFAULT_INGEST_URL = (os.getenv('ANPR_INGEST_URL', '') or '').strip() or 'http://127.0.0.1:8000/detection/ingest/'
DEFAULT_YOLO_MODEL = 'yolov8n.pt'

# Seconds before the same plate can be logged again (prevents duplicates)
DEBOUNCE_SECONDS = 4

# Minimum OCR confidence to accept a plate reading (0.0 - 1.0)
MIN_OCR_CONFIDENCE = _env_float('ANPR_MIN_OCR_CONFIDENCE', 0.36)

# Detector-path OCR still needs short temporal agreement to reduce one-frame misreads.
DETECTOR_MIN_VOTE_CONFIDENCE = _env_float('ANPR_DETECTOR_VOTE_CONFIDENCE', 0.50)

# Full-frame fallback OCR is noisier, so keep a higher confidence bar.
FALLBACK_MIN_OCR_CONFIDENCE = _env_float('ANPR_FALLBACK_MIN_OCR_CONFIDENCE', 0.58)

# Require short temporal agreement before posting a new plate to reduce OCR jitter.
VOTE_WINDOW_SECONDS = 1.4
MIN_VOTE_COUNT = 2
HIGH_CONF_SINGLE_SHOT = 0.80
DETECTOR_QUICK_ACCEPT_CONFIDENCE = _env_float('ANPR_DETECTOR_QUICK_ACCEPT_CONFIDENCE', 0.66)
FALLBACK_QUICK_ACCEPT_CONFIDENCE = _env_float('ANPR_FALLBACK_QUICK_ACCEPT_CONFIDENCE', 0.86)
FALLBACK_EVERY_N_FRAMES = max(1, _env_int('ANPR_FALLBACK_EVERY_N_FRAMES', 6))
HEARTBEAT_SNAPSHOT_SECONDS = max(2, _env_int('ANPR_HEARTBEAT_SECONDS', 5))

# Emergency demo profile for RTSP camera presentations.
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

# Detection confidence threshold for both Roboflow and YOLO modes
DETECTION_CONFIDENCE = _env_float('ANPR_DETECTION_CONFIDENCE', 0.34)
DEFAULT_ANPR_DEVICE = (os.getenv('ANPR_DEVICE', 'auto') or 'auto').strip().lower()
VALID_CAMERA_ROLES = {'ENTRY_CAM', 'EXIT_CAM', 'UNKNOWN'}

# Runtime diagnostics + RTSP resilience tuning.
DIAGNOSTIC_INTERVAL_SECONDS = 5.0
MAX_CONSECUTIVE_READ_FAILS = 20
MAX_CONSECUTIVE_INVALID_FRAMES = 12
MIN_VALID_FRAME_WIDTH = 160
MIN_VALID_FRAME_HEIGHT = 120

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('bantayplaka.anpr')

_DIGIT_LIKE_MAP = {
    'O': '0', 'Q': '0',
    'I': '1', 'L': '1',
    'B': '8',
}

_LETTER_LIKE_MAP = {
    '0': 'O',
    '1': 'I',
    '8': 'B',
}

_PLATE_PATTERNS = (
    re.compile(r'^[A-Z]{2,4}\d{3,4}$'),
    re.compile(r'^\d{3,4}[A-Z]{2,4}$'),
)

ALLOWED_PLATE_FORMAT = (os.getenv('ANPR_ALLOWED_PLATE_FORMAT', 'PH_3X3') or 'PH_3X3').strip().upper()
FALLBACK_MIN_NO_BOX_STREAK = max(1, _env_int('ANPR_FALLBACK_MIN_NO_BOX_STREAK', 10))


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
    """Generate robust candidate URLs so engine can recover if one channel/path fails."""
    source = _normalize_rtsp_url((rtsp_url or '').strip())
    if not source or '://' not in source:
        return [source]

    candidates: list[str] = [source]
    # Hikvision/HiLook main/sub channel fallback.
    if '/Streaming/Channels/101' in source:
        candidates.append(source.replace('/Streaming/Channels/101', '/Streaming/Channels/102'))
    elif '/Streaming/Channels/102' in source:
        candidates.append(source.replace('/Streaming/Channels/102', '/Streaming/Channels/101'))

    # Generic fallback paths for some brands.
    generic_paths = ('/stream1', '/live', '/h264')
    parsed = urlparse(source)
    for path in generic_paths:
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
    if normalized.endswith('/ingest'):
        return normalized[:-6] + '/ingest-frame'
    if normalized.endswith('/ingest/'):
        return normalized[:-7] + '/ingest-frame/'
    return normalized


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
    """Fast contour fallback for demo: find plate-like rectangles in lower frame area."""
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

        cy = y + (h / 2.0)
        if cy < fh * 0.35:
            # Skip top overlay/timestamp region.
            continue

        boxes.append((x, y, x + w, y + h))

    # Keep biggest plausible rectangles first.
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

    # Try all bounded slices to remove one noisy prefix/suffix OCR character.
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
    # Demo profile: accept only 3x3 formats to suppress random text hits.
    return bool(re.fullmatch(r'(?:[A-Z]{3} \d{3}|\d{3} [A-Z]{3})', plate))


def is_allowed_plate_format(plate: str) -> bool:
    """Apply runtime-selectable plate format filter to suppress non-plate text."""
    if ALLOWED_PLATE_FORMAT == 'PH_STRICT':
        return is_strict_plate(plate)
    if ALLOWED_PLATE_FORMAT == 'PH_3X3':
        return is_demo_strict_plate(plate)
    # PH_RELAXED: backwards-compatible strict mode.
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

    # letters+digits format
    if left.isalpha() and right.isdigit() and len(left) == 4 and left[0] in confusable_prefixes:
        return f'{left[1:]} {right}'

    # digits+letters format
    if left.isdigit() and right.isalpha() and len(right) == 4 and right[0] in confusable_prefixes:
        return f'{left} {right[1:]}'

    return plate


def build_ocr_variants(plate_crop: np.ndarray) -> list[np.ndarray]:
    """Create multiple image variants to improve OCR hit rate under blur/lighting noise."""
    variants: list[np.ndarray] = [plate_crop]

    # Upscale small crops to help OCR read distant/thin characters.
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

    # Keep OCR variants lightweight to reduce per-frame latency.
    _, th_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(th_otsu)
    return variants


def build_fast_fullframe_ocr_variants(frame: np.ndarray) -> list[np.ndarray]:
    """Low-cost full-frame OCR variants to keep RTSP processing responsive."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    variants: list[np.ndarray] = [gray]

    # Fast contrast bump for difficult lighting without expensive multi-pass filters.
    normalized = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
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

    # Direct token candidates first.
    for item in items:
        add_candidate(item['raw'], item['conf'], item['bbox'])

    # Combine adjacent same-line split tokens.
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
# Plate Detectors
# ---------------------------------------------------------------------------

class RoboflowDetector:
    """
    Detects license plates using the Roboflow-trained model.
    Accuracy: 98.8% mAP (Plate Number Detection v5).

    First run: downloads and caches the model locally (~30 seconds, needs internet).
    After that: runs completely offline, no internet needed.
    """

    def __init__(self, model_id: str, api_key: str):
        try:
            from inference import get_model
        except ImportError:
            log.error(
                "The 'inference' package is not installed.\n"
                "  Run:  pip install inference"
            )
            sys.exit(1)

        if not api_key:
            log.error(
                "ROBOFLOW_API_KEY is not set in your .env file.\n"
                "  1. Go to Roboflow -> click your profile -> Settings -> API Keys\n"
                "  2. Copy the API key\n"
                "  3. Add to .env:  ROBOFLOW_API_KEY=paste_your_key_here"
            )
            sys.exit(1)

        log.info(f"Loading Roboflow model: {model_id}")
        log.info("First run downloads and caches the model (~30 sec). Next runs are instant.")
        self._model = get_model(model_id=model_id, api_key=api_key)
        self._infer_calls = 0
        self._zero_box_calls = 0
        self._last_variant = 'none'
        self._no_box_streak = 0
        log.info("Roboflow model ready. Accuracy: 98.8% mAP on license plates.")

    def _infer_variant(
        self,
        image: np.ndarray,
        scale_back: float,
        parse_predictions,
        variant_label: str,
    ) -> list[tuple[int, int, int, int]]:
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

                if None in (x, y, w, h):
                    continue

                x1 = int((x - w / 2) * scale_back)
                y1 = int((y - h / 2) * scale_back)
                x2 = int((x + w / 2) * scale_back)
                y2 = int((y + h / 2) * scale_back)
                parsed.append((x1, y1, x2, y2))
            return parsed

        try:
            boxes = self._infer_variant(frame, 1.0, parse_predictions, 'bgr')

            # Some RTSP decoders produce color layouts that behave better after explicit BGR->RGB conversion.
            if not boxes and frame.ndim == 3 and frame.shape[2] == 3:
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                boxes = self._infer_variant(rgb_frame, 1.0, parse_predictions, 'rgb')

            # For CCTV feeds where plates are tiny in the full frame, retry on upscaled image.
            # Run this on no-box streaks to keep throughput stable while still probing tiny plates.
            if not boxes:
                self._no_box_streak += 1
                h, w = frame.shape[:2]
                max_dim = max(h, w)
                should_try_upscale = self._no_box_streak % 3 == 0
                if should_try_upscale and max_dim < 1600:
                    scale = min(2.0, 1600.0 / max(1.0, float(max_dim)))
                    upscaled = cv2.resize(
                        frame,
                        (int(w * scale), int(h * scale)),
                        interpolation=cv2.INTER_CUBIC,
                    )
                    boxes = self._infer_variant(upscaled, 1.0 / scale, parse_predictions, 'upscaled-bgr')

                    if not boxes and upscaled.ndim == 3 and upscaled.shape[2] == 3:
                        upscaled_rgb = cv2.cvtColor(upscaled, cv2.COLOR_BGR2RGB)
                        boxes = self._infer_variant(
                            upscaled_rgb,
                            1.0 / scale,
                            parse_predictions,
                            'upscaled-rgb',
                        )
            else:
                self._no_box_streak = 0

            if not boxes:
                self._zero_box_calls += 1
        except Exception as e:
            log.warning(f"Roboflow detection error: {e}")
        return boxes


class YOLODetector:
    """
    Detects plates using a local YOLO .pt weights file.
    Less accurate than the Roboflow model but works without an API key.
    Use this only as a fallback (--mode yolo).
    """

    def __init__(self, model_path: str, device: str = 'cpu'):
        try:
            from ultralytics import YOLO
        except ImportError:
            log.error("The 'ultralytics' package is not installed. Run: pip install ultralytics")
            sys.exit(1)

        if model_path == DEFAULT_YOLO_MODEL:
            log.warning(
                "Using generic yolov8n.pt -- NOT trained on license plates.\n"
                "  This model will have poor plate detection accuracy.\n"
                "  Use --mode roboflow for the proper trained model."
            )
        elif not os.path.exists(model_path):
            log.error(f"YOLO model file not found: {model_path}")
            sys.exit(1)

        log.info(f"Loading YOLO model: {model_path}")
        self._model = YOLO(model_path)
        self._device = device if device in {'cpu', 'cuda:0'} else 'cpu'
        self._infer_calls = 0
        if self._device.startswith('cuda'):
            try:
                self._model.to(self._device)
                log.info("YOLO device set to %s", self._device)
            except Exception as exc:
                log.warning("YOLO CUDA init failed (%s). Falling back to CPU.", exc)
                self._device = 'cpu'
        log.info("YOLO model loaded.")

    def get_debug_stats(self) -> dict[str, object]:
        return {
            'infer_calls': self._infer_calls,
            'zero_box_calls': None,
            'no_box_streak': None,
            'last_variant': 'yolo',
            'device': self._device,
        }

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        boxes = []
        try:
            self._infer_calls += 1
            results = self._model(frame, conf=DETECTION_CONFIDENCE, verbose=False, device=self._device)
            for result in results:
                if result.boxes is None:
                    continue
                for box in result.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    boxes.append((x1, y1, x2, y2))
        except Exception as e:
            if self._device.startswith('cuda'):
                log.warning("YOLO CUDA inference failed (%s). Retrying on CPU.", e)
                self._device = 'cpu'
                try:
                    results = self._model(frame, conf=DETECTION_CONFIDENCE, verbose=False, device='cpu')
                    for result in results:
                        if result.boxes is None:
                            continue
                        for box in result.boxes:
                            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                            boxes.append((x1, y1, x2, y2))
                except Exception as cpu_exc:
                    log.warning(f"YOLO detection error: {cpu_exc}")
            else:
                log.warning(f"YOLO detection error: {e}")
        return boxes


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
        yolo_model_path: str = DEFAULT_YOLO_MODEL,
        debounce_seconds: int = DEBOUNCE_SECONDS,
        frame_skip: int = 2,
        rtsp_drain_grabs: int = 2,
        heartbeat_seconds: int = HEARTBEAT_SNAPSHOT_SECONDS,
    ):
        self.rtsp_url = rtsp_url
        self.ingest_url = ingest_url
        self.ingest_frame_url = _derive_frame_ingest_url(ingest_url)
        self._is_rtsp_source = isinstance(rtsp_url, str) and '://' in rtsp_url
        requested_role = (camera_role or 'UNKNOWN').strip().upper()
        self.camera_role = requested_role if requested_role in VALID_CAMERA_ROLES else 'UNKNOWN'
        self.runtime_device = _resolve_runtime_device(device)
        self.debounce_seconds = debounce_seconds
        self.frame_skip = max(1, int(frame_skip))
        self.rtsp_drain_grabs = max(0, int(rtsp_drain_grabs))
        self.heartbeat_seconds = max(2, int(heartbeat_seconds))
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
                DETECTOR_QUICK_ACCEPT_CONFIDENCE,
                DEMO_DETECTOR_QUICK_ACCEPT_CONFIDENCE,
            )
            self.fallback_quick_accept_confidence = min(
                FALLBACK_QUICK_ACCEPT_CONFIDENCE,
                DEMO_FALLBACK_QUICK_ACCEPT_CONFIDENCE,
            )
            self.fallback_every_n_frames = max(1, DEMO_FALLBACK_EVERY_N_FRAMES)

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
        self._last_heartbeat_post_ts = 0.0
        self._active_source = str(rtsp_url)
        self._last_diag_ts = time.time()

        # Initialize plate detector. On restricted Windows clients, Roboflow model
        # package loading can fail due to symlink privileges; fallback keeps webcam
        # preview and pipeline running.
        if mode == 'roboflow':
            try:
                self.detector = RoboflowDetector(rf_model_id, ROBOFLOW_API_KEY)
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    raise
                log.error("Roboflow detector failed to initialize: %s", exc)
                log.warning("Falling back to YOLO detector for compatibility.")
                try:
                    self.detector = YOLODetector(yolo_model_path, device=self.runtime_device)
                except BaseException as yolo_exc:
                    log.error("YOLO fallback failed to initialize: %s", yolo_exc)
                    sys.exit(1)
        elif mode == 'yolo':
            self.detector = YOLODetector(yolo_model_path, device=self.runtime_device)
        else:
            log.error(f"Unknown mode '{mode}'. Use 'roboflow' or 'yolo'.")
            sys.exit(1)

        # EasyOCR reads the text from the cropped plate image
        use_gpu = self.runtime_device.startswith('cuda')
        if use_gpu:
            log.info("Runtime device: %s. EasyOCR GPU mode enabled.", self.runtime_device)
        else:
            log.info("Runtime device: CPU. EasyOCR CPU mode enabled.")
        log.info("Loading EasyOCR (first run downloads ~200 MB, then cached locally)...")
        self.ocr = easyocr.Reader(['en'], gpu=use_gpu)
        log.info("EasyOCR ready.")
        log.info(
            "Thresholds: detector=%.2f ocr=%.2f fallback_ocr=%.2f vote=%.2f",
            DETECTION_CONFIDENCE,
            self.min_ocr_confidence,
            self.fallback_min_ocr_confidence,
            self.detector_vote_confidence,
        )
        if self.demo_mode:
            log.warning(
                "DEMO RTSP MODE ENABLED: aggressive full-frame OCR and relaxed vote thresholds are active."
            )

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
            "[DIAG] role=%s source=%s frames_read=%d processed=%d read_fail=%d invalid=%d "
            "detector_frames=%d detector_box_frames=%d detector_boxes=%d ocr_candidates=%d "
            "accepted_plates=%d dropped_confidence=%d dropped_debounce=%d rf_calls=%s rf_zero_box=%s rf_variant=%s",
            self.camera_role,
            self._active_source,
            self._frames_read,
            self._processed_frames,
            self._read_failures,
            self._invalid_frames,
            self._detector_frames,
            self._detector_box_frames,
            self._detector_box_count,
            self._ocr_candidates,
            self._accepted_plates,
            self._dropped_by_confidence,
            self._dropped_by_debounce,
            detector_debug.get('infer_calls', '?'),
            detector_debug.get('zero_box_calls', '?'),
            detector_debug.get('last_variant', '?'),
        )
        self._last_diag_ts = now

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

    def _build_snapshot_b64(self, frame: np.ndarray) -> str:
        snapshot_b64 = ''
        try:
            preview_for_upload = frame
            max_width = 960
            if frame.shape[1] > max_width:
                scale = max_width / frame.shape[1]
                preview_for_upload = cv2.resize(
                    frame,
                    (max_width, int(frame.shape[0] * scale)),
                    interpolation=cv2.INTER_AREA,
                )
            ok, encoded = cv2.imencode(
                '.jpg',
                preview_for_upload,
                [int(cv2.IMWRITE_JPEG_QUALITY), 75],
            )
            if ok:
                snapshot_b64 = base64.b64encode(encoded.tobytes()).decode('ascii')
        except Exception:
            snapshot_b64 = ''
        return snapshot_b64

    def _post_to_django(self, plate: str, snapshot_b64: str = '') -> bool:
        """POST the detected plate to Django. Returns True on success."""
        if not DJANGO_API_KEY:
            log.error("ANPR_API_KEY is not set in .env -- cannot send plate to Django.")
            return False
        try:
            resp = requests.post(
                self.ingest_url,
                json={
                    'plate_number': plate,
                    'camera_role': self.camera_role,
                    'snapshot_b64': snapshot_b64,
                },
                headers={'Content-Type': 'application/json', 'X-Api-Key': DJANGO_API_KEY},
                timeout=5,
            )
            if resp.status_code == 200:
                result = resp.json()
                assigned_status = result.get('status', '?')
                log.info(
                    f"[LOGGED] '{plate}' ({self.camera_role}) -> {assigned_status} "
                    f"(Log ID {result.get('log_id')})"
                )
                return True
            else:
                log.error(f"[REJECTED] Django returned {resp.status_code}: {resp.text}")
                return False
        except requests.exceptions.ConnectionError:
            log.error("Cannot reach Django at %s -- is Daphne running?", self.ingest_url)
        except requests.exceptions.Timeout:
            log.error("Django request timed out.")
        except Exception as e:
            log.error("Unexpected error posting to Django: %s", e)
        return False

    def _post_frame_heartbeat(self, snapshot_b64: str) -> bool:
        if not snapshot_b64 or not DJANGO_API_KEY:
            return False
        if not self.ingest_frame_url:
            return False

        try:
            resp = requests.post(
                self.ingest_frame_url,
                json={
                    'camera_role': self.camera_role,
                    'snapshot_b64': snapshot_b64,
                },
                headers={'Content-Type': 'application/json', 'X-Api-Key': DJANGO_API_KEY},
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _process_frame(self, frame: np.ndarray):
        """Detect plates in this frame, read text, post to Django if valid."""
        self._processed_frames += 1
        self._detector_frames += 1
        h, w = frame.shape[:2]
        pad = 10

        if self.demo_mode and DEMO_SKIP_RF_DETECTOR:
            # Fast path for demo: skip expensive model inference and use contour boxes directly.
            boxes = detect_plate_like_rectangles(frame)
        else:
            boxes = self.detector.detect(frame)
            if self.demo_mode and not boxes:
                # Emergency demo fallback when model misses plates on noisy RTSP frames.
                boxes = detect_plate_like_rectangles(frame)
        if boxes:
            self._frames_no_box = 0
            self._detector_box_frames += 1
            self._detector_box_count += len(boxes)
            for (bx1, by1, bx2, by2) in boxes:
                # Draw raw detector output so operator can see detector activity.
                cv2.rectangle(frame, (max(0, bx1), max(0, by1)), (min(w, bx2), min(h, by2)), (0, 215, 255), 1)
        else:
            self._frames_no_box += 1
            if self._frames_no_box % 30 == 0:
                log.info("No plate boxes detected in last %s processed frames.", self._frames_no_box)

        for (x1, y1, x2, y2) in boxes:
            # Expand bounding box slightly for better OCR
            x1 = max(0, x1 - pad)
            y1 = max(0, y1 - pad)
            x2 = min(w, x2 + pad)
            y2 = min(h, y2 + pad)

            plate_crop = frame[y1:y2, x1:x2]
            if plate_crop.size == 0:
                continue

            posted = False
            frame_seen: set[str] = set()
            best_conf_by_plate: dict[str, float] = {}
            if self.demo_mode:
                gray_crop = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY)
                _, th_crop = cv2.threshold(gray_crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                ocr_variants = [gray_crop, th_crop]
            else:
                ocr_variants = build_ocr_variants(plate_crop)

            for candidate in ocr_variants:
                if posted:
                    break
                ocr_results = self.ocr.readtext(
                    candidate,
                    allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ',
                    detail=1,
                    paragraph=False,
                )
                for (plate, confidence, _) in extract_plate_candidates_from_ocr(ocr_results):
                    self._ocr_candidates += 1
                    plate = normalize_plate_variant_noise(plate)
                    if plate in frame_seen:
                        continue
                    frame_seen.add(plate)

                    if not is_allowed_plate_format(plate):
                        continue
                    if confidence < self.min_ocr_confidence:
                        self._dropped_by_confidence += 1
                        continue

                    previous = best_conf_by_plate.get(plate)
                    if previous is None or confidence > previous:
                        best_conf_by_plate[plate] = confidence

            ranked_candidates = sorted(
                best_conf_by_plate.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            for plate, confidence in ranked_candidates:
                if not self._passes_consensus(
                    plate,
                    confidence,
                    self.detector_vote_confidence,
                    self.detector_quick_accept_confidence,
                ):
                    continue

                log.info(f"Plate: '{plate}' (OCR conf: {confidence:.2f})")

                if self._is_debounced(plate):
                    log.info(f"Skipping '{plate}' -- debounced ({self.debounce_seconds}s).")
                    self._dropped_by_debounce += 1
                    continue

                # Draw box + plate text on preview window
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(frame, plate, (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                snapshot_b64 = self._build_snapshot_b64(frame)

                if self._post_to_django(plate, snapshot_b64=snapshot_b64):
                    self._record_logged(plate)
                    self._accepted_plates += 1
                posted = True
                break

        # Fallback: if detector found no box, run OCR on whole frame every few processed frames.
        # This keeps CPU manageable while recovering from weak detector outputs.
        fallback_every = 1 if (self.demo_mode and DEMO_FORCE_FULLFRAME_OCR) else self.fallback_every_n_frames
        if (
            not boxes
            and self._frames_no_box >= FALLBACK_MIN_NO_BOX_STREAK
            and self._processed_frames % fallback_every == 0
        ):
            frame_variants: list[tuple[np.ndarray, int, int]] = []

            # Demo mode: keep fallback OCR very lightweight to avoid CPU stalls.
            if self.demo_mode:
                fh, fw = frame.shape[:2]
                roi_specs = [
                    # Primary area where a held plate is expected during demo.
                    (0.22, 0.52, 0.82, 0.93),
                ]
                for x1r, y1r, x2r, y2r in roi_specs:
                    rx1 = max(0, min(fw - 1, int(fw * x1r)))
                    ry1 = max(0, min(fh - 1, int(fh * y1r)))
                    rx2 = max(rx1 + 1, min(fw, int(fw * x2r)))
                    ry2 = max(ry1 + 1, min(fh, int(fh * y2r)))
                    roi = frame[ry1:ry2, rx1:rx2]
                    if roi.size == 0:
                        continue
                    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                    frame_variants.append((gray, rx1, ry1))
                    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    frame_variants.append((th, rx1, ry1))

                # Scan most of the frame while excluding the top timestamp overlay strip.
                safe_top = int(fh * 0.22)
                if safe_top < fh - 10:
                    safe_frame = frame[safe_top:, :]
                    safe_gray = cv2.cvtColor(safe_frame, cv2.COLOR_BGR2GRAY)
                    frame_variants.append((safe_gray, 0, safe_top))
                    _, safe_th = cv2.threshold(safe_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    frame_variants.append((safe_th, 0, safe_top))

            if not (self.demo_mode and DEMO_FOCUS_ROI_ONLY):
                for variant in build_fast_fullframe_ocr_variants(frame):
                    frame_variants.append((variant, 0, 0))

            frame_seen: set[str] = set()
            for candidate, offset_x, offset_y in frame_variants:
                ocr_results = self.ocr.readtext(
                    candidate,
                    allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ',
                    detail=1,
                    paragraph=False,
                )
                for (plate, confidence, bbox_xyxy) in extract_plate_candidates_from_ocr(ocr_results):
                    if bbox_xyxy:
                        x1b, y1b, x2b, y2b = bbox_xyxy
                        bbox_xyxy = (x1b + offset_x, y1b + offset_y, x2b + offset_x, y2b + offset_y)

                    self._ocr_candidates += 1
                    plate = normalize_plate_variant_noise(plate)
                    if plate in frame_seen:
                        continue
                    frame_seen.add(plate)

                    if not is_allowed_plate_format(plate):
                        continue
                    if not is_plausible_plate_bbox(bbox_xyxy):
                        continue
                    if confidence < self.fallback_min_ocr_confidence:
                        self._dropped_by_confidence += 1
                        continue
                    if not self._passes_consensus(
                        plate,
                        confidence,
                        self.fallback_min_ocr_confidence,
                        self.fallback_quick_accept_confidence,
                    ):
                        continue

                    if self._is_debounced(plate):
                        log.info("Fallback OCR plate '%s' skipped (debounced).", plate)
                        self._dropped_by_debounce += 1
                        return

                    log.info("Fallback OCR hit: '%s' (conf: %.2f)", plate, confidence)

                    # Draw OCR-based bounding polygon so fallback detections still show visual evidence.
                    try:
                        if bbox_xyxy:
                            x1, y1, x2, y2 = bbox_xyxy
                            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                            x, y = x1, y1
                        else:
                            x, y = 20, 40
                        cv2.putText(
                            frame,
                            plate,
                            (int(x), max(20, int(y) - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.8,
                            (0, 255, 0),
                            2,
                        )
                    except Exception:
                        pass

                    snapshot_b64 = self._build_snapshot_b64(frame)
                    if self._post_to_django(plate, snapshot_b64=snapshot_b64):
                        self._record_logged(plate)
                        self._accepted_plates += 1
                    return

    def run(self, show_preview: bool = True):
        """Open the camera and run ANPR loop until stopped."""
        source: str | int = self.rtsp_url
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
            # preserve order while removing duplicates
            webcam_sources = list(dict.fromkeys(webcam_sources))
            log.info(f"Opening webcam index {source} (your laptop/PC built-in camera)")
        else:
            log.info(f"Connecting to RTSP stream: {source}")

        def _open_capture(src):
            backend_attempts = [
                ('FFMPEG', lambda: cv2.VideoCapture(src, cv2.CAP_FFMPEG)),
            ] if is_rtsp else []
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
                    log.info(
                        "Opened source using backend=%s (%sx%s @ %.2f fps)",
                        backend_name,
                        width,
                        height,
                        fps,
                    )
                    return cap_local

                cap_local.release()

            return cv2.VideoCapture()

        cap = None
        active_source = source
        candidate_sources = webcam_sources if webcam_sources else rtsp_candidates
        for candidate in candidate_sources:
            active_source = candidate
            self._active_source = str(candidate)
            log.info("Trying camera source: %s", candidate)
            cap = _open_capture(candidate)
            if cap.isOpened():
                break
            cap.release()

        if not cap or not cap.isOpened():
            log.error(
                "Cannot open camera!\n"
                "  Webcam:    Make sure no other app is using it. Try index 1 if 0 fails.\n"
                "  IP camera: Check RTSP URL + username/password. Test in VLC first.\n"
                "             Media -> Open Network Stream -> paste RTSP URL -> Play"
            )
            sys.exit(1)

        log.info("Camera open. ANPR running. Press Ctrl+C to stop.")
        if show_preview:
            log.info("Preview window open. Press 'q' inside it to quit.")

        # --- Background worker threads so ML inference never stalls the read loop ---
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
                    self._post_frame_heartbeat(item)
                except Exception as _exc:
                    log.warning("Heartbeat worker error: %s", _exc)

        ml_thread = threading.Thread(target=_ml_worker, daemon=True, name='anpr-ml')
        ml_thread.start()
        hb_thread = threading.Thread(target=_hb_worker, daemon=True, name='anpr-hb')
        hb_thread.start()
        log.info("Background ML + heartbeat worker threads started.")

        frame_interval = self.frame_skip
        frame_count = 0
        consecutive_read_fails = 0
        consecutive_invalid_frames = 0

        try:
            while True:
                if is_rtsp:
                    # Drop stale buffered frames to keep OCR close to real-time.
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

                    log.warning(
                        "Lost camera feed after %d failed reads. Reconnecting...",
                        consecutive_read_fails,
                    )
                    cap.release()
                    time.sleep(0.8)

                    reopened = False
                    for candidate in candidate_sources:
                        active_source = candidate
                        self._active_source = str(candidate)
                        log.info("Reconnecting with source: %s", candidate)
                        cap = _open_capture(candidate)
                        if cap.isOpened():
                            reopened = True
                            break
                        cap.release()

                    if not reopened:
                        log.warning("All camera source candidates failed; keeping retry loop active.")
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
                            consecutive_invalid_frames,
                            active_source,
                        )
                        cap.release()
                        cap = _open_capture(active_source)
                        consecutive_invalid_frames = 0

                    self._maybe_log_diagnostics()
                    continue

                consecutive_invalid_frames = 0

                if frame.ndim == 2:
                    # Some decoders can return grayscale frames; normalize to BGR for detector/OCR.
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

                frame_count += 1
                if frame_count % frame_interval == 0:
                    # Non-blocking: drop frame if ML worker is still busy with previous one.
                    try:
                        _ml_queue.put_nowait(frame.copy())
                    except queue.Full:
                        pass  # ML is still busy; skip this frame — no stall

                now_ts = time.time()
                if (now_ts - self._last_heartbeat_post_ts) >= self.heartbeat_seconds:
                    heartbeat_b64 = self._build_snapshot_b64(frame)
                    try:
                        _hb_queue.put_nowait(heartbeat_b64)
                        self._last_heartbeat_post_ts = now_ts
                    except queue.Full:
                        pass  # heartbeat upload in flight; skip this tick

                self._maybe_log_diagnostics()

                if show_preview:
                    cv2.imshow('BantayPlaka ANPR  [Q = quit]', frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break

        except KeyboardInterrupt:
            log.info("Stopped by user (Ctrl+C).")
        finally:
            cap.release()
            self._maybe_log_diagnostics(force=True)
            if show_preview:
                cv2.destroyAllWindows()
            log.info("ANPR engine stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='BantayPlaka ANPR Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quick start (no camera hardware needed):
  python anpr_engine/anpr_engine.py --rtsp 0

With IP camera:
  python anpr_engine/anpr_engine.py --rtsp "rtsp://admin:admin@192.168.1.108:554/stream1"

TIME_IN / TIME_OUT is auto-determined by Django (alternates per plate).
        """
    )
    parser.add_argument('--rtsp', required=True,
        help='Camera source: RTSP URL for IP cameras, or "0" for webcam.')
    parser.add_argument('--mode', choices=['roboflow', 'yolo'], default='roboflow',
        help='Detection mode. Default: roboflow (recommended, 98.8%% accuracy)')
    parser.add_argument('--model-id', default=DEFAULT_RF_MODEL_ID,
        help=f'Roboflow model ID (project-slug/version). Default: {DEFAULT_RF_MODEL_ID}')
    parser.add_argument('--model', default=DEFAULT_YOLO_MODEL,
        help='YOLO .pt file path (only for --mode yolo). Default: yolov8n.pt')
    parser.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default=DEFAULT_ANPR_DEVICE,
        help='Runtime device selection for OCR/YOLO. auto=prefer CUDA when available. Default: ANPR_DEVICE env or auto')
    parser.add_argument('--url', default=DEFAULT_INGEST_URL,
        help='Django ingest URL. Default: ANPR_INGEST_URL env or http://127.0.0.1:8000/detection/ingest/')
    parser.add_argument('--camera-role', choices=['ENTRY_CAM', 'EXIT_CAM', 'UNKNOWN'], default='UNKNOWN',
        help='Camera role for status mapping. ENTRY_CAM -> TIME_IN, EXIT_CAM -> TIME_OUT')
    parser.add_argument('--no-preview', action='store_true',
        help='Run without any GUI window.')
    parser.add_argument('--debounce', type=int, default=DEBOUNCE_SECONDS,
        help=f'Seconds before same plate can be logged again. Default: {DEBOUNCE_SECONDS}')
    parser.add_argument('--frame-skip', type=int, default=2,
        help='Process every Nth frame. Lower is faster detection but higher CPU/GPU usage. Default: 2')
    parser.add_argument('--rtsp-drain-grabs', type=int, default=3,
        help='How many buffered RTSP frames to grab/drop before each read. Higher lowers latency but can reduce decode stability. Default: 3')
    parser.add_argument('--heartbeat-seconds', type=int, default=HEARTBEAT_SNAPSHOT_SECONDS,
        help=f'Seconds between live frame heartbeat uploads for dashboard feed fallback. Default: {HEARTBEAT_SNAPSHOT_SECONDS}')

    args = parser.parse_args()

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
    )
    engine.run(show_preview=not args.no_preview)


if __name__ == '__main__':
    main()
