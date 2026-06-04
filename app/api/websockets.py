import asyncio
import logging
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.pub_sub import pubsub_broker

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


@router.websocket("/ws/{store_id}")
async def websocket_endpoint(websocket: WebSocket, store_id: str):
    """
    Subscribes to the in-process Pub/Sub channel for the store
    and streams live tracking events to the dashboard client.
    """
    await websocket.accept()
    logger.info(f"WebSocket connected for store: {store_id}")

    channel = f"live_tracks:{store_id}"
    queue = pubsub_broker.subscribe(channel)

    # Background task: detect client disconnects by reading from the socket
    async def keep_alive():
        try:
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
        except (WebSocketDisconnect, Exception):
            pass

    keep_alive_task = asyncio.create_task(keep_alive())

    try:
        while True:
            if keep_alive_task.done():
                logger.info(f"WebSocket client disconnected for store: {store_id}")
                break

            # Wait for a message with a short timeout so we can check disconnects
            try:
                message = await asyncio.wait_for(queue.get(), timeout=0.5)
                await websocket.send_text(message)
            except asyncio.TimeoutError:
                pass  # No message yet — loop back and check keep_alive

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected cleanly: {store_id}")
    except Exception as e:
        logger.error(f"WebSocket error on store {store_id}: {e}")
    finally:
        keep_alive_task.cancel()
        pubsub_broker.unsubscribe(channel, queue)
        try:
            await websocket.close()
        except Exception:
            pass
