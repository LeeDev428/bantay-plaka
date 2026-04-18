# BantayPlaka Rebuild Blueprint (Comprehensive)

This document is a full restart package for rebuilding the project in a new repository.
It contains:
1. The current system intent and functional scope.
2. Hardware you already own and what is still missing.
3. Real-world failure points discovered in this project.
4. A clean target architecture and implementation plan.
5. A full detailed copy-paste prompt for Copilot to generate a new production-ready project.

If you are done with this repo and want a cleaner reset, use this README as the source of truth.

---

## 1. Project Goal

Build a web-based gate monitoring platform that does the following:
1. Detect vehicle license plates from two IP cameras (ENTRY and EXIT).
2. Log vehicle movement in real time (TIME_IN, TIME_OUT) with snapshots.
3. Distinguish Resident and Visitor records.
4. Allow manual fallback entry when camera or OCR fails.
5. Broadcast live updates to guard dashboard through WebSocket.
6. Keep role-based access for Admin and Guard users.

---

## 2. Hardware You Already Have

### Already purchased and available
1. 2x Hikvision HiWatch IP cameras.
2. 1x TP-Link TL-SG1008D (8-port switch, non-PoE).
3. 3x Cat6 LAN cables.
4. 1x Computer/Laptop for backend + ANPR engine.

### Important hardware constraint
Your switch is non-PoE, so cameras still need power.

### What is still needed for robust deployment
Choose one:
1. Preferred: 2x PoE injectors (one per camera), plus short patch cables.
2. Alternative: 2x correct 12V camera power adapters if your camera model supports direct DC input.

---

## 3. Current Camera Network Plan

Recommended static IP assignment:
1. ENTRY camera: 192.168.1.100
2. EXIT camera: 192.168.1.101
3. Guard PC: 192.168.1.50
4. Subnet mask: 255.255.255.0

Default RTSP style used in this project:
1. rtsp://admin:YOUR_PASSWORD@192.168.1.100:554/Streaming/Channels/101
2. rtsp://admin:YOUR_PASSWORD@192.168.1.101:554/Streaming/Channels/101

---

## 4. Why This Repo Became Unstable (Root Causes)

These were the major blockers observed during debugging:
1. Process-state mismatch:
- Camera feed endpoint can still return frames while ANPR worker is dead.
- This makes UI look alive while detection is not running.

2. Environment mismatch:
- Multiple Python environments caused startup ambiguity.
- Dependencies installed in one env but runtime used another env.

3. RTSP stream jitter and decode errors:
- H264 packet loss and unstable stream config lead to dropped/garbled frames.
- This hurts OCR consistency.

4. OCR normalization and confidence tuning issues:
- Noisy OCR variants can produce wrong logs if filtering is too permissive.
- Too strict filtering can produce no logs when plate quality drops.

5. Camera scene geometry:
- If plate is too small, angled, motion-blurred, or backlit, detector misses plates.

---

## 5. New Project Requirements (Hard Requirements)

### Functional
1. Two-camera ANPR ingestion (ENTRY, EXIT).
2. Role-based dashboard (Admin, Guard).
3. Live log table via WebSocket.
4. Manual entry fallback.
5. Blacklist support and visible alerting.
6. Snapshot storage for detections.

### Non-functional
1. Deterministic startup with one command.
2. Clear health state for each worker (LIVE/STALE/STOPPED).
3. No hidden silent failure.
4. Strong logging with diagnostics counters.
5. Simple deployment flow with minimal manual steps.

---

## 6. Target System Flow

```mermaid
flowchart LR
    A[ENTRY IP Camera] --> B[ANPR Worker ENTRY]
    C[EXIT IP Camera] --> D[ANPR Worker EXIT]

    B --> E[POST /detection/ingest]
    D --> E

    E --> F[(MySQL)]
    E --> G[WebSocket Broadcast]

    F --> H[Guard Dashboard]
    G --> H

    I[Manual Entry Form] --> F
    I --> G
```

---

## 7. Data Model Blueprint

### Core tables
1. users
- id, username, email, password_hash, role (ADMIN or GUARD), is_active, timestamps.

2. residents
- id, first_name, last_name, address, contact, timestamps.

3. resident_vehicles
- id, resident_id, plate_number_normalized, plate_number_raw, vehicle_type, timestamps.

4. visitors
- id, full_name, contact, reason, host_resident(optional), timestamps.

5. vehicle_logs
- id, plate_number_normalized, plate_number_raw, detected_at,
- source (CAMERA or MANUAL),
- camera_role (ENTRY_CAM, EXIT_CAM, UNKNOWN),
- status (TIME_IN, TIME_OUT),
- classification (RESIDENT or VISITOR),
- snapshot_path(optional),
- confidence(optional),
- notes(optional),
- created_by(optional FK users for manual),
- timestamps.

6. blacklist_entries
- id, plate_number_normalized, reason, is_active, created_by, timestamps.

### Important indexing
1. vehicle_logs: index on detected_at desc.
2. vehicle_logs: index on plate_number_normalized.
3. resident_vehicles: unique index on plate_number_normalized.
4. blacklist_entries: index on plate_number_normalized and is_active.

---

## 8. API Contract Blueprint

### Detection ingest
POST /detection/ingest

Request JSON:
1. plate_number
2. camera_role
3. confidence
4. snapshot_b64
5. detector_meta (optional)

Headers:
1. X-Api-Key: ANPR_API_KEY

Response JSON:
1. ok
2. log_id
3. status
4. normalized_plate
5. resident_match (bool)
6. blacklisted (bool)
7. message

### Health endpoint
GET /health/anpr

Response JSON:
1. entry_worker_state
2. exit_worker_state
3. last_detection_at_entry
4. last_detection_at_exit
5. stream_error_counters

### Dashboard frame endpoint
GET /detection/frame/ENTRY_CAM
GET /detection/frame/EXIT_CAM

---

## 9. ANPR Engine Design Requirements

1. Support RTSP over TCP with timeout and reconnect loop.
2. Try fallback candidate stream paths when primary fails.
3. Keep frame buffer small to reduce stale frames.
4. Track metrics every N seconds:
- frames_read
- detector_boxes
- ocr_candidates
- accepted_plates
- dropped_by_confidence
- dropped_by_debounce

5. Use two-stage OCR acceptance:
- Main detector path: moderate threshold.
- Full-frame fallback path: stricter threshold + short vote consensus.

6. Plate normalization must preserve character order and support:
- letters+digits format
- digits+letters format

7. Debounce per normalized plate.
8. Save snapshot on accepted detections.

---

## 10. Camera Configuration Requirements (Hikvision)

For both cameras:
1. Codec: H.264 (avoid H.265 for this pipeline unless fully tested).
2. Resolution: at least 1080p, prefer higher only if CPU can handle.
3. FPS: 20 to 25.
4. I-frame interval: 25.
5. Bitrate mode: CBR.
6. Bitrate target: 2048 to 3072 kbps.
7. Time sync: GMT+08:00, NTP enabled.

Physical placement requirements:
1. Plate width should occupy at least 12 percent of frame width near trigger zone.
2. Minimize tilt and extreme angle.
3. Avoid backlight.
4. Use stable shutter for moving targets.

---

## 11. Startup and Operations Requirements

### One-command startup
The new project must provide a single command that starts:
1. Django server (or ASGI server).
2. ENTRY ANPR worker.
3. EXIT ANPR worker.

### Mandatory runtime observability
1. Clearly print worker started or failed.
2. Show active RTSP URL per worker.
3. Show model load completion.
4. Show periodic metrics.
5. Show last successful log timestamp.

---

## 12. Security Requirements

1. Keep RTSP credentials in environment variables only.
2. Keep ANPR API key in environment variable.
3. Validate all ingest inputs.
4. CSRF protect normal web forms.
5. Enforce auth and role guards.
6. Never return secret values in API responses.

Note on camera username and password:
1. Most Hikvision RTSP streams require auth and should keep auth enabled.
2. Anonymous plug-and-play RTSP is not recommended for security and usually not default.

---

## 13. Environment Variable Template

Use this template in the new project:

```env
SECRET_KEY=change-this
DEBUG=True

DB_NAME=bantay_plaka
DB_USER=root
DB_PASSWORD=
DB_HOST=127.0.0.1
DB_PORT=3306

ANPR_API_KEY=change-this

ROBOFLOW_API_KEY=change-this
ROBOFLOW_MODEL_ID=plate-number-detection-fvpwv/5

ENTRY_CAMERA_RTSP=rtsp://admin:YOUR_PASSWORD@192.168.1.100:554/Streaming/Channels/101
EXIT_CAMERA_RTSP=rtsp://admin:YOUR_PASSWORD@192.168.1.101:554/Streaming/Channels/101
```

---

## 14. Rebuild Acceptance Checklist

The rebuild is acceptable only if all pass:
1. Migrations run cleanly from empty DB.
2. Admin and guard login flow works.
3. One-command startup launches all required services.
4. Dashboard shows both camera feeds.
5. Camera workers show periodic diagnostics.
6. Plate detection creates logs in DB.
7. Live dashboard updates through WebSocket.
8. Manual entry creates logs and broadcasts.
9. Blacklist detection blocks normal entry and shows alert.
10. If ANPR worker stops, dashboard explicitly shows STALE.

---

## 15. Full Detailed Prompt for Copilot (Copy-Paste)

Use the block below in a new repository with Copilot.

```text
You are my senior full-stack engineer. Build a production-ready web-based ANPR gate monitoring system from scratch.

Project name: BantayPlaka 2
Primary stack: Django + Channels + MySQL + OpenCV + EasyOCR + Roboflow inference
OS target: Windows first, but code should be portable.

Context and constraints:
1) I have 2 Hikvision HiWatch IP cameras.
2) Camera roles:
   - Camera at 192.168.1.100 is ENTRY
   - Camera at 192.168.1.101 is EXIT
3) RTSP format:
   - rtsp://admin:YOUR_PASSWORD@192.168.1.100:554/Streaming/Channels/101
   - rtsp://admin:YOUR_PASSWORD@192.168.1.101:554/Streaming/Channels/101
4) I use a non-PoE switch (TP-Link TL-SG1008D), so cameras are powered separately.
5) I need one-command startup for server + two ANPR workers.
6) I need robust logs, health endpoints, and visible worker health in dashboard.
7) Keep architecture simple and modular. No overengineering.

Build requirements:
A. Backend
1. Custom User model with ADMIN and GUARD roles.
2. Apps/modules:
   - accounts
   - residents
   - visitors
   - logs
   - detection
   - reports
3. Database tables:
   - users
   - residents
   - resident_vehicles
   - visitors
   - vehicle_logs
   - blacklist_entries
4. Detection ingest API:
   - POST /detection/ingest
   - header X-Api-Key
   - accepts plate_number, camera_role, confidence, snapshot_b64
   - normalizes plate
   - classifies resident/visitor
   - applies blacklist checks
   - decides status (TIME_IN/TIME_OUT) based on camera_role
   - saves log + optional snapshot
   - broadcasts update via WebSocket
5. Health API:
   - GET /health/anpr
   - returns worker health and stream error counters

B. Frontend
1. Admin dashboard:
   - stats cards
   - user management
2. Guard dashboard:
   - live entry and exit feeds
   - live gate logs table
   - worker health badges (LIVE/STALE)
   - manual entry button
3. Blacklist page and management UI.
4. Responsive design for desktop and laptop.

C. ANPR engine
1. Separate worker process per camera.
2. Robust RTSP handling:
   - TCP transport
   - open/read timeout
   - reconnect loop
   - fallback candidate stream paths
3. Detection + OCR pipeline:
   - detector-based OCR path
   - full-frame fallback OCR path
   - confidence thresholds
   - short temporal voting for noisy fallback path
4. Plate normalization rules must preserve character order and support:
   - letters+digits
   - digits+letters
5. Debounce duplicate logs.
6. Save snapshots for accepted detections.
7. Print diagnostics every few seconds:
   - frames_read
   - detector_boxes
   - ocr_candidates
   - accepted_plates
   - dropped_by_confidence
   - dropped_by_debounce

D. Startup and scripts
1. Provide a single command/script that starts:
   - Django server
   - ENTRY worker
   - EXIT worker
2. Use one canonical Python interpreter path in scripts.
3. Ensure startup windows remain open on errors for debugging.

E. Security and quality
1. Use environment variables for all secrets and RTSP credentials.
2. Validate and sanitize ingest input.
3. Protect role-based routes.
4. Write clean, maintainable code with clear comments only where needed.
5. Provide migrations and seed helper for admin account.

F. Deliverables
1. Full project structure.
2. requirements.txt.
3. .env.example.
4. Setup guide with exact commands for Windows PowerShell.
5. Troubleshooting guide section for:
   - camera not opening
   - worker stale
   - no detections
   - decode errors
6. Final acceptance checklist.

Important implementation order:
1) DB schema and migrations
2) backend logic and APIs
3) websocket events
4) frontend pages
5) ANPR workers and startup script
6) final docs and validation

When I send images later:
1) Analyze camera angle and plate size visibility.
2) Suggest exact camera placement and ROI recommendations.
3) Tune detection thresholds for that scene.

Do not stop at planning. Implement the full codebase end-to-end.
```

---

## 16. How To Use This README For a Fresh Restart

1. Create a new empty repository.
2. Paste the Full Detailed Prompt for Copilot section into Copilot Chat.
3. Generate project skeleton and code in implementation order.
4. Paste your real environment values in .env.
5. Connect cameras and run one-command startup.
6. Use the acceptance checklist before calling it complete.

---

This document is intentionally exhaustive so you can rebuild with less friction and avoid repeating the same failure cycle.
