# BANTAYPLAKA — ANPR Vehicle Monitoring System for Subdivision Security

A capstone project — a web-based Automatic Number Plate Recognition (ANPR) vehicle monitoring system designed for subdivision security. It logs the time-in and time-out of both **residents** and **visitors** through camera-based plate detection and a manual fallback mode.

---

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [System Requirements](#system-requirements)
- [Getting Started](#getting-started)
  - [1. Clone the Repository](#1-clone-the-repository)
  - [2. Create Virtual Environment](#2-create-virtual-environment)
  - [3. Install Dependencies](#3-install-dependencies)
  - [4. Configure Environment Variables](#4-configure-environment-variables)
  - [5. Create the Database](#5-create-the-database)
  - [6. Run Migrations](#6-run-migrations)
  - [7. Create a Superuser (Admin)](#7-create-a-superuser-admin)
  - [8. Start the Server](#8-start-the-server)
- [User Roles](#user-roles)
- [System Flow](#system-flow)
- [Pages & URLs](#pages--urls)
- [Database Schema](#database-schema)
- [WebSocket (Real-Time)](#websocket-real-time)
- [Manual Entry Mode](#manual-entry-mode)
- [ANPR Detection API](#anpr-detection-api)
- [Hardware Recommendations](#hardware-recommendations)
- [Default Credentials](#default-credentials)
- [Environment Variables Reference](#environment-variables-reference)

---

## Features

- **Role-based access** — Admin and Security Guard roles with separate dashboards
- **Resident management** — Register residents and link their vehicle plate numbers
- **Visitor logging** — Guards log visitor info and plate number on entry
- **Real-time live gate log** — WebSocket-powered live table that updates instantly when a plate is detected or manually entered
- **Resident/Visitor flag** — Every log entry is clearly tagged as `RESIDENT` or `VISITOR`
- **Time In / Time Out tracking** — All entries record entry status and timestamp
- **Manual Entry fallback** — When camera malfunctions, guards can manually enter plate details; all logs are flagged as `MANUAL` vs `CAMERA` for audit purposes
- **Plate snapshot storage** — Camera captures can be stored alongside log entries
- **User management** — Admin can create, edit, activate, and deactivate guard accounts
- **Log filtering** — Filter logs by plate number, entry type, and date
- **Paginated log history** — Full vehicle log list with pagination

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Framework** | Django 5.0.6 |
| **Database** | MySQL 8.x |
| **Real-time** | Django Channels 4.x + Daphne (ASGI) |
| **WebSocket Layer** | In-Memory Channel Layer (dev) / Redis (production) |
| **Frontend** | Django Templates + DaisyUI + Tailwind CSS (CDN) |
| **Icons** | Font Awesome 6 (CDN) |
| **Image Handling** | Pillow |
| **Env Config** | django-environ |
| **ANPR Engine** | OpenCV + YOLOv8n + EasyOCR *(detection module — separate setup)* |

---

## Project Structure

```
bantay-plaka/
│
├── apps/                        # All Django applications
│   ├── accounts/                # User auth, roles, dashboards
│   │   ├── migrations/
│   │   ├── models.py            # Custom User model (Admin / Guard roles)
│   │   ├── forms.py             # Login, UserCreate, UserEdit forms
│   │   ├── views.py             # Login, logout, admin & guard dashboards
│   │   ├── urls.py              # /login/ /logout/
│   │   ├── dashboard_urls.py    # /dashboard/admin/ /dashboard/guard/ etc.
│   │   └── admin.py
│   │
│   ├── residents/               # Resident and vehicle registration
│   │   ├── migrations/
│   │   ├── models.py            # Resident, Vehicle models
│   │   ├── forms.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   └── admin.py
│   │
│   ├── visitors/                # Visitor logging
│   │   ├── migrations/
│   │   ├── models.py            # Visitor model
│   │   ├── forms.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   └── admin.py
│   │
│   ├── logs/                    # Vehicle log (time in/out), manual entry
│   │   ├── migrations/
│   │   ├── models.py            # VehicleLog model
│   │   ├── forms.py             # ManualLogForm
│   │   ├── views.py             # manual_entry, log_list
│   │   ├── services.py          # broadcast_log() — WebSocket broadcaster
│   │   ├── consumers.py         # Django Channels WebSocket consumer
│   │   ├── routing.py           # WebSocket URL routing
│   │   ├── urls.py
│   │   └── admin.py
│   │
│   └── detection/               # ANPR engine integration
│       ├── views.py             # ingest_plate() API endpoint
│       └── urls.py              # /detection/ingest/
│
├── config/                      # Project configuration
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py                  # ASGI entry point (Daphne + Channels)
│   └── wsgi.py
│
├── templates/                   # All HTML templates
│   ├── base.html                # Main layout with sidebar + drawer
│   ├── accounts/
│   │   └── login.html
│   ├── dashboard/
│   │   ├── admin/
│   │   │   ├── index.html       # Admin dashboard
│   │   │   ├── user_management.html
│   │   │   └── user_form.html
│   │   └── guard/
│   │       └── index.html       # Guard dashboard
│   ├── residents/
│   │   ├── resident_list.html
│   │   ├── resident_form.html
│   │   └── vehicle_form.html
│   ├── visitors/
│   │   ├── visitor_form.html
│   │   └── visitor_list.html
│   ├── logs/
│   │   ├── manual_entry.html
│   │   └── log_list.html
│   └── partials/
│       ├── navbar.html          # Mobile top bar
│       ├── sidebar.html         # Main navigation sidebar
│       ├── messages.html        # Flash messages
│       ├── log_row.html         # Reusable log table row
│       └── websocket_listener.html  # WebSocket JS script
│
├── static/                      # Static files (CSS, JS, images)
├── media/                       # Uploaded files (plate snapshots)
├── manage.py
├── requirements.txt
├── .env                         # Environment variables (not committed)
├── .env.example                 # Template for .env
└── README.md
```

---

## System Requirements

- Python 3.10+
- MySQL 8.0+ (via Laragon, XAMPP, or standalone)
- pip
- Git

---

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/bantay-plaka.git
cd bantay-plaka
```

### 2. Create Virtual Environment

```bash
python -m venv venv
```

**Activate it:**

- Windows (PowerShell):
  ```powershell
  .\venv\Scripts\Activate.ps1
  ```
- Windows (CMD):
  ```cmd
  venv\Scripts\activate.bat
  ```
- macOS/Linux:
  ```bash
  source venv/bin/activate
  ```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
SECRET_KEY=your-secret-key-here
DEBUG=True

DB_NAME=bantay_plaka
DB_USER=root
DB_PASSWORD=
DB_HOST=127.0.0.1
DB_PORT=3306
```

> For Laragon, the default MySQL user is `root` with **no password**.

### 5. Create the Database

Open your MySQL client (Laragon HeidiSQL, MySQL Workbench, or CLI) and run:

```sql
CREATE DATABASE IF NOT EXISTS bantay_plaka
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
```

Or via CLI (Laragon):

```powershell
& "C:\laragon\bin\mysql\mysql-8.0\bin\mysql.exe" -u root -e "CREATE DATABASE IF NOT EXISTS bantay_plaka CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

### 6. Run Migrations

```bash
python manage.py migrate
```

### 7. Create a Superuser (Admin)

```bash
python manage.py shell -c "
from apps.accounts.models import User
u = User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
u.role = 'ADMIN'
u.first_name = 'System'
u.last_name = 'Admin'
u.save()
print('Admin created.')
"
```

### 8. Start the Server

BantayPlaka uses **Daphne** (ASGI server) to support WebSockets for real-time updates.

```bash
.\venv\Scripts\daphne.exe -b 127.0.0.1 -p 8000 config.asgi:application
```

> On macOS/Linux:
> ```bash
> daphne -b 127.0.0.1 -p 8000 config.asgi:application
> ```

Then open your browser at: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## User Roles

| Role | Access |
|---|---|
| **Admin** | Full access — create guards, register residents + vehicles, view all logs, manual entry, user management |
| **Security Guard** | Guard dashboard, log visitors, manual entry, view logs and residents (read-only) |

> Admin accounts are created via the Django shell or by another Admin through the User Management page.
> Guards are created by the Admin through the **User Management** page (`/dashboard/admin/users/`).

---

## System Flow

```
ENTRY / EXIT GATE
       │
  [IP Camera]
       │
  [ANPR Engine]  ──  detects plate  ──►  POST /detection/ingest/
       │
  [Django Backend]
       │
  Is plate in Resident DB?
  ├── YES  →  entry_type = RESIDENT, auto-fill resident name
  └── NO   →  entry_type = VISITOR
       │
  VehicleLog saved (plate, type, status, source, timestamp)
       │
  WebSocket broadcast → all connected dashboards update live
```

**Manual fallback (camera offline):**
```
Guard opens Manual Entry page
  → Types plate number, selects Time In / Time Out
  → System auto-checks if plate is a resident
  → Log saved with source = MANUAL
  → Live monitor still updates via WebSocket
```

---

## Pages & URLs

| URL | Role | Description |
|---|---|---|
| `/login/` | Public | Login page |
| `/logout/` | Authenticated | Logout (POST) |
| `/dashboard/` | Authenticated | Redirects to role-specific dashboard |
| `/dashboard/admin/` | Admin | Stats overview + live gate log |
| `/dashboard/admin/users/` | Admin | List all users |
| `/dashboard/admin/users/create/` | Admin | Create a new user |
| `/dashboard/admin/users/<id>/edit/` | Admin | Edit a user |
| `/dashboard/admin/users/<id>/toggle/` | Admin | Activate / deactivate user |
| `/dashboard/guard/` | Guard | Live gate log + quick actions |
| `/logs/` | Both | Full log list with filters |
| `/logs/manual/` | Both | Manual plate entry form |
| `/residents/` | Both | View registered residents |
| `/residents/create/` | Admin | Register new resident |
| `/residents/<id>/edit/` | Admin | Edit resident |
| `/residents/<id>/delete/` | Admin | Remove resident |
| `/residents/<id>/vehicles/add/` | Admin | Add vehicle to resident |
| `/visitors/` | Both | View visitor log |
| `/visitors/log/` | Both | Log a new visitor |
| `/detection/ingest/` | System | ANPR engine posts detected plate (POST, JSON) |
| `ws://127.0.0.1:8000/ws/logs/` | WebSocket | Real-time log stream |

---

## Database Schema

### `users` (Custom User)
| Column | Type | Notes |
|---|---|---|
| id | BigInt PK | |
| username | VARCHAR | Unique |
| first_name | VARCHAR | |
| last_name | VARCHAR | |
| contact_number | VARCHAR | |
| role | VARCHAR | `ADMIN` or `GUARD` |
| is_active | Boolean | |
| date_joined | DateTime | |

### `residents`
| Column | Type | Notes |
|---|---|---|
| id | BigInt PK | |
| first_name | VARCHAR | |
| last_name | VARCHAR | |
| address | VARCHAR | |
| contact_number | VARCHAR | |
| registered_by_id | FK → users | |
| created_at | DateTime | |

### `vehicles`
| Column | Type | Notes |
|---|---|---|
| id | BigInt PK | |
| resident_id | FK → residents | |
| plate_number | VARCHAR | Unique, indexed |
| vehicle_type | VARCHAR | CAR / MOTORCYCLE / TRUCK / VAN / OTHER |
| make | VARCHAR | e.g. Toyota |
| model | VARCHAR | e.g. Vios |
| color | VARCHAR | |

### `visitors`
| Column | Type | Notes |
|---|---|---|
| id | BigInt PK | |
| first_name | VARCHAR | |
| last_name | VARCHAR | |
| contact_number | VARCHAR | |
| purpose | VARCHAR | |
| host_name | VARCHAR | Resident being visited |
| plate_number | VARCHAR | |
| vehicle_type | VARCHAR | |
| logged_by_id | FK → users | |
| created_at | DateTime | |

### `vehicle_logs` *(core table)*
| Column | Type | Notes |
|---|---|---|
| id | BigInt PK | |
| plate_number | VARCHAR | Indexed |
| entry_type | VARCHAR | `RESIDENT` / `VISITOR` / `UNKNOWN` |
| status | VARCHAR | `TIME_IN` / `TIME_OUT` |
| source | VARCHAR | `CAMERA` / `MANUAL` |
| resident_name | VARCHAR | Denormalized for display |
| visitor_name | VARCHAR | Denormalized for display |
| snapshot | ImageField | Optional plate photo |
| timestamp | DateTime | Auto, indexed |
| logged_by_id | FK → users | Guard who manual-logged (nullable) |

---

## WebSocket (Real-Time)

The live gate log uses Django Channels over WebSocket. Every time a vehicle log is created — whether from the ANPR camera or manual entry — it is instantly broadcast to all connected clients.

**WebSocket endpoint:**
```
ws://127.0.0.1:8000/ws/logs/
```

**Broadcast payload (JSON):**
```json
{
  "id": 42,
  "plate_number": "ABC 1234",
  "entry_type": "RESIDENT",
  "status": "TIME_IN",
  "source": "CAMERA",
  "display_name": "Juan Dela Cruz",
  "timestamp": "Mar 05, 2026 08:02:14 AM"
}
```

The frontend JavaScript in `templates/partials/websocket_listener.html` listens for these messages and prepends a new row to the live log table.

---

## Manual Entry Mode

When the ANPR camera is offline or malfunctioning, the guard switches to manual mode at `/logs/manual/`.

- The guard types the plate number and selects Time In or Time Out
- The system automatically checks if the plate belongs to a registered resident
- The log is saved with `source = MANUAL` for audit trail purposes
- The live monitor still updates in real-time via WebSocket
- Manual logs are visually flagged with a ✏️ Manual badge in the log table

---

## ANPR Detection API

The detection module exposes an endpoint for the ANPR engine to push detected plates:

**Endpoint:** `POST /detection/ingest/`

**Request body (JSON):**
```json
{
  "plate_number": "ABC 1234",
  "status": "TIME_IN"
}
```

**Response:**
```json
{
  "ok": true,
  "log_id": 42
}
```

The backend will:
1. Check if the plate is registered as a resident vehicle
2. Set `entry_type` accordingly (`RESIDENT` or `VISITOR`)
3. Save the `VehicleLog` entry
4. Broadcast to all connected WebSocket clients

> The ANPR engine (OpenCV + YOLOv8n + EasyOCR) runs as a separate Python process and calls this endpoint.

---

## Hardware Recommendations (Philippines)

| Component | Recommended | Price (PHP) |
|---|---|---|
| IP Camera | Hikvision DS-2CD2T47G2-L (ColorVu, 8MP) | ₱5,000–₱7,000 |
| PoE Switch (4-port) | Any brand | ₱1,000–₱1,500 |
| Cat6 Cable (per meter) | — | ₱15–₱25 |
| Processing Unit | Mini PC (Intel N100, 8GB RAM) | ₱10,000–₱12,000 |
| Guard Monitor | Any 21" monitor | ₱4,000–₱6,000 |
| **Total** | | **~₱25,000–₱35,000** |

**Camera placement:** Mount 2–4 meters from the gate, angled slightly downward at the vehicle plate. Use a fixed 4mm lens for gate distances of 2–5 meters.

---

## Default Credentials

> **Change these immediately in production.**

| Username | Password | Role |
|---|---|---|
| `admin` | `admin123` | Admin |
| `guard1` | `guard123` | Security Guard (test) |

---

## Environment Variables Reference

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | Django secret key | *(required)* |
| `DEBUG` | Debug mode (`True`/`False`) | `True` |
| `DB_NAME` | MySQL database name | `bantay_plaka` |
| `DB_USER` | MySQL username | `root` |
| `DB_PASSWORD` | MySQL password | *(empty for Laragon)* |
| `DB_HOST` | MySQL host | `127.0.0.1` |
| `DB_PORT` | MySQL port | `3306` |

---

## License

This project is developed as a capstone requirement. All rights reserved by the project authors.
