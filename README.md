# Purplle Store Intelligence Platform

> Real-time in-store customer analytics — from raw camera footage to live dashboard metrics.

A full-stack retail analytics platform built for Purplle stores. It ingests visual telemetry from in-store cameras, tracks customer movement and dwell time, filters out staff, detects queue anomalies, and streams everything live to a React dashboard — all in under 100ms latency.

---

## Table of Contents

1. [What This Project Does](#1-what-this-project-does)
2. [Architecture Overview](#2-architecture-overview)
3. [Tech Stack](#3-tech-stack)
4. [Project Structure](#4-project-structure)
5. [Prerequisites](#5-prerequisites)
6. [Local Development Setup (Recommended)](#6-local-development-setup-recommended)
   - [6.1 Clone & Environment](#61-clone--environment)
   - [6.2 Backend API](#62-backend-api)
   - [6.3 Dashboard UI](#63-dashboard-ui)
   - [6.4 Pipeline Simulator](#64-pipeline-simulator)
7. [Environment Variables](#7-environment-variables)
8. [Docker Compose (Full Stack)](#8-docker-compose-full-stack)
9. [API Reference](#9-api-reference)
10. [Running Tests](#10-running-tests)
11. [Camera & Zone Configuration](#11-camera--zone-configuration)
12. [Key Design Decisions](#12-key-design-decisions)

---

## 1. What This Project Does

Purplle runs physical cosmetics stores across India. Each store has multiple CCTV cameras monitoring entrances, aisles, billing counters, and display zones. This platform turns that raw footage into actionable intelligence:

| Metric | Description |
|---|---|
| **Live Footfall** | Count of unique customers who entered the store today |
| **Active Customers** | Customers currently on the floor (not yet exited) |
| **Zone Dwell Time** | Average seconds spent in each store zone (Skincare, Makeup, Billing, etc.) |
| **Queue Analytics** | Real-time billing queue depth, average wait time, and abandon rate |
| **Conversion Funnel** | Entry → Zone Visit → Billing Queue → Purchase drop-off analysis |
| **POS Correlation** | Match exit events to POS transactions (±60s) to estimate purchase rate |
| **Anomaly Detection** | Queue spikes, conversion drops vs. 7-day average, dead zones (no visits in 30 min) |
| **Staff Exclusion** | 5-layer filter ensures staff movement never contaminates customer analytics |
| **Cross-Camera Re-ID** | MobileNetV3 visual embeddings to track customers across multiple camera views |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         In-Store Edge (CV Pipeline)                     │
│                                                                         │
│  ┌─────────┐   ┌─────────┐   ┌──────────────────┐   ┌──────────────┐  │
│  │ Camera 1│──▶│         │──▶│ YOLOv11+ByteTrack│──▶│ Staff Filter │  │
│  │ Camera 2│──▶│  Video  │   │   (Detect+Track) │   │ (HSV+Behav.) │  │
│  │ Camera 3│──▶│ Process │   └──────────────────┘   └──────────────┘  │
│  │ Camera 4│──▶│   -or-  │   ┌──────────────────┐   ┌──────────────┐  │
│  └─────────┘   │  JSONL  │──▶│  Group Detector  │   │  Re-ID Buffer│  │
│                │ Replay  │   │ (Spatial Cluster) │   │ (MobileNetV3)│  │
│                └─────────┘   └──────────────────┘   └──────────────┘  │
│                                       │                                 │
│                              POST /api/v1/events                        │
└───────────────────────────────────────┼─────────────────────────────────┘
                                        │
┌───────────────────────────────────────▼─────────────────────────────────┐
│                         FastAPI Backend (app/)                          │
│                                                                         │
│  ┌─────────────┐   ┌─────────────────┐   ┌───────────────────────────┐ │
│  │  Ingestion  │──▶│ Pydantic Schema │──▶│  Staff?  →  staff_tracks  │ │
│  │  Service    │   │  Normalisation  │   │  Cust?   →  spatial_events│ │
│  └─────────────┘   └─────────────────┘   └───────────────────────────┘ │
│         │                                          │                    │
│         ▼                                          ▼                    │
│  ┌─────────────┐   ┌──────────────────────────────────────────────────┐ │
│  │  Pub/Sub    │   │          MongoDB (Motor AsyncIO)                  │ │
│  │  Broker     │   │  spatial_events · customer_sessions              │ │
│  │ (In-memory  │   │  staff_tracks · pos_transactions · zones         │ │
│  │  or Upstash)│   └──────────────────────────────────────────────────┘ │
│  └─────────────┘                │                                       │
│         │                       ▼                                       │
│         │            ┌─────────────────────┐                           │
│         │            │  Analytics Service  │                           │
│         │            │  Anomalies Service  │                           │
│         │            │  Simulated Data Svc │                           │
│         │            └─────────────────────┘                           │
└─────────┼───────────────────────────────────────────────────────────────┘
          │ WebSocket (ws://localhost:8000/api/v1/ws/{store_id})
┌─────────▼───────────────────────────────────────────────────────────────┐
│                       Next.js 16 Dashboard (dashboard/)                 │
│                                                                         │
│  ┌────────────┐  ┌──────────────┐  ┌───────────┐  ┌─────────────────┐  │
│  │ LiveCanvas │  │MetricsCards  │  │ZoneHeatmap│  │  QueueGauge     │  │
│  │(LERP Anim) │  │(KPI Cards)   │  │(Zone Bars)│  │(Radial Gauge)   │  │
│  └────────────┘  └──────────────┘  └───────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Data Flow Summary

1. **Camera footage** is processed by YOLOv11 + ByteTrack to produce track bounding boxes per frame.
2. The **Staff Filter** checks HSV color signatures and behavioral velocity patterns to flag staff tracks.
3. **Group Detector** clusters simultaneous entrants to avoid double-counting footfall.
4. **Re-ID Buffer** uses MobileNetV3 embeddings to maintain track identity across camera views.
5. Each track event is **POSTed to `/api/v1/events`** by the pipeline or simulator.
6. The **Ingestion Service** validates & normalizes the payload, routes staff to a separate collection, and publishes customer events to the in-process Pub/Sub broker.
7. MongoDB stores all events; the **Analytics Service** runs aggregation pipelines for metrics.
8. The **WebSocket router** subscribes to the Pub/Sub channel and streams live events to connected dashboard clients.
9. The **Next.js Dashboard** renders LERP-animated tracking dots on store floor plan images and displays KPI cards, zone heatmaps, and queue gauges.

---

## 3. Tech Stack

| Layer | Technology | Version |
|---|---|---|
| **API Framework** | FastAPI | ≥ 0.115 |
| **ASGI Server** | Uvicorn | ≥ 0.30 |
| **Database** | MongoDB (Motor async client) | ≥ 6.0 (replica set) |
| **Cache / Pub-Sub** | Upstash Redis REST or in-memory fallback | — |
| **Schema Validation** | Pydantic v2 + pydantic-settings | ≥ 2.7 |
| **HTTP Client** | httpx (async) | ≥ 0.27 |
| **Object Detection** | Ultralytics YOLOv11 | ≥ 8.3 |
| **Multi-Object Tracking** | ByteTrack (via lapx) | — |
| **Re-ID Embeddings** | PyTorch + MobileNetV3 | ≥ 2.0 |
| **Computer Vision** | OpenCV headless | ≥ 4.8 |
| **Dashboard Framework** | Next.js + React | 16.x / 19.x |
| **Dashboard Language** | TypeScript | ≥ 5 |
| **Dashboard Runtime** | Bun (or Node.js) | latest |
| **Containerisation** | Docker + Docker Compose | ≥ 3.8 |
| **Python Version** | Python | 3.12 |

---

## 4. Project Structure

```
purplle-tech-challenge-2026-round-2/
├── app/                                # FastAPI backend
│   ├── main.py                         # App entrypoint, lifespan, CORS, health endpoint
│   ├── Dockerfile                      # Production image for the API
│   ├── requirements.txt                # Backend Python dependencies
│   ├── core/
│   │   ├── config.py                   # pydantic-settings — reads .env
│   │   ├── database.py                 # Motor MongoDB client, DB init, zone/POS seeding
│   │   ├── upstash_redis.py            # Upstash REST client + in-memory fallback
│   │   └── pub_sub.py                  # In-process asyncio Pub/Sub broker
│   ├── models/
│   │   ├── schemas.py                  # Pydantic request/response models + field normaliser
│   │   └── domain.py                   # Domain models (CustomerSession, StaffTrack)
│   ├── api/
│   │   ├── endpoints.py                # All REST routes (/events, /metrics, /zones, /funnel …)
│   │   └── websockets.py               # WebSocket route with keep-alive + pub/sub relay
│   └── services/
│       ├── ingestion.py                # Event routing, idempotency, pub/sub publish
│       ├── analytics.py                # MongoDB aggregation pipelines
│       ├── anomalies.py                # Queue spike, conversion drop, dead zone detection
│       ├── simulated_data.py           # Reads pipeline JSONL output for offline demo mode
│       └── idle_monitor.py             # Background task: broadcasts store_idle if no events
│
├── store-intelligence/
│   └── pipeline/                       # CV pipeline (runs on edge or replays JSONL)
│       ├── simulate.py                 # JSONL replay simulator — posts to API at real-time speed
│       ├── video_processor.py          # Multi-camera threaded orchestrator (LIVE mode)
│       ├── detect.py                   # YOLOv11 + ByteTrack inference worker
│       ├── heartbeat.py                # Sends heartbeat pings during idle periods
│       ├── process_videos.py           # Batch video processor (generates JSONL output)
│       ├── run.sh                      # Entrypoint script (Docker + local bash)
│       ├── Dockerfile                  # Production image for the pipeline
│       ├── requirements.txt            # Pipeline Python dependencies
│       ├── config/
│       │   └── cameras.yaml            # Camera IDs, roles, and file paths per store
│       ├── tracker/
│       │   ├── staff_filter.py         # HSV uniform detection + velocity/revisit filter
│       │   ├── reid_buffer.py          # MobileNetV3 cross-camera re-identification
│       │   └── group_detector.py       # Spatial proximity clustering for group entries
│       └── data/
│           ├── sample_eventsbe42122.jsonl   # Sample event replay file
│           ├── output/                      # Pipeline-processed JSONL + summary.json per store
│           │   ├── ST1076/
│           │   └── ST1008/
│           └── videos/                      # Camera footage (not included in repo)
│
├── dashboard/                          # Next.js 16 frontend
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx              # Root layout with ThemeProvider
│   │   │   ├── page.tsx                # Global overview page (aggregated metrics)
│   │   │   └── store/[id]/page.tsx     # Per-store live tracking page
│   │   ├── components/
│   │   │   ├── LiveCanvas.tsx          # HTML5 Canvas with LERP-animated tracking dots
│   │   │   ├── MetricsCards.tsx        # Glassmorphic KPI cards
│   │   │   ├── QueueGauge.tsx          # Radial gauge for queue depth
│   │   │   ├── ZoneHeatmap.tsx         # Sorted zone dwell-time progress bars
│   │   │   └── StoreSelector.tsx       # Store navigation + dark/light mode toggle
│   │   ├── context/
│   │   │   └── ThemeContext.tsx        # Light/dark mode React context
│   │   └── hooks/
│   │       ├── useWebSocket.ts         # Auto-reconnecting WebSocket hook
│   │       ├── useStoreMetrics.ts      # Polling hook for REST metrics
│   │       └── useZones.ts             # Static zone configuration fetch
│   └── public/
│       └── layouts/                    # Store floor plan PNG images
│
├── tests/                              # Full backend test suite
│   ├── conftest.py                     # Mock MongoDB + Redis + PubSub fixtures
│   ├── test_endpoints.py               # REST API integration tests
│   ├── test_ingestion.py               # Schema normalisation + event routing unit tests
│   └── test_websockets.py              # WebSocket connection + broadcast tests
│
├── docs/
│   ├── DESIGN.md                       # Architecture walkthrough + AI decision retrospective
│   └── CHOICES.md                      # Detailed rationale for 3 key technical decisions
│
├── docker-compose.yml                  # Full local orchestration (Mongo + API + Pipeline)
├── .env.example                        # Environment variable template
├── POS - sample transactionsb1e826f.csv  # Sample POS transaction data for seeding
└── walkthrough.md                      # Implementation walkthrough
```

---

## 5. Prerequisites

### Required for Local Development

| Tool | Version | Install |
|---|---|---|
| Python | 3.12+ | [python.org](https://python.org) |
| MongoDB | 6.0+ (with replica set) | [mongodb.com](https://mongodb.com) or Docker |
| Node.js | 18+ or **Bun** | [bun.sh](https://bun.sh) |
| Git | Any | [git-scm.com](https://git-scm.com) |

### Required for Docker Compose

| Tool | Version |
|---|---|
| Docker Desktop | 4.x+ |
| Docker Compose | v2 (bundled with Docker Desktop) |

### Optional (for LIVE camera mode only)

| Tool | Notes |
|---|---|
| CUDA GPU | Recommended for real-time YOLOv11 inference |
| Physical CCTV cameras | Or USB webcams |
| PyTorch with CUDA | Auto-installed via requirements.txt |

---

## 6. Local Development Setup (Recommended)

This is the fastest way to get everything running. You'll have three terminal windows open: API, Dashboard, and Pipeline.

### 6.1 Clone & Environment

```powershell
# Clone the repository
git clone <repo-url>
cd purpell/v2

# Copy environment template
cp .env.example .env
```

Open `.env` and verify the defaults are correct for local development (MongoDB on localhost, Upstash fields left blank). See [Section 7](#7-environment-variables) for details.

---

### 6.2 Backend API

Open a terminal in the project root (`d:\purpell\v2`).

```powershell
# Create a Python virtual environment
python -m venv venv

# Activate it (Windows PowerShell)
.\venv\Scripts\Activate.ps1

# Or Windows CMD
.\venv\Scripts\activate.bat

# Install backend dependencies
pip install -r app/requirements.txt

# Start the API with live reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**What happens on startup:**
- Motor connects to MongoDB and creates a time-series `spatial_events` collection (if it doesn't exist)
- Indexes are created on all collections
- Zone boundary polygons are seeded for ST1076 and ST1008
- POS transactions are imported from the sample CSV file
- The Idle Monitor background task starts polling for store activity

**Verify it's running:**

```powershell
# Health check
curl http://localhost:8000/health

# Interactive API docs (Swagger UI)
# Open in browser: http://localhost:8000/docs
```

> **Note on MongoDB replica set:** The `spatial_events` time-series collection requires a replica set. For local MongoDB without Docker, run:
> ```
> mongod --replSet rs0
> ```
> Then in a mongosh shell:
> ```js
> rs.initiate({ _id: "rs0", members: [{ _id: 0, host: "localhost:27017" }] })
> ```
> If you skip this, the API will still start but use a regular collection as fallback.

---

### 6.3 Dashboard UI

Open a **new terminal** in `d:\purpell\v2\dashboard`.

```powershell
# Using Bun (recommended — faster installs)
bun install
bun run dev

# Or using npm
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

**Dashboard pages:**
| URL | What you see |
|---|---|
| `http://localhost:3000` | Global overview — both stores, aggregated metrics |
| `http://localhost:3000/store/ST1076` | Store 1 (Mumbai Central) — live tracking canvas + metrics |
| `http://localhost:3000/store/ST1008` | Store 2 (Delhi SelectCitywalk) — live tracking canvas + metrics |

> **Environment:** The dashboard reads `NEXT_PUBLIC_API_URL` from `dashboard/.env.local`. If that file doesn't exist, it defaults to `http://localhost:8000`. No configuration needed for local development.

---

### 6.4 Pipeline Simulator

The simulator reads pre-processed JSONL event files and replays them chronologically to the API — perfect for local demos without real cameras.

Open a **new terminal** in the project root.

```powershell
# Activate venv first if not already active
.\venv\Scripts\Activate.ps1

# Run the simulator in loop mode (replays continuously)
.\venv\Scripts\python store-intelligence\pipeline\simulate.py `
    --loop `
    --speed 1.5 `
    --api-url http://localhost:8000

# Options:
#   --loop          Loop infinitely after each replay completes
#   --speed 1.5     Replay at 1.5x real time (higher = faster)
#   --file <path>   Custom JSONL file (default: data/sample_eventsbe42122.jsonl)
#   --store ST1076  Force override store_id in all events
#   --api-url URL   Target API endpoint (default: http://localhost:8000)
```

Once the simulator starts posting events, you'll see tracking dots appear on the live canvas in the dashboard within 1–2 seconds.

**For LIVE mode** (real cameras + YOLOv11):

```powershell
# Install pipeline-specific dependencies
pip install -r store-intelligence\pipeline\requirements.txt

# Set execution mode in .env
# EXECUTION_MODE=LIVE

# Start the multi-camera video processor
.\venv\Scripts\python store-intelligence\pipeline\video_processor.py
```

---

## 7. Environment Variables

Copy `.env.example` to `.env` and configure:

```env
# ─────────────────────────────────────────────────
# MongoDB
# ─────────────────────────────────────────────────

# Local MongoDB with replica set (required for time-series collections)
MONGO_URI=mongodb://localhost:27017/store_intelligence?replicaSet=rs0

# OR: MongoDB Atlas (cloud) — no replica set config needed
# MONGO_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/store_intelligence

# ─────────────────────────────────────────────────
# Upstash Redis (optional — leave blank for local dev)
# ─────────────────────────────────────────────────
# When blank, the system uses an in-memory Pub/Sub broker and key store.
# This is perfectly fine for local development and single-node deployments.
# Fill these in from your Upstash dashboard for production / multi-instance setups.
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=

# ─────────────────────────────────────────────────
# Pipeline Execution Mode
# ─────────────────────────────────────────────────
# SIMULATED: Replays JSONL files from pipeline/data/output/
# LIVE:      Runs YOLOv11 inference on real camera streams
EXECUTION_MODE=SIMULATED

# ─────────────────────────────────────────────────
# Backend API URL (used by the pipeline to POST events)
# ─────────────────────────────────────────────────
API_URL=http://localhost:8000
```

> **Upstash fallback:** If `UPSTASH_REDIS_REST_URL` is empty, all pub/sub and key operations run in-process using Python's `asyncio.Queue`. The dashboard and API work identically — you won't notice any difference in single-node local dev.

---

## 8. Docker Compose (Full Stack)

Docker Compose brings up the full platform in one command: MongoDB (with replica set init), the FastAPI backend, and the pipeline simulator — all networked together.

### 8.1 Quickstart

```bash
# Build all images and start all services
docker compose up --build

# Or run detached (in background)
docker compose up --build -d

# Follow logs for a specific service
docker compose logs -f api
docker compose logs -f pipeline
```

### 8.2 Services

| Service | Container | Port | Description |
|---|---|---|---|
| `mongo` | `store-intel-db` | `27017` | MongoDB 6 with replica set |
| `mongo-init` | `store-intel-db-init` | — | One-shot replica set initialiser |
| `api` | `store-intel-api` | `8000` | FastAPI backend |
| `pipeline` | `store-intel-pipeline` | — | JSONL simulator or LIVE processor |

### 8.3 Configuration with Docker

Pass Upstash credentials at runtime (they default to empty, falling back to in-memory):

```bash
UPSTASH_REDIS_REST_URL=https://your.upstash.io \
UPSTASH_REDIS_REST_TOKEN=your-token \
docker compose up --build
```

Or create a `.env` file and Docker Compose will read it automatically.

### 8.4 Run LIVE mode in Docker

```bash
# Set execution mode to LIVE in your .env
EXECUTION_MODE=LIVE docker compose up --build

# Ensure camera video files are mounted at:
# store-intelligence/pipeline/data/videos/Store 1/*.mp4
# store-intelligence/pipeline/data/videos/Store 2/*.mp4
```

### 8.5 Useful Docker commands

```bash
# Stop all containers but keep volumes
docker compose stop

# Stop and remove containers AND volumes (wipes MongoDB data)
docker compose down -v

# Rebuild a single service
docker compose up --build api

# Open a shell in the API container
docker compose exec api bash

# View container resource usage
docker compose stats
```

### 8.6 Startup order

Docker Compose waits for MongoDB to pass its healthcheck before starting the API. The pipeline uses `run.sh` which polls `GET /health` up to 30 times (3s intervals) before starting event replay — this prevents the simulator from posting events before the API finishes seeding zones and indexes.

---

## 9. API Reference

The full interactive API reference (Swagger UI) is available at **[http://localhost:8000/docs](http://localhost:8000/docs)** when the server is running.

### Core Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | System health — DB status, stale feed detection, last event timestamps |
| `GET` | `/api/v1/stores` | List all configured stores with data source status |
| `POST` | `/api/v1/events` | Ingest a single visual telemetry event |
| `POST` | `/api/v1/events/ingest` | Batch ingest up to 500 events (partial success supported) |
| `GET` | `/api/v1/metrics/{store_id}` | Aggregated metrics (footfall, dwell, queue, correlation) |
| `GET` | `/api/v1/heatmap/{store_id}` | Zone coordinate heatmap (intensity normalized 0–100) |
| `GET` | `/api/v1/stores/{store_id}/zones` | Zone boundary polygons (GeoJSON) |
| `GET` | `/api/v1/stores/{store_id}/metrics` | Alias for `/metrics/{store_id}` |
| `GET` | `/api/v1/stores/{store_id}/funnel` | Conversion funnel (Entry → Zone → Billing → Purchase) |
| `GET` | `/api/v1/stores/{store_id}/heatmap` | Normalized heatmap with data confidence flag |
| `GET` | `/api/v1/stores/{store_id}/anomalies` | Active anomalies (queue spike, conversion drop, dead zone) |
| `POST` | `/api/v1/transactions` | Ingest a POS transaction |
| `GET` | `/api/v1/transactions/{store_id}` | Recent POS transactions for a store |
| `GET` | `/api/v1/data-source/{store_id}` | Current data source info (simulated or live) |
| `WS` | `/api/v1/ws/{store_id}` | WebSocket — live customer track stream |

### Event Payload (POST /api/v1/events)

The API accepts flexible field names and normalises them automatically:

```json
{
  "event_type": "entry",
  "store_id": "ST1076",
  "track_id": "cust_001",
  "camera_id": "CAM1",
  "timestamp": "2026-06-04T10:00:00Z",
  "is_staff": false,
  "x": 0.25,
  "y": 0.45,
  "gender": "female",
  "age": 27.0
}
```

**Aliased fields accepted (auto-mapped):**

| Sent as | Stored as |
|---|---|
| `store_code: "store_1076"` | `store_id: "ST1076"` |
| `id_token: 12345` | `track_id: "12345"` |
| `event_timestamp` | `timestamp` |
| `gender_pred` | `gender` |
| `age_pred` | `age` |
| `x, y` (pixels or 0–1) | `location.coordinates` (normalized 0–1) |

### WebSocket Protocol

Connect to `ws://localhost:8000/api/v1/ws/{store_id}` (e.g. `ws://localhost:8000/api/v1/ws/ST1076`).

**Keep-alive ping:**
```
Client → Server:  ping
Server → Client:  {"type": "pong"}
```

**Live event stream (customer events only — staff excluded):**
```json
{
  "event_type": "zone_entered",
  "store_id": "ST1076",
  "track_id": "cust_001",
  "camera_id": "CAM1",
  "timestamp": "2026-06-04T10:00:05.123456+00:00",
  "location": { "type": "Point", "coordinates": [0.32, 0.55] },
  "zone_id": "Z03",
  "zone_name": "F.O.H Center (Fragrance/Nail)"
}
```

**Idle event (no activity for >60s):**
```json
{
  "event_type": "store_idle",
  "store_id": "ST1076",
  "timestamp": "2026-06-04T10:05:00.000000+00:00"
}
```

---

## 10. Running Tests

The test suite runs entirely in-process — no external MongoDB or Redis required. Mock Motor and Redis clients are provided by `conftest.py`.

```powershell
# Activate venv
.\venv\Scripts\Activate.ps1

# Run all 19 tests
.\venv\Scripts\python -m pytest -v

# Run a specific test file
.\venv\Scripts\python -m pytest tests/test_ingestion.py -v

# Run a single test
.\venv\Scripts\python -m pytest tests/test_endpoints.py::test_store_anomalies_endpoint -v

# Run with coverage (install pytest-cov first)
pip install pytest-cov
.\venv\Scripts\python -m pytest --cov=app --cov-report=term-missing
```

**Expected output:**
```
tests/test_endpoints.py::test_health_endpoint                    PASSED
tests/test_endpoints.py::test_stores_endpoint                    PASSED
tests/test_endpoints.py::test_zones_endpoint                     PASSED
tests/test_endpoints.py::test_pos_transaction_endpoints          PASSED
tests/test_endpoints.py::test_live_pipeline_metrics_and_heatmap  PASSED
tests/test_endpoints.py::test_simulated_pipeline_metrics_and_heatmap PASSED
tests/test_endpoints.py::test_enhanced_health_endpoint           PASSED
tests/test_endpoints.py::test_store_anomalies_endpoint           PASSED
tests/test_endpoints.py::test_store_funnel_endpoint              PASSED
tests/test_endpoints.py::test_store_heatmap_confidence           PASSED
tests/test_ingestion.py::test_event_normalization                PASSED
tests/test_ingestion.py::test_idempotency_check                  PASSED
tests/test_ingestion.py::test_staff_event_routing                PASSED
tests/test_ingestion.py::test_customer_event_routing_and_pubsub  PASSED
tests/test_ingestion.py::test_customer_session_lifecycle         PASSED
tests/test_ingestion.py::test_api_ingest_event_endpoint          PASSED
tests/test_ingestion.py::test_api_batch_ingest_endpoint          PASSED
tests/test_websockets.py::test_websocket_connection_and_ping     PASSED
tests/test_websockets.py::test_websocket_event_broadcasting      PASSED

19 passed in ~1.1s
```

---

## 11. Camera & Zone Configuration

Camera roles and zone polygons are defined in `store-intelligence/pipeline/config/cameras.yaml`:

```yaml
stores:
  ST1076:             # Store 1 — Mumbai Central
    cameras:
      - id: CAM1      # Aisle zone coverage
        role: zone
      - id: CAM3      # Main entrance
        role: entry
      - id: CAM5      # Billing counter
        role: billing

  ST1008:             # Store 2 — Delhi SelectCitywalk
    cameras:
      - id: CAMA      # Billing area
        role: billing
      - id: CAMB      # Entry 1
        role: entry
      - id: CAMC      # Entry 2
        role: entry
      - id: CAMD      # Zone coverage
        role: zone
```

Zone polygons (in normalized 0.0–1.0 coordinate space) are seeded into MongoDB on first startup via `database.py`. Each zone has:
- `zone_id`: Identifier (Z01–Z07)
- `zone_name`: Human-readable name
- `zone_type`: `SHELF` | `DISPLAY` | `BILLING` | `ENTRANCE`
- `is_revenue_zone`: Whether the zone directly influences purchase decisions
- `geometry`: GeoJSON Polygon for spatial queries

---

## 12. Key Design Decisions

See [`docs/DESIGN.md`](docs/DESIGN.md) for full architecture rationale and [`docs/CHOICES.md`](docs/CHOICES.md) for the three most impactful technical decisions.

### Staff Exclusion — 5 Layers

Staff movement is filtered at every level to guarantee zero contamination of customer analytics:

1. **CV Pipeline** — HSV color check for store uniforms (Store 1: all-black; Store 2: pink/magenta) + velocity and zone-revisit behavioral filters → sets `is_staff: true`
2. **Ingestion Gate** — `is_staff: true` events route exclusively to `staff_tracks` collection
3. **Pub/Sub** — Staff events are never published to `live_tracks:{store_id}` channels
4. **Analytics Pipelines** — All aggregation queries include `{$match: {is_staff: {$ne: true}}}`
5. **Dashboard** — Metrics API responses contain only customer data

### Redis Fallback

The app runs with zero Redis configuration. When `UPSTASH_REDIS_REST_URL` is blank, an in-process `InMemoryPubSub` and key-value store handles all pub/sub and idempotency operations. This means the full platform — including live WebSocket tracking — works on a laptop with no network access.

### Simulated vs Live Mode

The backend serves metrics from two sources, in priority order:
1. **Pre-processed JSONL output** from `pipeline/data/output/{store_id}/` — instant responses, no DB needed
2. **Live MongoDB** — real-time aggregation queries from ingested events

This lets you demonstrate the full platform with rich data instantly (simulated mode) while also supporting real live deployments.
