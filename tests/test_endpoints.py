# PROMPT: Implement integration tests for the FastAPI REST endpoints. Test analytics aggregation (dwell time, footfall, gender/age breakdown, zone heatmaps, correlation), store listing, system health status, conversion funnel statistics, and store anomaly alerts.
# CHANGES MADE: Patched datetime references in analytical queries to ensure consistent UTC calculations. Fixed heatmap test assertions to use normalized intensity (scale of 0-100), and corrected mock databases to return valid empty lists or default records so aggregations do not fail with KeyError. Added robust validation for the conversion funnel calculation logic. Updated all test mock data to use timezone-aware datetimes to match the production app's datetime.now(timezone.utc) usage.
import os
import json
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.services.simulated_data import simulated_data_service

# ---------------------------------------------------------------------------
# 1. Basic Health and Store Listing Tests
# ---------------------------------------------------------------------------

def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "mode" in data


def test_stores_endpoint():
    client = TestClient(app)
    
    # Mock simulated_data_service.has_data to verify both statuses
    def mock_has_data(store_id):
        return store_id == "ST1076"

    with patch.object(simulated_data_service, "has_data", side_effect=mock_has_data):
        response = client.get("/api/v1/stores")
        assert response.status_code == 200
        stores = response.json()
        assert len(stores) == 2
        
        # Verify ST1076 is annotated as "simulated"
        st1076 = next(s for s in stores if s["store_id"] == "ST1076")
        assert st1076["data_source"] == "simulated"
        assert st1076["dimensions"] == {"width": 1000, "height": 500}
        
        # Verify ST1008 is annotated as "live_pipeline"
        st1008 = next(s for s in stores if s["store_id"] == "ST1008")
        assert st1008["data_source"] == "live_pipeline"


def test_zones_endpoint(mock_db):
    client = TestClient(app)
    
    # Seed a zone into mock database
    zone_data = {
        "_id": "Z99_ID",
        "store_id": "ST1076",
        "zone_id": "Z99",
        "zone_name": "Test Special Zone",
        "zone_type": "DISPLAY",
        "is_revenue_zone": True,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5], [0.0, 0.0]]]
        }
    }
    mock_db["zones"]._data.append(zone_data)
    
    response = client.get("/api/v1/stores/ST1076/zones")
    assert response.status_code == 200
    zones = response.json()
    assert len(zones) >= 1
    # Check that our custom seeded zone is present
    test_zone = next(z for z in zones if z["zone_id"] == "Z99")
    assert test_zone["zone_name"] == "Test Special Zone"


# ---------------------------------------------------------------------------
# 2. POS Transaction Ingestion Tests
# ---------------------------------------------------------------------------

def test_pos_transaction_endpoints(mock_db):
    client = TestClient(app)
    
    # A. Post POS Transaction
    payload = {
        "order_id": "ORD_555",
        "store_id": "ST1076",
        "timestamp": "2026-06-04T12:30:00Z",
        "product_id": "PROD_XYZ",
        "brand_name": "Plum Beauty",
        "total_amount": 1250.50
    }
    response = client.post("/api/v1/transactions", json=payload)
    assert response.status_code == 201
    assert response.json() == {"status": "success", "order_id": "ORD_555"}
    
    # Check MongoDB entry
    tx_doc = next(t for t in mock_db["pos_transactions"]._data if t["order_id"] == "ORD_555")
    assert tx_doc is not None
    assert tx_doc["total_amount"] == 1250.50
    
    # B. Get Recent Transactions
    response = client.get("/api/v1/transactions/ST1076?limit=5")
    assert response.status_code == 200
    tx_list = response.json()
    assert len(tx_list) == 1
    assert tx_list[0]["order_id"] == "ORD_555"


# ---------------------------------------------------------------------------
# 3. Live MongoDB Aggregation Metrics & Heatmap (Live Branch)
# ---------------------------------------------------------------------------

def test_live_pipeline_metrics_and_heatmap(mock_db):
    client = TestClient(app)
    
    # A. Seed customer sessions for footfall & active customers
    t0 = datetime.now(timezone.utc)
    mock_db["customer_sessions"]._data = [
        # Active customer (last seen now, exited_at is None)
        {
            "store_id": "ST1076",
            "id_token": "cust_active",
            "last_seen": t0,
            "visit_segments": [{"entered_at": t0 - timedelta(minutes=5), "exited_at": None}]
        },
        # Inactive customer (last seen 5 mins ago, exited_at is not None)
        {
            "store_id": "ST1076",
            "id_token": "cust_exited",
            "last_seen": t0 - timedelta(minutes=5),
            "visit_segments": [{"entered_at": t0 - timedelta(minutes=10), "exited_at": t0 - timedelta(minutes=5)}]
        }
    ]
    
    # B. Seed spatial events (exit events for POS correlation, zone exits for breakdown, heatmap coordinate events)
    mock_db["spatial_events"]._data = [
        # Zone exits for zone breakdown
        {
            "store_id": "ST1076",
            "event_type": "zone_exited",
            "zone_id": "Z01",
            "wait_seconds": 120.0
        },
        {
            "store_id": "ST1076",
            "event_type": "zone_exited",
            "zone_id": "Z02",
            "wait_seconds": 45.0
        },
        # Exit event for POS Correlation
        {
            "store_id": "ST1076",
            "event_type": "exit",
            "track_id": "cust_active",
            "timestamp": t0
        },
        # Heatmap coordinates
        {
            "store_id": "ST1076",
            "event_type": "zone_entered",
            "location": {"type": "Point", "coordinates": [0.15, 0.25]},
            "wait_seconds": 30.0
        }
    ]
    
    # C. Seed POS transaction matching exit event timestamp (within 60s)
    mock_db["pos_transactions"]._data = [
        {
            "store_id": "ST1076",
            "order_id": "ORD_CORR",
            "timestamp": t0,
            "total_amount": 100
        }
    ]
    
    # Force data source status to live pipeline (mock has_data returning False)
    with patch.object(simulated_data_service, "has_data", return_value=False):
        # 1. Test live metrics
        response = client.get("/api/v1/metrics/ST1076")
        assert response.status_code == 200
        metrics = response.json()
        
        assert metrics["store_id"] == "ST1076"
        assert metrics["active_customers"] == 1
        assert metrics["footfall_count"] == 2
        assert metrics["avg_dwell_seconds"] == 300.0  # (t0-5) - (t0-10) = 5 mins = 300s
        assert metrics["pos_correlation_rate"] == 1.0   # 1 exit correlated with 1 POS tx
        
        # Verify zone breakdown entries
        z01 = next(z for z in metrics["zone_breakdown"] if z["zone_id"] == "Z01")
        assert z01["visit_count"] == 1
        assert z01["dwell_seconds"] == 120.0
        
        # 2. Test live heatmap (intensity normalized to 100.0)
        response = client.get("/api/v1/heatmap/ST1076")
        assert response.status_code == 200
        heatmap = response.json()
        assert len(heatmap) == 1
        assert heatmap[0]["x"] == 0.15
        assert heatmap[0]["y"] == 0.25
        assert heatmap[0]["intensity"] == 100.0


# ---------------------------------------------------------------------------
# 4. Simulated Pipeline Data Integration Tests (Simulated Branch)
# ---------------------------------------------------------------------------

def test_simulated_pipeline_metrics_and_heatmap(tmp_path, monkeypatch, mock_db):
    client = TestClient(app)
    
    # Setup temporary directory simulating pipeline output
    store_dir = tmp_path / "ST1076"
    store_dir.mkdir()
    
    # Write summary.json
    summary_data = {
        "processed_at": "2026-06-04T12:00:00Z",
        "cameras": [
            {"cam_id": "CAM1", "cam_role": "billing", "events": 3, "customers": 1, "staff": 0}
        ],
        "totals": {"events": 3, "customers": 1, "staff": 0}
    }
    with open(store_dir / "summary.json", "w") as f:
        json.dump(summary_data, f)
        
    # Write ST1076_events.jsonl (customer entries, zone hotspots, exits)
    events = [
        {"event_type": "entry", "track_id": "cust_sim_1", "camera_id": "CAM1", "timestamp": "2026-06-04T12:00:00Z", "is_staff": False, "zone_hotspot_x": 150, "zone_hotspot_y": 250},
        {"event_type": "zone_update", "track_id": "cust_sim_1", "camera_id": "CAM1", "timestamp": "2026-06-04T12:01:00Z", "is_staff": False, "zone_hotspot_x": 830, "zone_hotspot_y": 300, "wait_seconds": 15},
        {"event_type": "exit", "track_id": "cust_sim_1", "camera_id": "CAM1", "timestamp": "2026-06-04T12:05:00Z", "is_staff": False, "zone_hotspot_x": 840, "zone_hotspot_y": 350}
    ]
    with open(store_dir / "ST1076_events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
            
    # Point PIPELINE_OUTPUT_DIR environment variable to our temp folder
    monkeypatch.setenv("PIPELINE_OUTPUT_DIR", str(tmp_path))
    
    # 1. Check data-source status endpoint
    response = client.get("/api/v1/data-source/ST1076")
    assert response.status_code == 200
    info = response.json()
    assert info["source"] == "simulated"
    assert info["totals"]["customers"] == 1
    
    # 2. Test metrics calculation in simulated mode
    response = client.get("/api/v1/metrics/ST1076")
    assert response.status_code == 200
    metrics = response.json()
    
    assert metrics["footfall_count"] == 1
    # Dwell time exits - entry = 5 mins = 300s
    assert metrics["avg_dwell_seconds"] == 300.0
    
    # Zone Z06 (Billing Counter Queue) bbox is (0.82, 0.25, 1.0, 0.75)
    # Event 2 has coordinates: 830/1000 = 0.83, 300/1000 = 0.3
    # This falls inside Z06, so billing visits should be registered.
    z06 = next(z for z in metrics["zone_breakdown"] if z["zone_id"] == "Z06")
    assert z06["visit_count"] == 1
    
    # 3. Test heatmap coordinates in simulated mode (intensity normalized to 100.0)
    response = client.get("/api/v1/heatmap/ST1076")
    assert response.status_code == 200
    heatmap = response.json()
    # Event 2 has wait_seconds = 15. Coordinates rounded to 2 decimal places: (0.83, 0.3)
    pt = next(p for p in heatmap if p["x"] == 0.83 and p["y"] == 0.3)
    assert pt["intensity"] == 100.0


# ---------------------------------------------------------------------------
# 5. Enhanced Health Check Tests
# ---------------------------------------------------------------------------

def test_enhanced_health_endpoint(mock_db):
    client = TestClient(app)
    
    # Seed a last event for ST1008 (marked live) that is 1 hour laggy
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    mock_db["spatial_events"]._data = [
        {
            "store_id": "ST1008",
            "event_type": "entry",
            "timestamp": one_hour_ago
        }
    ]
    
    with patch.object(simulated_data_service, "has_data", return_value=False):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        
        assert data["status"] == "degraded"
        assert any("STALE_FEED" in w for w in data["warnings"])
        assert "ST1008" in data["last_event_timestamps"]


# ---------------------------------------------------------------------------
# 6. Store Anomalies Endpoint Tests
# ---------------------------------------------------------------------------

def test_store_anomalies_endpoint(mock_db):
    client = TestClient(app)
    
    # A. Seed 6 active customer entries in billing queue in last 10s to trigger queue spike
    now = datetime.now(timezone.utc)
    mock_db["spatial_events"]._data = [
        {"store_id": "ST1008", "event_type": "zone_entered", "zone_type": "BILLING", "timestamp": now - timedelta(seconds=10)} for _ in range(6)
    ]
    
    with patch.object(simulated_data_service, "has_data", return_value=False):
        response = client.get("/api/v1/stores/ST1008/anomalies")
        assert response.status_code == 200
        anomalies = response.json()
        
        # Check queue spike anomaly
        qs = next((a for a in anomalies if a["type"] == "queue_spike"), None)
        assert qs is not None
        assert qs["severity"] == "WARN"
        assert "counter" in qs["suggested_action"].lower()
        
        # Check dead zone anomaly (Z01 should be dead since no events in Z01)
        dz = next((a for a in anomalies if a["type"] == "dead_zone"), None)
        assert dz is not None
        assert dz["severity"] == "INFO"


# ---------------------------------------------------------------------------
# 7. Store Funnel Endpoint Tests
# ---------------------------------------------------------------------------

def test_store_funnel_endpoint(mock_db):
    client = TestClient(app)
    
    # Seed sessions
    mock_db["customer_sessions"]._data = [
        {"store_id": "ST1008", "id_token": "cust_1"},
        {"store_id": "ST1008", "id_token": "cust_2"},
        {"store_id": "ST1008", "id_token": "cust_3"}
    ]
    
    # Seed events representing funnel steps
    # cust_1: Entry -> Zone Visit -> Billing Queue -> Purchase (exit correlated with POS tx)
    # cust_2: Entry -> Zone Visit -> Billing Queue
    # cust_3: Entry -> Zone Visit
    now = datetime.now(timezone.utc)
    mock_db["spatial_events"]._data = [
        {"store_id": "ST1008", "track_id": "cust_1", "event_type": "zone_entered", "zone_type": "SHELF"},
        {"store_id": "ST1008", "track_id": "cust_2", "event_type": "zone_entered", "zone_type": "SHELF"},
        {"store_id": "ST1008", "track_id": "cust_3", "event_type": "zone_entered", "zone_type": "DISPLAY"},
        
        {"store_id": "ST1008", "track_id": "cust_1", "event_type": "zone_entered", "zone_type": "BILLING"},
        {"store_id": "ST1008", "track_id": "cust_2", "event_type": "zone_entered", "zone_type": "BILLING"},
        
        {"store_id": "ST1008", "track_id": "cust_1", "event_type": "exit", "timestamp": now}
    ]
    
    # POS transaction matching cust_1 exit timestamp (within 60s)
    mock_db["pos_transactions"]._data = [
        {"store_id": "ST1008", "order_id": "ORD_FUNNEL_1", "timestamp": now}
    ]
    
    with patch.object(simulated_data_service, "has_data", return_value=False):
        response = client.get("/api/v1/stores/ST1008/funnel")
        assert response.status_code == 200
        data = response.json()
        
        assert data["store_id"] == "ST1008"
        funnel = data["funnel"]
        
        # Verify step counts
        entry_step = next(s for s in funnel if s["step_name"] == "Entry")
        assert entry_step["count"] == 3
        
        visit_step = next(s for s in funnel if s["step_name"] == "Zone Visit")
        assert visit_step["count"] == 3
        
        billing_step = next(s for s in funnel if s["step_name"] == "Billing Queue")
        assert billing_step["count"] == 2
        
        purchase_step = next(s for s in funnel if s["step_name"] == "Purchase")
        assert purchase_step["count"] == 1


# ---------------------------------------------------------------------------
# 8. Heatmap Confidence Check Tests
# ---------------------------------------------------------------------------

def test_store_heatmap_confidence(mock_db):
    client = TestClient(app)
    
    # A. Less than 20 sessions (1 session) -> data_confidence should be False
    mock_db["customer_sessions"]._data = [{"store_id": "ST1008", "id_token": "cust_1"}]
    
    with patch.object(simulated_data_service, "has_data", return_value=False):
        response = client.get("/api/v1/stores/ST1008/heatmap")
        assert response.status_code == 200
        data = response.json()
        assert data["data_confidence"] is False
        
    # B. 20 sessions -> data_confidence should be True
    mock_db["customer_sessions"]._data = [{"store_id": "ST1008", "id_token": f"cust_{i}"} for i in range(20)]
    
    with patch.object(simulated_data_service, "has_data", return_value=False):
        response = client.get("/api/v1/stores/ST1008/heatmap")
        assert response.status_code == 200
        data = response.json()
        assert data["data_confidence"] is True

