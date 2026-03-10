#!/usr/bin/env python
"""
BantayPlaka ANPR Engine
=======================
Reads frames from a real IP camera via RTSP, detects license plates using
YOLOv8 + EasyOCR, then POSTs the result to the running Django application.

How it connects to your Django app:
  - Your Django app runs at http://127.0.0.1:8000 (Daphne local server)
  - This script calls POST http://127.0.0.1:8000/detection/ingest/
  - The Django app saves the log, broadcasts it via WebSocket to the guard dashboard

Dependencies (install via: pip install -r anpr_requirements.txt):
  - opencv-python
  - ultralytics       (YOLOv8 — plate detection)
  - easyocr           (plate text reading)
  - requests          (HTTP POST to Django)
  - python-dotenv     (reads .env for API key)
  - numpy

Usage examples:
  # Entry gate camera:
  python anpr_engine.py --rtsp "rtsp://admin:admin@192.168.1.100:554/stream1" --status TIME_IN

  # Exit gate camera:
  python anpr_engine.py --rtsp "rtsp://admin:admin@192.168.1.101:554/stream1" --status TIME_OUT

  # With custom Django URL (e.g. different port):
  python anpr_engine.py --rtsp "rtsp://..." --status TIME_IN --url http://127.0.0.1:8000/detection/ingest/

  # Test with your PC webcam (no IP camera yet):
  python anpr_engine.py --rtsp 0 --status TIME_IN
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
from ultralytics import YOLO

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Load .env from the parent folder (your Django project root)
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

API_KEY = os.getenv('ANPR_API_KEY', '')
DEFAULT_INGEST_URL = 'http://127.0.0.1:8000/detection/ingest/'

# How many seconds to wait before logging the SAME plate again.
# Prevents a single vehicle from creating 100 log entries as it sits in front of the camera.
DEBOUNCE_SECONDS = 30

# Minimum confidence for EasyOCR to accept a reading (0.0 - 1.0)
MIN_OCR_CONFIDENCE = 0.3

# YOLOv8 confidence threshold for plate detection
YOLO_CONFIDENCE = 0.4

# ---------------------------------------------------------------------------
# Logging
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

# Characters that look similar and get misread by OCR
OCR_CHAR_MAP = {
    'O': '0',  # letter O → digit 0 (depends on plate format — disable if wrong)
    'I': '1',  # letter I → digit 1
    'S': '5',  # letter S → digit 5
}

# Philippine license plate formats:
#   Old format: ABC 1234  (3 letters + space + 4 digits)
#   New format: ABC 1234  (same)
#   Motorcycles: AB 1234  (2 letters + space + 4 digits)
# We keep it flexible — just clean obvious noise.


def clean_plate_text(raw_text: str) -> str | None:
    """
    Clean and normalize raw OCR output into a usable plate number.
    Returns None if the text looks invalid.
    """
    # Remove everything except letters, digits, and spaces
    cleaned = ''.join(c for c in raw_text.upper() if c.isalnum() or c == ' ')
    cleaned = cleaned.strip()

    # Must be at least 5 characters (shortest valid plate: "AB 123")
    if len(cleaned) < 5:
        return None

    # Insert a space if there's none (e.g. "ABC1234" → "ABC 1234")
    if ' ' not in cleaned:
        # Try to split at the boundary between letters and digits
        letter_part = ''.join(c for c in cleaned if c.isalpha())
        digit_part = ''.join(c for c in cleaned if c.isdigit())
        if letter_part and digit_part:
            cleaned = f"{letter_part} {digit_part}"
        else:
            return None  # Can't split — probably garbage

    return cleaned


# ---------------------------------------------------------------------------
# ANPR Engine class
# ---------------------------------------------------------------------------

class ANPREngine:
    def __init__(self, rtsp_url: str, status: str, ingest_url: str):
        self.rtsp_url = rtsp_url
        self.status = status
        self.ingest_url = ingest_url

        # Tracks last time each plate was logged: { plate_number: timestamp }
        self._last_logged: dict[str, float] = {}

        log.info("Loading YOLOv8 model for license plate detection...")
        # Use the nano model for speed on the guard house PC.
        # On first run, it auto-downloads (~6 MB).
        # If you have a custom-trained Philippine plate model, replace 'yolov8n.pt'
        # with the path to your .pt file.
        self.yolo = YOLO('yolov8n.pt')
        log.info("YOLOv8 loaded.")

        log.info("Loading EasyOCR (this may take 1-2 minutes on first run, downloads ~200 MB)...")
        # 'en' = English alphabet (covers Philippine plates fine)
        # gpu=False is safe default; set gpu=True if your PC has a CUDA GPU
        self.reader = easyocr.Reader(['en'], gpu=False)
        log.info("EasyOCR loaded.")

    def _is_debounced(self, plate: str) -> bool:
        """Return True if this plate was logged too recently (skip duplicate)."""
        last_time = self._last_logged.get(plate, 0)
        return (time.time() - last_time) < DEBOUNCE_SECONDS

    def _record_logged(self, plate: str):
        self._last_logged[plate] = time.time()

    def _post_to_django(self, plate: str) -> bool:
        """
        POST the detected plate number to the Django app.
        Returns True on success.
        """
        if not API_KEY:
            log.error("ANPR_API_KEY is not set in .env! Cannot send to Django.")
            return False

        payload = {
            'plate_number': plate,
            'status': self.status,
        }
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': API_KEY,
        }

        try:
            resp = requests.post(
                self.ingest_url,
                json=payload,
                headers=headers,
                timeout=5,  # 5 second timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                log.info(f"[OK] Plate '{plate}' logged. Log ID: {data.get('log_id')}")
                return True
            else:
                log.error(f"[FAIL] Django returned {resp.status_code}: {resp.text}")
                return False
        except requests.exceptions.ConnectionError:
            log.error("Cannot connect to Django. Is Daphne running at %s?", self.ingest_url)
            return False
        except requests.exceptions.Timeout:
            log.error("Request to Django timed out.")
            return False
        except Exception as e:
            log.error("Unexpected error posting to Django: %s", e)
            return False

    def _process_frame(self, frame: np.ndarray):
        """
        Run YOLO plate detection + EasyOCR on a single frame.
        If a valid plate is found and not debounced, post it to Django.
        """
        results = self.yolo(frame, conf=YOLO_CONFIDENCE, verbose=False)

        for result in results:
            if result.boxes is None:
                continue

            for box in result.boxes:
                # Get bounding box coordinates
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

                # Add small padding around the detected plate
                pad = 10
                h, w = frame.shape[:2]
                x1 = max(0, x1 - pad)
                y1 = max(0, y1 - pad)
                x2 = min(w, x2 + pad)
                y2 = min(h, y2 + pad)

                # Crop the plate region
                plate_crop = frame[y1:y2, x1:x2]
                if plate_crop.size == 0:
                    continue

                # Run OCR on the cropped plate
                ocr_results = self.reader.readtext(plate_crop)
                for (bbox, text, confidence) in ocr_results:
                    if confidence < MIN_OCR_CONFIDENCE:
                        continue

                    plate = clean_plate_text(text)
                    if plate is None:
                        log.debug(f"OCR read '{text}' but cleaned text was invalid — skipping.")
                        continue

                    log.info(f"Plate detected: '{plate}' (confidence: {confidence:.2f})")

                    if self._is_debounced(plate):
                        log.info(f"'{plate}' was recently logged — skipping (debounce {DEBOUNCE_SECONDS}s).")
                        continue

                    # Draw on frame for visual feedback (shown in the preview window)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(
                        frame, plate,
                        (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 255, 0), 2
                    )

                    # Post to Django
                    success = self._post_to_django(plate)
                    if success:
                        self._record_logged(plate)

    def run(self, show_preview: bool = True):
        """
        Main loop: open RTSP stream, read frames, run ANPR on each frame.
        Press 'q' in the preview window to stop.
        """
        # If rtsp_url is a digit string, treat as webcam index
        source = self.rtsp_url
        if str(source).isdigit():
            source = int(source)
            log.info(f"Using webcam index {source}")
        else:
            log.info(f"Connecting to RTSP stream: {source}")

        cap = cv2.VideoCapture(source)

        if not cap.isOpened():
            log.error(
                "Cannot open camera stream.\n"
                "  - For IP cameras: check RTSP URL, username, password, and that the camera is reachable.\n"
                "  - Try pinging the camera IP: ping 192.168.1.100\n"
                "  - Try opening the RTSP URL in VLC: Media > Open Network Stream"
            )
            sys.exit(1)

        log.info("Camera stream opened. Starting ANPR loop. Press Ctrl+C or 'q' to stop.")

        # Process every Nth frame to reduce CPU load.
        # 5 = process 1 out of every 5 frames (good balance of speed vs accuracy)
        frame_interval = 5
        frame_count = 0

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    log.warning("Failed to read frame. Camera disconnected? Retrying in 5s...")
                    time.sleep(5)
                    cap.release()
                    cap = cv2.VideoCapture(source)
                    continue

                frame_count += 1
                if frame_count % frame_interval != 0:
                    # Still show preview even for skipped frames
                    if show_preview:
                        cv2.imshow('BantayPlaka ANPR — Press Q to quit', frame)
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            break
                    continue

                # Process this frame
                self._process_frame(frame)

                if show_preview:
                    cv2.imshow('BantayPlaka ANPR — Press Q to quit', frame)
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
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='BantayPlaka ANPR Engine — reads RTSP camera, detects plates, posts to Django'
    )
    parser.add_argument(
        '--rtsp',
        required=True,
        help='RTSP URL of the IP camera, e.g. rtsp://admin:admin@192.168.1.100:554/stream1  '
             'OR a webcam index like 0 for testing with your laptop camera.',
    )
    parser.add_argument(
        '--status',
        choices=['TIME_IN', 'TIME_OUT'],
        default='TIME_IN',
        help='Whether this camera handles entry (TIME_IN) or exit (TIME_OUT). Default: TIME_IN',
    )
    parser.add_argument(
        '--url',
        default=DEFAULT_INGEST_URL,
        help=f'URL of the Django ingest endpoint. Default: {DEFAULT_INGEST_URL}',
    )
    parser.add_argument(
        '--no-preview',
        action='store_true',
        help='Run headless (no OpenCV GUI window). Use this on servers without a display.',
    )
    parser.add_argument(
        '--debounce',
        type=int,
        default=DEBOUNCE_SECONDS,
        help=f'Seconds to wait before logging the same plate again. Default: {DEBOUNCE_SECONDS}',
    )

    args = parser.parse_args()

    global DEBOUNCE_SECONDS
    DEBOUNCE_SECONDS = args.debounce

    engine = ANPREngine(
        rtsp_url=args.rtsp,
        status=args.status,
        ingest_url=args.url,
    )
    engine.run(show_preview=not args.no_preview)


if __name__ == '__main__':
    main()
