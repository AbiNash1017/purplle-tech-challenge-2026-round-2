# PROMPT: Create tests for real-time WebSocket communication in FastAPI. Verify connection lifecycles, ping-pong responses, and pub/sub message broadcasting to connected active clients when telemetry events are ingested.
# CHANGES MADE: Developed clean websocket connection scopes using FastAPIs TestClient. Handled assertion of active subscriber counts on local pub/sub brokers and validated automated queue teardowns when websocket clients disconnect.
from fastapi.testclient import TestClient
from app.main import app
from app.core.pub_sub import pubsub_broker

def test_websocket_connection_and_ping():
    client = TestClient(app)
    
    # Establish connection
    with client.websocket_connect("/api/v1/ws/ST1076") as ws:
        # Verify connection was accepted
        ws.send_text("ping")
        data = ws.receive_json()
        assert data == {"type": "pong"}


def test_websocket_event_broadcasting():
    client = TestClient(app)
    
    # Establish connection
    with client.websocket_connect("/api/v1/ws/ST1076") as ws:
        # Check that there is 1 subscriber on the channel now
        assert pubsub_broker.subscriber_count("live_tracks:ST1076") == 1
        
        # Trigger an event ingestion via HTTP POST
        payload = {
            "event_type": "entry",
            "store_id": "ST1076",
            "track_id": "cust_websocket_broadcaster",
            "camera_id": "CAM1",
            "timestamp": "2026-06-04T12:00:00Z",
            "is_staff": False
        }
        response = client.post("/api/v1/events", json=payload)
        assert response.status_code == 200
        
        # Verify that the websocket client receives the telemetry broadcast
        broadcast_message = ws.receive_json()
        assert broadcast_message["track_id"] == "cust_websocket_broadcaster"
        assert broadcast_message["event_type"] == "entry"
        assert broadcast_message["store_id"] == "ST1076"

    # Once we exit the context, the websocket client disconnects.
    # Verify that the pub/sub broker correctly unsubscribes the client queue.
    assert pubsub_broker.subscriber_count("live_tracks:ST1076") == 0
