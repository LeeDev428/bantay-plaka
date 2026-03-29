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
import re
import sys
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

# Key used to authenticate with your Django app's /detection/ingest/ endpoint
DJANGO_API_KEY = os.getenv('ANPR_API_KEY', '')

# Your Roboflow API key -- get it from: Roboflow -> Settings -> API Keys
ROBOFLOW_API_KEY = os.getenv('ROBOFLOW_API_KEY', '')

# Roboflow model ID: "project-slug/version"
# Your friend's model: workspace=kurt-4w5dv, project=plate-number-detection, version=5
DEFAULT_RF_MODEL_ID = os.getenv('ROBOFLOW_MODEL_ID', 'plate-number-detection/5')

DEFAULT_INGEST_URL = 'http://127.0.0.1:8000/detection/ingest/'
DEFAULT_YOLO_MODEL = 'yolov8n.pt'

# Seconds before the same plate can be logged again (prevents duplicates)
DEBOUNCE_SECONDS = 30

# Minimum OCR confidence to accept a plate reading (0.0 - 1.0)
MIN_OCR_CONFIDENCE = 0.35

# Full-frame fallback OCR is noisier, so keep a higher confidence bar.
FALLBACK_MIN_OCR_CONFIDENCE = 0.55

# Require short temporal agreement before posting a new plate to reduce OCR jitter.
VOTE_WINDOW_SECONDS = 2.0
MIN_VOTE_COUNT = 2
HIGH_CONF_SINGLE_SHOT = 0.88

# Detection confidence threshold for both Roboflow and YOLO modes
DETECTION_CONFIDENCE = 0.25
VALID_CAMERA_ROLES = {'ENTRY_CAM', 'EXIT_CAM', 'UNKNOWN'}

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
    'O': '0', 'Q': '0', 'D': '0',
    'I': '1', 'L': '1',
    'Z': '2',
    'S': '5',
    'G': '6',
    'T': '7',
    'B': '8',
}

_LETTER_LIKE_MAP = {
    '0': 'O',
    '1': 'I',
    '2': 'Z',
    '5': 'S',
    '6': 'G',
    '7': 'T',
    '8': 'B',
}

_PLATE_PATTERNS = (
    re.compile(r'^[A-Z]{2,4}\d{3,4}$'),
    re.compile(r'^\d{3,4}[A-Z]{2,4}$'),
)


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

    # Contrast-limited adaptive histogram equalization helps with dark/washed plates.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_clahe = clahe.apply(gray)
    variants.append(gray_clahe)

    # Binary variants often help EasyOCR with high-contrast characters.
    _, th_otsu = cv2.threshold(gray_clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(th_otsu)

    th_adaptive = cv2.adaptiveThreshold(
        gray_clahe,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        5,
    )
    variants.append(th_adaptive)

    # Slightly sharpened grayscale can recover faint marker strokes.
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    sharp = cv2.filter2D(gray_clahe, -1, kernel)
    variants.append(sharp)
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

    # Combine adjacent same-line alpha token + digit token.
    for i, left in enumerate(items):
        for j, right in enumerate(items):
            if i == j:
                continue
            if left['cx'] >= right['cx']:
                continue

            same_line = abs(left['cy'] - right['cy']) <= max(left['h'], right['h']) * 0.8
            if not same_line:
                continue

            alpha_ok = left['raw'].isalpha() and 2 <= len(left['raw']) <= 4
            digit_ok = right['raw'].isdigit() and 3 <= len(right['raw']) <= 4
            if not (alpha_ok and digit_ok):
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
        log.info("Roboflow model ready. Accuracy: 98.8% mAP on license plates.")

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
            results = self._model.infer(frame, confidence=DETECTION_CONFIDENCE)
            boxes = parse_predictions(results, scale_back=1.0)

            # For CCTV feeds where plates are tiny in the full frame, retry on upscaled image.
            if not boxes:
                h, w = frame.shape[:2]
                max_dim = max(h, w)
                if max_dim < 1600:
                    scale = min(2.0, 1600.0 / max(1.0, float(max_dim)))
                    upscaled = cv2.resize(
                        frame,
                        (int(w * scale), int(h * scale)),
                        interpolation=cv2.INTER_CUBIC,
                    )
                    results_up = self._model.infer(upscaled, confidence=DETECTION_CONFIDENCE)
                    boxes = parse_predictions(results_up, scale_back=1.0 / scale)
        except Exception as e:
            log.warning(f"Roboflow detection error: {e}")
        return boxes


class YOLODetector:
    """
    Detects plates using a local YOLO .pt weights file.
    Less accurate than the Roboflow model but works without an API key.
    Use this only as a fallback (--mode yolo).
    """

    def __init__(self, model_path: str):
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
        log.info("YOLO model loaded.")

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        boxes = []
        try:
            results = self._model(frame, conf=DETECTION_CONFIDENCE, verbose=False)
            for result in results:
                if result.boxes is None:
                    continue
                for box in result.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    boxes.append((x1, y1, x2, y2))
        except Exception as e:
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
        rf_model_id: str = DEFAULT_RF_MODEL_ID,
        yolo_model_path: str = DEFAULT_YOLO_MODEL,
        debounce_seconds: int = DEBOUNCE_SECONDS,
        frame_skip: int = 2,
    ):
        self.rtsp_url = rtsp_url
        self.ingest_url = ingest_url
        requested_role = (camera_role or 'UNKNOWN').strip().upper()
        self.camera_role = requested_role if requested_role in VALID_CAMERA_ROLES else 'UNKNOWN'
        self.debounce_seconds = debounce_seconds
        self.frame_skip = max(1, int(frame_skip))
        self._last_logged: dict[str, float] = {}
        self._vote_history: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self._frames_no_box = 0
        self._processed_frames = 0

        # Initialize plate detector
        if mode == 'roboflow':
            self.detector = RoboflowDetector(rf_model_id, ROBOFLOW_API_KEY)
        elif mode == 'yolo':
            self.detector = YOLODetector(yolo_model_path)
        else:
            log.error(f"Unknown mode '{mode}'. Use 'roboflow' or 'yolo'.")
            sys.exit(1)

        # EasyOCR reads the text from the cropped plate image
        use_gpu = bool(torch and torch.cuda.is_available())
        if use_gpu:
            log.info("CUDA detected. EasyOCR GPU mode enabled.")
        else:
            log.info("CUDA not available. EasyOCR CPU mode enabled.")
        log.info("Loading EasyOCR (first run downloads ~200 MB, then cached locally)...")
        self.ocr = easyocr.Reader(['en'], gpu=use_gpu)
        log.info("EasyOCR ready.")

    def _is_debounced(self, plate: str) -> bool:
        return (time.time() - self._last_logged.get(plate, 0)) < self.debounce_seconds

    def _record_logged(self, plate: str):
        self._last_logged[plate] = time.time()

    def _has_vote_consensus(self, plate: str, confidence: float, min_conf: float) -> bool:
        now = time.time()
        votes = self._vote_history[plate]
        votes.append((now, float(confidence)))
        votes[:] = [(ts, conf) for (ts, conf) in votes if (now - ts) <= VOTE_WINDOW_SECONDS]

        if confidence >= HIGH_CONF_SINGLE_SHOT:
            return True
        if len(votes) < MIN_VOTE_COUNT:
            return False

        avg_conf = sum(conf for _, conf in votes) / len(votes)
        return avg_conf >= min_conf

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

    def _process_frame(self, frame: np.ndarray):
        """Detect plates in this frame, read text, post to Django if valid."""
        self._processed_frames += 1
        h, w = frame.shape[:2]
        pad = 10

        boxes = self.detector.detect(frame)
        if boxes:
            self._frames_no_box = 0
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
            for candidate in build_ocr_variants(plate_crop):
                if posted:
                    break
                ocr_results = self.ocr.readtext(
                    candidate,
                    allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ',
                    detail=1,
                    paragraph=False,
                )
                for (plate, confidence, _) in extract_plate_candidates_from_ocr(ocr_results):
                    plate = normalize_plate_variant_noise(plate)
                    if plate in frame_seen:
                        continue
                    frame_seen.add(plate)

                    if not is_strict_plate(plate):
                        continue
                    if confidence < MIN_OCR_CONFIDENCE:
                        continue
                    if not self._has_vote_consensus(plate, confidence, MIN_OCR_CONFIDENCE):
                        continue

                    log.info(f"Plate: '{plate}' (OCR conf: {confidence:.2f})")

                    if self._is_debounced(plate):
                        log.info(f"Skipping '{plate}' -- debounced ({self.debounce_seconds}s).")
                        posted = True
                        break

                    # Draw box + plate text on preview window
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, plate, (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                    snapshot_b64 = self._build_snapshot_b64(frame)

                    if self._post_to_django(plate, snapshot_b64=snapshot_b64):
                        self._record_logged(plate)
                    posted = True
                    break

        # Fallback: if detector found no box, run OCR on whole frame every few processed frames.
        # This keeps CPU manageable while recovering from weak detector outputs.
        if not boxes and self._processed_frames % 3 == 0:
            frame_variants = build_ocr_variants(frame)
            frame_seen: set[str] = set()
            for candidate in frame_variants:
                ocr_results = self.ocr.readtext(
                    candidate,
                    allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ',
                    detail=1,
                    paragraph=False,
                )
                for (plate, confidence, bbox_xyxy) in extract_plate_candidates_from_ocr(ocr_results):
                    plate = normalize_plate_variant_noise(plate)
                    if plate in frame_seen:
                        continue
                    frame_seen.add(plate)

                    if not is_strict_plate(plate):
                        continue
                    if confidence < FALLBACK_MIN_OCR_CONFIDENCE:
                        continue
                    if not self._has_vote_consensus(plate, confidence, FALLBACK_MIN_OCR_CONFIDENCE):
                        continue

                    if self._is_debounced(plate):
                        log.info("Fallback OCR plate '%s' skipped (debounced).", plate)
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
                    return

    def run(self, show_preview: bool = True):
        """Open the camera and run ANPR loop until stopped."""
        source: str | int = self.rtsp_url
        is_rtsp = isinstance(source, str) and '://' in source

        if is_rtsp:
            os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp|max_delay;500000|stimeout;5000000'
            source = _normalize_rtsp_url(str(source))
            rtsp_candidates = _rtsp_candidates(str(source))
        else:
            rtsp_candidates = [source]

        if str(source).isdigit():
            source = int(source)
            log.info(f"Opening webcam index {source} (your laptop/PC built-in camera)")
        else:
            log.info(f"Connecting to RTSP stream: {source}")

        def _open_capture(src):
            cap_local = cv2.VideoCapture(src, cv2.CAP_FFMPEG) if is_rtsp else cv2.VideoCapture(src)
            cap_local.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            try:
                cap_local.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                cap_local.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
            except Exception:
                pass
            return cap_local

        cap = None
        active_source = source
        for candidate in rtsp_candidates:
            active_source = candidate
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

        frame_interval = self.frame_skip
        frame_count = 0

        try:
            while True:
                if is_rtsp:
                    # Drop stale buffered frames to keep OCR close to real-time.
                    for _ in range(2):
                        cap.grab()

                ret, frame = cap.read()
                if not ret:
                    log.warning("Lost camera feed. Retrying in 5 seconds...")
                    time.sleep(5)
                    cap.release()

                    reopened = False
                    for candidate in rtsp_candidates:
                        active_source = candidate
                        log.info("Reconnecting with source: %s", candidate)
                        cap = _open_capture(candidate)
                        if cap.isOpened():
                            reopened = True
                            break
                        cap.release()

                    if not reopened:
                        log.warning("All camera source candidates failed; keeping retry loop active.")
                    continue

                frame_count += 1
                if frame_count % frame_interval == 0:
                    self._process_frame(frame)

                if show_preview:
                    cv2.imshow('BantayPlaka ANPR  [Q = quit]', frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break

        except KeyboardInterrupt:
            log.info("Stopped by user (Ctrl+C).")
        finally:
            cap.release()
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
    parser.add_argument('--url', default=DEFAULT_INGEST_URL,
        help=f'Django ingest URL. Default: {DEFAULT_INGEST_URL}')
    parser.add_argument('--camera-role', choices=['ENTRY_CAM', 'EXIT_CAM', 'UNKNOWN'], default='UNKNOWN',
        help='Camera role for status mapping. ENTRY_CAM -> TIME_IN, EXIT_CAM -> TIME_OUT')
    parser.add_argument('--no-preview', action='store_true',
        help='Run without any GUI window.')
    parser.add_argument('--debounce', type=int, default=DEBOUNCE_SECONDS,
        help=f'Seconds before same plate can be logged again. Default: {DEBOUNCE_SECONDS}')
    parser.add_argument('--frame-skip', type=int, default=2,
        help='Process every Nth frame. Lower is faster detection but higher CPU/GPU usage. Default: 2')

    args = parser.parse_args()

    engine = ANPREngine(
        rtsp_url=args.rtsp,
        ingest_url=args.url,
        mode=args.mode,
        camera_role=args.camera_role,
        rf_model_id=args.model_id,
        yolo_model_path=args.model,
        debounce_seconds=args.debounce,
        frame_skip=args.frame_skip,
    )
    engine.run(show_preview=not args.no_preview)


if __name__ == '__main__':
    main()
