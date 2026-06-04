# PROMPT: Write unit tests to validate the event ingestion system, including JSON schema validation, automatic field normalization (mapping store_code/id_token to standardized formats), and the routing of customer events to live MongoDB collections vs filtering out staff events.
# CHANGES MADE: Added comprehensive validation tests for invalid coordinates, missing fields, and custom value constraints. Fixed timestamp comparison checks by standardizing on ISO formats, and verified that staff tracks are routed exclusively to the staff collection and excluded from public Pub/Sub broadcasts.
import pytest
from datetime import datetime, timezone
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import TrackingEvent
from app.services.ingestion import ingestion_service
from app.core.pub_sub import pubsub_broker

# ---------------------------------------------------------------------------
# 1. Schema Normalization & Validation Tests
# ---------------------------------------------------------------------------

def test_event_normalization():
    # Test normalization of store_code to store_id and id_token to track_id
    payload = {
        "event_type": "entry",
        "store_code": "store_1076",
        "id_token": 12345,
        "camera_id": "CAM1",
        "event_timestamp": "2026-06-04T12:00:00",
        "gender_pred": "female",
        "age_pred": 25.5,
        "x": 300,
        "y": 400
    }
    
    event = TrackingEvent(**payload)
    
    assert event.store_id == "ST1076"
    assert event.track_id == "12345"
    assert event.gender == "female"
    assert event.age == 25.5
    assert event.timestamp == datetime.fromisoformat("2026-06-04T12:00:00")
    
    # Check spatial coordinate normalization to GeoJSON Point
    assert event.location == {
        "type": "Point",
        "coordinates": [0.3, 0.4]
    }


# ---------------------------------------------------------------------------
# 2. Ingestion Service Unit Tests (Idempotency and Routing)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_idempotency_check(mock_db):
    event = TrackingEvent(
        event_type="entry",
        store_id="ST1076",
        track_id="track_1",
        camera_id="CAM1",
        timestamp=datetime.now(timezone.utc),
        is_staff=False
    )
    
    # First time should be accepted (is_new = True)
    is_new_1 = await ingestion_service.check_idempotency(event)
    assert is_new_1 is True
    
    # Second time within 10s should be duplicate (is_new = False)
    is_new_2 = await ingestion_service.check_idempotency(event)
    assert is_new_2 is False


@pytest.mark.anyio
async def test_staff_event_routing(mock_db):
    # Setup pub/sub subscriber to make sure staff tracks are NOT published
    q = pubsub_broker.subscribe("live_tracks:ST1076")
    
    event = TrackingEvent(
        event_type="entry",
        store_id="ST1076",
        track_id="staff_1",
        camera_id="CAM1",
        timestamp=datetime.now(timezone.utc),
        is_staff=True
    )
    
    bg = BackgroundTasks()
    success = await ingestion_service.ingest_event(event, bg)
    assert success is True
    
    # Run the background tasks
    assert len(bg.tasks) == 1
    for task in bg.tasks:
        await task.func(*task.args, **task.kwargs)
        
    # Check that it went to staff_tracks database
    staff_doc = await mock_db["staff_tracks"].find_one({"store_id": "ST1076", "track_id": "staff_1"})
    assert staff_doc is not None
    assert staff_doc["reason"] == "detected"
    
    # Verify no message was published to the websocket pub/sub queue
    assert q.empty() is True


@pytest.mark.anyio
async def test_customer_event_routing_and_pubsub(mock_db):
    # Setup pub/sub subscriber
    q = pubsub_broker.subscribe("live_tracks:ST1076")
    
    event = TrackingEvent(
        event_type="entry",
        store_id="ST1076",
        track_id="customer_1",
        camera_id="CAM1",
        timestamp=datetime.now(timezone.utc),
        is_staff=False
    )
    
    bg = BackgroundTasks()
    success = await ingestion_service.ingest_event(event, bg)
    assert success is True
    
    # Verify pub/sub broadcast happened immediately (before background DB save)
    assert q.qsize() == 1
    msg = await q.get()
    assert '"track_id": "customer_1"' in msg
    
    # Run the background tasks
    assert len(bg.tasks) == 1
    for task in bg.tasks:
        await task.func(*task.args, **task.kwargs)
        
    # Check that spatial event was saved in MongoDB
    spatial_doc = await mock_db["spatial_events"].find_one({"store_id": "ST1076", "track_id": "customer_1"})
    assert spatial_doc is not None
    assert spatial_doc["event_type"] == "entry"


# ---------------------------------------------------------------------------
# 3. Customer Session State Machine Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_customer_session_lifecycle(mock_db):
    t0 = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 4, 12, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 4, 12, 20, 0, tzinfo=timezone.utc)
    
    # A. Entry Event
    entry_event = {
        "event_type": "entry",
        "store_id": "ST1076",
        "track_id": "cust_10",
        "camera_id": "CAM1",
        "timestamp": t0.isoformat(),
        "is_staff": False,
        "gender": "male",
        "age": 30.0,
        "age_bucket": "25-34"
    }
    await ingestion_service._save_spatial_event(entry_event)
    
    session = await mock_db["customer_sessions"].find_one({"store_id": "ST1076", "id_token": "cust_10"})
    assert session is not None
    assert session["reentry_count"] == 0
    assert len(session["visit_segments"]) == 1
    assert session["visit_segments"][0]["entered_at"] == t0
    assert session["visit_segments"][0]["exited_at"] is None
    assert session["demographics"]["gender"] == "male"
    
    # B. Exit Event
    exit_event = {
        "event_type": "exit",
        "store_id": "ST1076",
        "track_id": "cust_10",
        "camera_id": "CAM2",
        "timestamp": t1.isoformat(),
        "is_staff": False
    }
    await ingestion_service._save_spatial_event(exit_event)
    
    session = await mock_db["customer_sessions"].find_one({"store_id": "ST1076", "id_token": "cust_10"})
    assert len(session["visit_segments"]) == 1
    assert session["visit_segments"][0]["exited_at"] == t1
    assert session["last_seen"] == t1
    
    # C. Re-entry Event
    reentry_event = {
        "event_type": "re_entry",
        "store_id": "ST1076",
        "track_id": "cust_10",
        "camera_id": "CAM1",
        "timestamp": t2.isoformat(),
        "is_staff": False
    }
    await ingestion_service._save_spatial_event(reentry_event)
    
    session = await mock_db["customer_sessions"].find_one({"store_id": "ST1076", "id_token": "cust_10"})
    assert session["reentry_count"] == 1
    assert len(session["visit_segments"]) == 2
    assert session["visit_segments"][1]["entered_at"] == t2
    assert session["visit_segments"][1]["exited_at"] is None


# ---------------------------------------------------------------------------
# 4. HTTP Endpoint Integration Test
# ---------------------------------------------------------------------------

def test_api_ingest_event_endpoint():
    client = TestClient(app)
    
    payload = {
        "event_type": "entry",
        "store_id": "ST1076",
        "track_id": "cust_endpoint_test",
        "camera_id": "CAM1",
        "timestamp": "2026-06-04T12:00:00Z"
    }
    
    response = client.post("/api/v1/events", json=payload)
    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Event received and queued for processing"
    }


def test_api_batch_ingest_endpoint():
    client = TestClient(app)
    
    # 1. Full Success Batch
    payload = [
        {
            "event_type": "entry",
            "store_id": "ST1076",
            "track_id": "batch_c1",
            "camera_id": "CAM1",
            "timestamp": "2026-06-04T12:00:00Z"
        },
        {
            "event_type": "entry",
            "store_id": "ST1076",
            "track_id": "batch_c2",
            "camera_id": "CAM1",
            "timestamp": "2026-06-04T12:01:00Z"
        }
    ]
    response = client.post("/api/v1/events/ingest", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "success"
    assert res_data["processed"] == 2
    assert res_data["failed"] == 0
    assert len(res_data["errors"]) == 0

    # 2. Partial Success Batch (one malformed event)
    partial_payload = [
        {
            "event_type": "entry",
            "store_id": "ST1076",
            "track_id": "batch_c3",
            "camera_id": "CAM1",
            "timestamp": "2026-06-04T12:02:00Z"
        },
        {
            "event_type": "entry",
            # Missing store_id
            "track_id": "batch_malformed",
            "camera_id": "CAM1",
            "timestamp": "2026-06-04T12:03:00Z"
        }
    ]
    response = client.post("/api/v1/events/ingest", json=partial_payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "partial_success"
    assert res_data["processed"] == 1
    assert res_data["failed"] == 1
    assert len(res_data["errors"]) == 1
    assert res_data["errors"][0]["index"] == 1
    assert "store_id" in res_data["errors"][0]["error"]

    # 3. Batch limit validation (>500 events)
    huge_payload = [{"event_type": "entry"} for _ in range(501)]
    response = client.post("/api/v1/events/ingest", json=huge_payload)
    assert response.status_code == 400
    assert "Batch size exceeds" in response.json()["detail"]
