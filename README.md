# SIH26187 — AI Border Surveillance System

**Smart India Hackathon | Ministry of Home Affairs — Sashastra Seema Bal (SSB)**

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-6.0-3178C6?logo=typescript&logoColor=white)
![YOLOv8x](https://img.shields.io/badge/YOLOv8x-TensorRT%20FP16-00FFFF?logo=nvidia&logoColor=black)
![Tests](https://img.shields.io/badge/tests-466%20passing-success)

---

## Problem Statement

**SIH26187** — Development of an AI-based video surveillance system for SSB border management that detects
suspicious activity and raises alerts in real time, using the existing IP CCTV infrastructure.

**Organization:** Ministry of Home Affairs — Sashastra Seema Bal (SSB)

## Our Solution

An end-to-end AI surveillance platform that turns existing IP CCTV cameras into an intelligent border
monitoring system — no new camera hardware required.

| Innovation | What it means |
|---|---|
| **Reuse existing CCTV** | Software only: any RTSP/IP camera, or a USB camera, plugs straight in |
| **Real-time AI detection** | YOLOv8x on TensorRT FP16 — 19 ms per frame on an RTX 3050 laptop GPU |
| **Military-grade HUD** | Tactical overlay drawn on the live feed: zones, trails, bracketed boxes, risk |
| **Explainable risk scoring** | Every alert carries the exact reasons that produced its 0–100 score |
| **Virtual fence zones** | 5 zone types with their own dwell thresholds and night rules |
| **Evidence chain of custody** | SHA-256 at capture, re-verified on demand, archive to cold storage |
| **Bharat border digital twin** | Live India map, 7 styles, threat heatmap, geo-fence rings |

## Architecture

```
┌──────────────────────────────────────────────┐
│   EXISTING IP CCTV  /  USB CAMERA  /  VIDEO  │
└───────────────────────┬──────────────────────┘
                        │  RTSP / device index / file
                        ▼
┌──────────────────────────────────────────────┐
│   ML PIPELINE            (Python, CUDA)      │
│   capture → enhance → YOLOv8x → ByteTrack →  │
│   ReID → fence → behaviour → risk → evidence │
└───────────────────────┬──────────────────────┘
                        │  WebSocket (annotated frames + detections)
                        ▼
┌──────────────────────────────────────────────┐
│   FASTAPI BACKEND                            │
│   50 REST + 2 WebSocket | JWT | RBAC | audit │
└───────────────────────┬──────────────────────┘
                        │  SQLAlchemy 2.0 async
                        ▼
┌──────────────────────────────────────────────┐
│   POSTGRESQL 16                              │
│   10 tables | 10 enums | Alembic migration   │
└───────────────────────┬──────────────────────┘
                        │  REST + WebSocket
                        ▼
┌──────────────────────────────────────────────┐
│   REACT CONSOLE                              │
│   13 screens | military HUD | threat map     │
└──────────────────────────────────────────────┘
```

## Features

### AI detection

- **YOLOv8x** (COCO pretrained) — person, 5 vehicle classes, 7 animal classes, knife and bat
- **ByteTrack** — the same person keeps one track id across frames
- **Appearance ReID** — a person who leaves and returns gets the same identity id again
- **Virtual fence** — public / buffer / sensitive / restricted / no man's land
- **Night and fog enhancement** — applied before detection when the frame is dark or hazy
- **Evidence capture** — SHA-256 snapshot plus a clip of 15 s before and 15 s after the alert

### Risk engine

| Factor | Points |
|---|---|
| Base | +10 |
| Buffer zone / sensitive zone | +10 / +30 |
| Restricted zone | +50 |
| No man's land | +100 |
| Loitering past the zone threshold | +20 |
| Moving toward the fence | +20 |
| Third or later appearance | +15 |
| Running | +25 |
| Weapon detected | +50 |
| Night (22:00–05:00) | × 1.5 |
| **Maximum** | **100** |

Bands: normal 0–20, low 21–40, suspicious 41–60, high risk 61–80, critical 81–100.
An alert is raised from **suspicious** upward.

### Security

- **JWT HS256** — 15 minute access token, 7 day refresh token
- **RBAC** — admin, regional head, supervisor, operator; operators see only their own cameras
- **Account lockout** — 3 failed logins lock the account for 30 minutes
- **Full audit trail** — every endpoint and WebSocket action is written to `audit_logs`
- **Rate limiting** — 100 requests/minute per IP, 10/minute on login
- **Evidence integrity** — SHA-256 computed while the upload streams to disk
- **Screen lock** — Ctrl+L (or Alt+L), plus auto-lock after 5 idle minutes

## Tech stack

| Layer | Technology |
|---|---|
| Detection | YOLOv8x (Ultralytics 8.3) + TensorRT 10.0.1 FP16 |
| Tracking | ByteTrack + HSV appearance re-identification |
| ML runtime | PyTorch 2.5.1 + CUDA 12.1, OpenCV 4.11 |
| Backend | FastAPI 0.141 + Python 3.11 + Pydantic 2 |
| Database | PostgreSQL 16 + SQLAlchemy 2.0 (async) + Alembic |
| Frontend | React 19 + TypeScript 6 (strict) + Tailwind CSS 4 + Vite 8 |
| State / data | Zustand 5, TanStack Query 5 |
| Map | Leaflet 1.9 + react-leaflet 5 + leaflet.heat |

## Test coverage

| Week | Area | Tests | Status |
|---|---|---|---|
| Week 1 | Database layer (models, schemas, CRUD, migration) | 75 | Passing |
| Week 2 | Backend API, WebSocket, auth, audit | 130 | Passing |
| Week 3 | ML pipeline (risk, fence, tracker, evidence, ReID) | 106 | Passing |
| Week 4 | React console (unit 119 + live integration 36) | 155 | Passing |
| **Total** | | **466** | **All passing** |

Run them with `python -m pytest tests`, `python -m pytest ml/tests`, and `npm test` inside `frontend/`.

## Installation

### Prerequisites

- Python 3.11+
- PostgreSQL 16+
- Node.js 20+
- NVIDIA GPU with CUDA 12.1 (optional — the pipeline falls back to CPU, slowly)

### Setup

```bash
# 1. Clone
git clone https://github.com/lathiyavasu2006-star/sih26187-border-surveillance.git
cd sih26187-border-surveillance

# 2. Environment file
cp .env.example .env          # Windows: copy .env.example .env
# edit .env: database URL, SECRET_KEY, ADMIN_PASSWORD, paths

# 3. Python dependencies
pip install -r requirements.txt

# 4. ML dependencies (PyTorch must come from the CUDA index, not plain PyPI)
pip install torch==2.5.1+cu121 torchvision==0.20.1+cu121 --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements-ml.txt

# 5. Database — creates the schema and stamps the Alembic revision
python -m backend.database.init_db
python backend/scripts/seed_admin.py

# 6. Frontend
cd frontend && npm install && cd ..

# 7. YOLOv8x weights into models/
python -c "from ultralytics import YOLO; YOLO('yolov8x.pt')"

# 8. Optional: build the TensorRT FP16 engine for your GPU (19 ms vs 32 ms per frame)
python -m ml.build_engine
```

### Run

On Windows, double-click **`START-PROTOTYPE.bat`**. It checks PostgreSQL, starts the three services in the
right order (the ML pipeline needs the backend to be up first) and prints the console link.
**`STOP-PROTOTYPE.bat`** shuts everything down.

Manually, in three terminals:

```bash
python -m uvicorn backend.main:app --port 8000     # 1. backend API
cd frontend && npm run dev                          # 2. operator console
python -m ml.main                                   # 3. ML pipeline (start last)
```

Console: `http://localhost:5173` · API docs: `http://127.0.0.1:8000/docs`

With no camera hardware, point the pipeline at a video file instead:

```bash
python -m ml.main --camera CAM-N-001=path/to/border.mp4 --no-register-webcam
```

## Project structure

```
sih26187/
├── backend/
│   ├── core/          # config, JWT auth, RBAC, rate limiting
│   ├── models/        # 10 SQLAlchemy models
│   ├── schemas/       # Pydantic request/response schemas
│   ├── routers/       # 50 REST endpoints
│   ├── services/      # audit, evidence, camera monitor, analysis jobs
│   ├── websocket/     # live camera stream + analysis stream
│   ├── migrations/    # Alembic V001
│   └── scripts/       # seed_admin, Postman collection generator
├── ml/                # 23 modules: detector, tracker, identity, fence,
│                      # risk_engine, annotator, evidence, pipeline, main
├── frontend/src/
│   ├── pages/         # 13 screens
│   ├── components/    # camera, map, alerts, modals, layout, UI primitives
│   ├── stores/        # Zustand (auth, UI, live)
│   └── hooks/         # data, keyboard, WebSocket, session
├── tests/             # database + API tests
├── ml/tests/          # ML pipeline tests
├── postman_collection.json
├── .env.example
└── START-PROTOTYPE.bat / STOP-PROTOTYPE.bat
```

## What this repository does not contain

Surveillance systems handle personal data, so the following never leave the operator's machine and are
excluded by `.gitignore`:

- `.env` — database credentials, JWT secret, admin password
- `evidence/` — snapshots and clips of real people
- `models/` — YOLOv8x weights (137 MB) and the GPU-specific TensorRT engine
- `datasets/`, `logs/` — training data and operational logs

## Roadmap

| Phase | Status | Scope |
|---|---|---|
| Phase 1 — Foundation | Complete | Database, API, ML pipeline, operator console |
| Phase 2 — Intelligence | In progress | Cross-camera identity, ANPR, behaviour models, deployable demo mode |
| Phase 3 — Scale | Planned | Multiple regional servers, many cameras per server |
| Phase 4 — Predict | Planned | Movement prediction, full digital twin, mobile app |

## Current limitations

Honest scope of this prototype:

- The detector uses **COCO pretrained weights**; no border-specific dataset has been trained yet. COCO has no
  firearm class, so weapon detection covers knives and bats only.
- Re-identification is appearance based and works **within one camera**, not across cameras.
- Tested with one USB camera and recorded video; PTZ, thermal, drone and radar integrations are registry
  entries only.
- Everything runs on a single machine in development mode. Production deployment needs HTTPS, hardened
  secrets and network integration.

## Disclaimer

This is a prototype built for the Smart India Hackathon problem statement SIH26187. It is not a deployed
government system, and it contains no operational or classified data. Actual deployment would require
additional security hardening, accreditation and integration with government networks.

---

**SIH26187 | Ministry of Home Affairs — SSB | Crown AI | VK King**
