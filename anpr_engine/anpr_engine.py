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
  python anpr_engine/anpr_engine.py --rtsp 0 --status TIME_IN

  # Roboflow mode with real IP camera (entry gate):
  python anpr_engine/anpr_engine.py --rtsp "rtsp://admin:admin@192.168.1.108:554/stream1" --status TIME_IN

  # Exit gate camera (TIME_OUT):
  python anpr_engine/anpr_engine.py --rtsp "rtsp://admin:admin@192.168.1.109:554/stream1" --status TIME_OUT

  # YOLO fallback mode:
  python anpr_engine/anpr_engine.py --rtsp 0 --status TIME_IN --mode yolo

  # Headless (no GUI window, background service):
  python anpr_engine/anpr_engine.py --rtsp "rtsp://..." --status TIME_IN --no-preview
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import cv2
import easyocr
import numpy as np
import requests
from dotenv import load_dotenv

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
MIN_OCR_CONFIDENCE = 0.3

# Detection confidence threshold for both Roboflow and YOLO modes
DETECTION_CONFIDENCE = 0.4

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('bantayplaka.anpr')

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
    cleaned = ''.join(c for c in raw_text.upper() if c.isalnum() or c == ' ')
    cleaned = ' '.join(cleaned.split())

    if len(cleaned) < 5:
        return None

    # Insert space if missing (e.g. "ABC1234" -> "ABC 1234")
    if ' ' not in cleaned:
        letters = ''.join(c for c in cleaned if c.isalpha())
        digits = ''.join(c for c in cleaned if c.isdigit())
        if letters and digits:
            cleaned = f'{letters} {digits}'
        else:
            return None

    return cleaned


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
        boxes = []
        try:
            results = self._model.infer(frame, confidence=DETECTION_CONFIDENCE)
            if not results:
                return boxes
            for prediction in results[0].predictions:
                # Roboflow uses center x, y, width, height -> convert to corners
                x1 = int(prediction.x - prediction.width / 2)
                y1 = int(prediction.y - prediction.height / 2)
                x2 = int(prediction.x + prediction.width / 2)
                y2 = int(prediction.y + prediction.height / 2)
                boxes.append((x1, y1, x2, y2))
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
        status: str,
        ingest_url: str,
        mode: str = 'roboflow',
        rf_model_id: str = DEFAULT_RF_MODEL_ID,
        yolo_model_path: str = DEFAULT_YOLO_MODEL,
    ):
        self.rtsp_url = rtsp_url
        self.status = status
        self.ingest_url = ingest_url
        self._last_logged: dict[str, float] = {}

        # Initialize plate detector
        if mode == 'roboflow':
            self.detector = RoboflowDetector(rf_model_id, ROBOFLOW_API_KEY)
        elif mode == 'yolo':
            self.detector = YOLODetector(yolo_model_path)
        else:
            log.error(f"Unknown mode '{mode}'. Use 'roboflow' or 'yolo'.")
            sys.exit(1)

        # EasyOCR reads the text from the cropped plate image
        log.info("Loading EasyOCR (first run downloads ~200 MB, then cached locally)...")
        self.ocr = easyocr.Reader(['en'], gpu=False)
        log.info("EasyOCR ready.")

    def _is_debounced(self, plate: str) -> bool:
        return (time.time() - self._last_logged.get(plate, 0)) < DEBOUNCE_SECONDS

    def _record_logged(self, plate: str):
        self._last_logged[plate] = time.time()

    def _post_to_django(self, plate: str) -> bool:
        """POST the detected plate to Django. Returns True on success."""
        if not DJANGO_API_KEY:
            log.error("ANPR_API_KEY is not set in .env -- cannot send plate to Django.")
            return False
        try:
            resp = requests.post(
                self.ingest_url,
                json={'plate_number': plate, 'status': self.status},
                headers={'Content-Type': 'application/json', 'X-Api-Key': DJANGO_API_KEY},
                timeout=5,
            )
            if resp.status_code == 200:
                log.info(f"[LOGGED] '{plate}' -> Log ID {resp.json().get('log_id')}")
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
        h, w = frame.shape[:2]
        pad = 10

        for (x1, y1, x2, y2) in self.detector.detect(frame):
            # Expand bounding box slightly for better OCR
            x1 = max(0, x1 - pad)
            y1 = max(0, y1 - pad)
            x2 = min(w, x2 + pad)
            y2 = min(h, y2 + pad)

            plate_crop = frame[y1:y2, x1:x2]
            if plate_crop.size == 0:
                continue

            for (_, text, confidence) in self.ocr.readtext(plate_crop):
                if confidence < MIN_OCR_CONFIDENCE:
                    continue

                plate = clean_plate_text(text)
                if plate is None:
                    log.debug(f"OCR '{text}' -- invalid format, skipping.")
                    continue

                log.info(f"Plate: '{plate}' (OCR conf: {confidence:.2f})")

                if self._is_debounced(plate):
                    log.info(f"Skipping '{plate}' -- debounced ({DEBOUNCE_SECONDS}s).")
                    continue

                # Draw box + plate text on preview window
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(frame, plate, (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                if self._post_to_django(plate):
                    self._record_logged(plate)

    def run(self, show_preview: bool = True):
        """Open the camera and run ANPR loop until stopped."""
        source: str | int = self.rtsp_url
        if str(source).isdigit():
            source = int(source)
            log.info(f"Opening webcam index {source} (your laptop/PC built-in camera)")
        else:
            log.info(f"Connecting to RTSP stream: {source}")

        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
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

        frame_interval = 5  # Process every 5th frame (CPU efficiency)
        frame_count = 0

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    log.warning("Lost camera feed. Retrying in 5 seconds...")
                    time.sleep(5)
                    cap.release()
                    cap = cv2.VideoCapture(source)
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
  python anpr_engine/anpr_engine.py --rtsp 0 --status TIME_IN

With IP camera:
  python anpr_engine/anpr_engine.py --rtsp "rtsp://admin:admin@192.168.1.108:554/stream1" --status TIME_IN
        """
    )
    parser.add_argument('--rtsp', required=True,
        help='Camera source: RTSP URL for IP cameras, or "0" for webcam.')
    parser.add_argument('--status', choices=['TIME_IN', 'TIME_OUT'], default='TIME_IN',
        help='Entry (TIME_IN) or exit (TIME_OUT) gate. Default: TIME_IN')
    parser.add_argument('--mode', choices=['roboflow', 'yolo'], default='roboflow',
        help='Detection mode. Default: roboflow (recommended, 98.8%% accuracy)')
    parser.add_argument('--model-id', default=DEFAULT_RF_MODEL_ID,
        help=f'Roboflow model ID (project-slug/version). Default: {DEFAULT_RF_MODEL_ID}')
    parser.add_argument('--model', default=DEFAULT_YOLO_MODEL,
        help='YOLO .pt file path (only for --mode yolo). Default: yolov8n.pt')
    parser.add_argument('--url', default=DEFAULT_INGEST_URL,
        help=f'Django ingest URL. Default: {DEFAULT_INGEST_URL}')
    parser.add_argument('--no-preview', action='store_true',
        help='Run without any GUI window.')
    parser.add_argument('--debounce', type=int, default=DEBOUNCE_SECONDS,
        help=f'Seconds before same plate can be logged again. Default: {DEBOUNCE_SECONDS}')

    args = parser.parse_args()

    global DEBOUNCE_SECONDS
    DEBOUNCE_SECONDS = args.debounce

    engine = ANPREngine(
        rtsp_url=args.rtsp,
        status=args.status,
        ingest_url=args.url,
        mode=args.mode,
        rf_model_id=args.model_id,
        yolo_model_path=args.model,
    )
    engine.run(show_preview=not args.no_preview)


if __name__ == '__main__':
    main()
