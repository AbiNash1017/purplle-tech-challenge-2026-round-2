import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import init_db, close_db_connections
from app.api.endpoints import router as api_router
from app.api.websockets import router as ws_router
from app.services.idle_monitor import idle_monitor_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    logger.info("Initializing databases...")
    try:
        await init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Critical: Failed to initialize database: {e}")
        
    # Start the idle monitor background service
    logger.info("Starting idle monitor...")
    await idle_monitor_service.start()
    
    yield
    
    # Shutdown actions
    logger.info("Stopping idle monitor...")
    await idle_monitor_service.stop()
    
    logger.info("Closing database connections...")
    await close_db_connections()
    logger.info("Application shutdown complete.")

app = FastAPI(
    title="Store Intelligence Platform API",
    description="Backend API for real-time tracking, metrics, and POS correlation",
    version="1.0.0",
    lifespan=lifespan
)

# Mount pipeline output videos directory for CCTV playback
output_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "store-intelligence", "pipeline", "data", "output"))
if os.path.isdir(output_dir):
    logger.info(f"Mounting pipeline output directory for videos at: {output_dir}")
    app.mount("/videos", StaticFiles(directory=output_dir), name="videos")
else:
    logger.warning(f"Pipeline output directory not found at: {output_dir}. CCTV footage streaming will not be available.")

# Configure CORS for Local Development and Production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to dashboard domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(api_router)
app.include_router(ws_router)

@app.get("/health")
async def health_check():
    """
    Enhanced service health check endpoint.
    Returns service status, last event timestamp per store, and STALE_FEED warnings for live stores.
    """
    db_status = "healthy"
    db = None
    last_event_timestamps = {}
    warnings = []
    
    try:
        from app.core.database import get_db_client
        db = get_db_client()
        await db.command("ping")
    except Exception as e:
        db_status = f"unreachable: {e}"
        warnings.append(f"DATABASE_ERROR: {e}")

    stores = ["ST1076", "ST1008"]
    from app.services.simulated_data import simulated_data_service
    
    now = datetime.now(timezone.utc)
    
    for store_id in stores:
        last_ts = None
        is_sim = simulated_data_service.has_data(store_id)
        
        if is_sim:
            try:
                events = simulated_data_service._load_events(store_id)
                if events:
                    max_ts = None
                    for e in events:
                        ts_str = e.get("timestamp")
                        if ts_str:
                            try:
                                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                if max_ts is None or dt > max_ts:
                                    max_ts = dt
                            except Exception:
                                pass
                    last_ts = max_ts
            except Exception:
                pass
        elif db is not None:
            try:
                latest = await db["spatial_events"].find_one(
                    {"store_id": store_id},
                    sort=[("timestamp", -1)]
                )
                if latest:
                    last_ts = latest.get("timestamp")
                    if isinstance(last_ts, str):
                        last_ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            except Exception:
                pass
                
        if last_ts:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            
            last_event_timestamps[store_id] = last_ts.isoformat()
            lag_seconds = (now - last_ts).total_seconds()
            
            if lag_seconds > 600 and not is_sim:
                lag_mins = lag_seconds / 60.0
                warnings.append(f"STALE_FEED: Store {store_id} feed lag is {lag_mins:.1f} minutes")
        else:
            last_event_timestamps[store_id] = None
            if not is_sim:
                warnings.append(f"STALE_FEED: No events received yet for store {store_id}")
                
    status = "healthy"
    if db_status != "healthy" or warnings:
        status = "degraded"
        
    return {
        "status": status,
        "database": db_status,
        "mode": settings.EXECUTION_MODE,
        "last_event_timestamps": last_event_timestamps,
        "warnings": warnings
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
