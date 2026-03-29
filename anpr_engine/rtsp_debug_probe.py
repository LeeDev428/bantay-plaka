#!/usr/bin/env python
"""
RTSP + Roboflow debug probe for BantayPlaka.

Use this script when webcam detection works but RTSP camera detection does not.
It isolates:
- RTSP decode stability (OpenCV read failures / reconnect behavior)
- Roboflow model inference activity (calls, latency, zero-box frames)
- Color-space fallback (BGR and RGB)
- Optional upscaled inference for tiny plates
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import cv2
import numpy as np
from dotenv import load_dotenv


def normalize_rtsp_url(rtsp_url: str) -> str:
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


def rtsp_candidates(rtsp_url: str) -> list[str]:
    source = normalize_rtsp_url((rtsp_url or '').strip())
    if not source:
        return []

    candidates = [source]
    if '/Streaming/Channels/101' in source:
        candidates.append(source.replace('/Streaming/Channels/101', '/Streaming/Channels/102'))
    elif '/Streaming/Channels/102' in source:
        candidates.append(source.replace('/Streaming/Channels/102', '/Streaming/Channels/101'))

    out: list[str] = []
    seen = set()
    for value in candidates:
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def open_capture(source: str):
    attempts = [
        ('FFMPEG', lambda: cv2.VideoCapture(source, cv2.CAP_FFMPEG)),
        ('DEFAULT', lambda: cv2.VideoCapture(source)),
    ]

    for backend_name, factory in attempts:
        cap = factory()
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        open_timeout_prop = getattr(cv2, 'CAP_PROP_OPEN_TIMEOUT_MSEC', None)
        read_timeout_prop = getattr(cv2, 'CAP_PROP_READ_TIMEOUT_MSEC', None)
        try:
            if open_timeout_prop is not None:
                cap.set(open_timeout_prop, 8000)
            if read_timeout_prop is not None:
                cap.set(read_timeout_prop, 8000)
        except Exception:
            pass

        if cap.isOpened():
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            print(f"[CAPTURE] opened backend={backend_name} size={width}x{height} fps={fps:.2f}")
            return cap

        cap.release()

    return cv2.VideoCapture()


def parse_predictions(results_obj, scale_back: float = 1.0) -> list[tuple[int, int, int, int]]:
    boxes: list[tuple[int, int, int, int]] = []
    if not results_obj:
        return boxes

    first = results_obj[0]
    if hasattr(first, 'predictions'):
        predictions = first.predictions or []
    elif isinstance(first, dict):
        predictions = first.get('predictions') or first.get('objects') or []
    else:
        predictions = []

    for prediction in predictions:
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
        boxes.append((x1, y1, x2, y2))

    return boxes


def infer_with_variants(model, frame: np.ndarray, confidence: float) -> tuple[list[tuple[int, int, int, int]], str, float]:
    attempts: list[tuple[str, np.ndarray, float]] = [('bgr', frame, 1.0)]
    if frame.ndim == 3 and frame.shape[2] == 3:
        attempts.append(('rgb', cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), 1.0))

    h, w = frame.shape[:2]
    max_dim = max(h, w)
    if max_dim < 1600:
        scale = min(2.0, 1600.0 / max(1.0, float(max_dim)))
        upscaled = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        attempts.append(('upscaled-bgr', upscaled, 1.0 / scale))
        if upscaled.ndim == 3 and upscaled.shape[2] == 3:
            attempts.append(('upscaled-rgb', cv2.cvtColor(upscaled, cv2.COLOR_BGR2RGB), 1.0 / scale))

    total_latency_ms = 0.0
    for label, image, scale_back in attempts:
        t0 = time.perf_counter()
        results = model.infer(image, confidence=confidence)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        total_latency_ms += elapsed_ms

        boxes = parse_predictions(results, scale_back=scale_back)
        if boxes:
            return boxes, label, total_latency_ms

    return [], 'none', total_latency_ms


def main():
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')

    parser = argparse.ArgumentParser(description='RTSP + Roboflow debug probe')
    parser.add_argument('--rtsp', required=True, help='RTSP URL to test')
    parser.add_argument('--model-id', default=os.getenv('ROBOFLOW_MODEL_ID', 'plate-number-detection/5'))
    parser.add_argument('--api-key', default=os.getenv('ROBOFLOW_API_KEY', ''))
    parser.add_argument('--confidence', type=float, default=0.25)
    parser.add_argument('--process-every', type=int, default=1, help='Run model every Nth frame')
    parser.add_argument('--drain-grabs', type=int, default=1, help='Drop N buffered frames before read')
    parser.add_argument('--diag-interval', type=float, default=5.0)
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit('ROBOFLOW_API_KEY is missing. Set it in .env or pass --api-key')

    try:
        from inference import get_model
    except Exception as exc:
        raise SystemExit(f'inference package import failed: {exc}')

    model = get_model(model_id=args.model_id, api_key=args.api_key)
    print(f"[MODEL] loaded model_id={args.model_id}")

    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
        'rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|'
        'max_delay;500000|stimeout;7000000|reorder_queue_size;0'
    )

    candidates = rtsp_candidates(args.rtsp)
    if not candidates:
        raise SystemExit('No RTSP candidates generated from input URL')

    cap = None
    active_source = candidates[0]
    for candidate in candidates:
        active_source = candidate
        print(f"[CAPTURE] trying source={candidate}")
        cap = open_capture(candidate)
        if cap.isOpened():
            break
        cap.release()

    if not cap or not cap.isOpened():
        raise SystemExit('Could not open RTSP stream with any candidate URL')

    frames_read = 0
    read_fail = 0
    invalid_frames = 0
    infer_calls = 0
    frames_with_boxes = 0
    total_boxes = 0
    last_variant = 'none'
    last_infer_ms = 0.0
    frame_idx = 0
    last_diag_ts = time.time()

    print('[RUN] started. Press q in preview to exit.')

    while True:
        for _ in range(max(0, int(args.drain_grabs))):
            cap.grab()

        ok, frame = cap.read()
        if not ok:
            read_fail += 1
            if read_fail % 20 == 0:
                print(f"[READ] fail streak={read_fail}, reconnecting source={active_source}")
                cap.release()
                cap = open_capture(active_source)
                if not cap.isOpened():
                    time.sleep(0.8)
            continue

        read_fail = 0
        frames_read += 1
        frame_idx += 1

        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            invalid_frames += 1
            continue

        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        if frame_idx % max(1, int(args.process_every)) == 0:
            infer_calls += 1
            boxes, last_variant, last_infer_ms = infer_with_variants(model, frame, args.confidence)
            if boxes:
                frames_with_boxes += 1
                total_boxes += len(boxes)
                for (x1, y1, x2, y2) in boxes:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, 'plate', (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        now = time.time()
        if (now - last_diag_ts) >= args.diag_interval:
            print(
                f"[DIAG] source={active_source} frames_read={frames_read} read_fail={read_fail} "
                f"invalid={invalid_frames} infer_calls={infer_calls} box_frames={frames_with_boxes} "
                f"boxes={total_boxes} last_variant={last_variant} last_infer_ms={last_infer_ms:.1f}"
            )
            last_diag_ts = now

        cv2.imshow('RTSP Debug Probe [Q=quit]', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
