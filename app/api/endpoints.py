from fastapi import APIRouter, HTTPException, BackgroundTasks, status
from typing import List, Dict, Any
from datetime import datetime, timezone
from app.models.schemas import (
    TrackingEvent, POSTransaction, MetricsResponse, ZoneHeatmapPoint,
    AnomalyDetail, FunnelResponse, FunnelStep, HeatmapResponse,
    BatchIngestResponse, BatchIngestError
)
from app.services.anomalies import anomalies_service
from app.services.ingestion import ingestion_service
from app.services.analytics import analytics_service
from app.services.simulated_data import simulated_data_service
from app.core.database import get_db_client
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Helper: simulated-first data routing
# ---------------------------------------------------------------------------

async def _get_metrics(store_id: str) -> MetricsResponse:
    """
    Returns metrics from the pipeline output if available,
    otherwise falls back to the live MongoDB analytics service.
    """
    if simulated_data_service.has_data(store_id):
        logger.info(f"[DataRouter] Serving simulated data for store {store_id}")
        result = await simulated_data_service.get_store_metrics(store_id)
        if result is not None:
            return result
        logger.warning(f"[DataRouter] Simulated data available but compute failed for {store_id}, falling back to pipeline.")

    logger.info(f"[DataRouter] Serving live pipeline data for store {store_id}")
    return await analytics_service.get_store_metrics(store_id)


async def _get_heatmap(store_id: str) -> List[Dict[str, Any]]:
    """
    Returns heatmap points from pipeline output if available,
    otherwise falls back to the live MongoDB analytics service.
    """
    if simulated_data_service.has_data(store_id):
        logger.info(f"[DataRouter] Serving simulated heatmap for store {store_id}")
        result = await simulated_data_service.get_zone_heatmap(store_id)
        if result:
            return result
        logger.warning(f"[DataRouter] Simulated heatmap empty for {store_id}, falling back to pipeline.")

    logger.info(f"[DataRouter] Serving live pipeline heatmap for store {store_id}")
    return await analytics_service.get_zone_heatmap(store_id)


# ---------------------------------------------------------------------------
# Event ingestion
# ---------------------------------------------------------------------------

@router.post("/events", status_code=status.HTTP_200_OK)
async def ingest_event(event: TrackingEvent, background_tasks: BackgroundTasks):
    """
    Ingests live visual telemetry. Performs idempotency check using Redis,
    routes staff tracks to exclusion storage, publishes live customer tracks 
    to Redis Pub/Sub, and inserts events into MongoDB in the background.
    """
    try:
        success = await ingestion_service.ingest_event(event, background_tasks)
        if not success:
            raise HTTPException(status_code=400, detail="Event ingestion failed")
        return {"status": "success", "message": "Event received and queued for processing"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@router.get("/metrics/{store_id}", response_model=MetricsResponse)
async def get_store_metrics(store_id: str):
    """
    Retrieves aggregated metrics for the store.

    **Data source priority:**
    1. Pre-processed pipeline output (JSONL files) — if available for this store.
    2. Live MongoDB ingestion pipeline — fallback when no processed data exists.

    Metrics include: active customers, footfall, average dwell time,
    queue wait/abandon rates, queue depth, POS correlation, and zone breakdown.
    """
    try:
        return await _get_metrics(store_id)
    except Exception as e:
        logger.error(f"Error fetching metrics for {store_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------

@router.get("/heatmap/{store_id}", response_model=List[Dict[str, Any]])
async def get_store_heatmap(store_id: str):
    """
    Retrieves coordinate points and dwell intensity weights for heatmap rendering.
    Weights normalized 0-100.
    """
    try:
        raw_points = await _get_heatmap(store_id)
        max_intensity = max([float(p.get("intensity", 0)) for p in raw_points]) if raw_points else 0.0
        normalized_points = []
        for p in raw_points:
            pt_copy = dict(p)
            intensity = float(p.get("intensity", 0))
            pt_copy["intensity"] = round((intensity / max_intensity) * 100.0, 2) if max_intensity > 0 else 0.0
            normalized_points.append(pt_copy)
        return normalized_points
    except Exception as e:
        logger.error(f"Error fetching heatmap for {store_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Store listing
# ---------------------------------------------------------------------------

@router.get("/stores", response_model=List[Dict[str, Any]])
async def get_stores():
    """
    Returns store configurations and layout file names.
    Annotates each store with its current data source (simulated / live_pipeline).
    """
    stores = [
        {
            "store_id": "ST1076",
            "name": "Store 1 - Mumbai Central",
            "layout_image": "Store 1 - layout.png",
            "dimensions": {"width": 1000, "height": 500}
        },
        {
            "store_id": "ST1008",
            "name": "Store 2 - Delhi SelectCitywalk",
            "layout_image": "store 2 - layout.png",
            "dimensions": {"width": 900, "height": 1130}
        }
    ]

    # Annotate data source
    for store in stores:
        sid = store["store_id"]
        store["data_source"] = (
            "simulated" if simulated_data_service.has_data(sid) else "live_pipeline"
        )

    return stores


# ---------------------------------------------------------------------------
# Data source status
# ---------------------------------------------------------------------------

@router.get("/data-source/{store_id}", response_model=Dict[str, Any])
async def get_data_source(store_id: str):
    """
    Returns the current data source for a store, including pipeline metadata
    when simulated output is active.

    Response fields:
    - `source`: `"simulated"` | `"live_pipeline"`
    - `processed_at`: ISO timestamp of when pipeline output was generated (simulated only)
    - `cameras`: per-camera stats (simulated only)
    - `totals`: aggregate event/customer/staff totals (simulated only)
    """
    return simulated_data_service.get_data_source_info(store_id)


@router.get("/data-sources", response_model=List[Dict[str, Any]])
async def get_all_data_sources():
    """
    Returns data source status for all known stores.
    Useful for dashboard status indicators.
    """
    store_ids = ["ST1076", "ST1008"]
    return [simulated_data_service.get_data_source_info(sid) for sid in store_ids]


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------

@router.get("/stores/{store_id}/zones")
async def get_store_zones(store_id: str):
    """
    Retrieves all predefined physical zone boundaries (polygons) for overlay rendering.
    """
    try:
        db = get_db_client()
        zones_cursor = db["zones"].find({"store_id": store_id})
        zones = await zones_cursor.to_list(length=100)
        # Convert _id to string for JSON serialization
        for z in zones:
            z["_id"] = str(z["_id"])
        return zones
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POS Transactions
# ---------------------------------------------------------------------------

@router.post("/transactions", status_code=status.HTTP_201_CREATED)
async def create_transaction(transaction: POSTransaction):
    """
    Ingests a POS transaction to correlate with queue exits.
    """
    try:
        db = get_db_client()
        t_dict = transaction.model_dump()
        await db["pos_transactions"].update_one(
            {"order_id": transaction.order_id},
            {"$set": t_dict},
            upsert=True
        )
        return {"status": "success", "order_id": transaction.order_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/transactions/{store_id}", response_model=List[POSTransaction])
async def get_recent_transactions(store_id: str, limit: int = 10):
    """
    Returns the most recent POS transactions for a specific store.
    """
    try:
        db = get_db_client()
        cursor = db["pos_transactions"].find({"store_id": store_id}).sort("timestamp", -1).limit(limit)
        results = await cursor.to_list(limit)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Batch Ingestion
# ---------------------------------------------------------------------------

@router.post("/events/ingest", response_model=BatchIngestResponse)
async def ingest_events_batch(events: List[Dict[str, Any]], background_tasks: BackgroundTasks):
    """
    Ingests a batch of visual telemetry events (up to 500).
    Provides structured response supporting partial successes on malformed items.
    """
    if len(events) > 500:
        raise HTTPException(status_code=400, detail="Batch size exceeds limit of 500 events")
        
    processed = 0
    failed = 0
    errors = []
    
    for idx, event_data in enumerate(events):
        try:
            event = TrackingEvent(**event_data)
            success = await ingestion_service.ingest_event(event, background_tasks)
            if success:
                processed += 1
            else:
                failed += 1
                errors.append(BatchIngestError(index=idx, error="Event ingestion failed"))
        except Exception as e:
            failed += 1
            errors.append(BatchIngestError(index=idx, error=str(e)))
            
    status_str = "success"
    if failed > 0:
        status_str = "failed" if processed == 0 else "partial_success"
        
    return BatchIngestResponse(
        status=status_str,
        processed=processed,
        failed=failed,
        errors=errors
    )


# ---------------------------------------------------------------------------
# Store metrics (Alias / Redirect)
# ---------------------------------------------------------------------------

@router.get("/stores/{store_id}/metrics", response_model=MetricsResponse)
async def get_store_metrics_alias(store_id: str):
    """
    Retrieves store metrics for specified store_id.
    """
    try:
        return await _get_metrics(store_id)
    except Exception as e:
        logger.error(f"Error fetching metrics for {store_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Store anomalies
# ---------------------------------------------------------------------------

@router.get("/stores/{store_id}/anomalies", response_model=List[AnomalyDetail])
async def get_store_anomalies(store_id: str):
    """
    Scans for active anomalies (queue spikes, conversion drops, dead zones) at the store.
    """
    try:
        return await anomalies_service.get_store_anomalies(store_id)
    except Exception as e:
        logger.error(f"Error computing store anomalies for {store_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Store funnel
# ---------------------------------------------------------------------------

@router.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
async def get_store_funnel(store_id: str):
    """
    Returns conversion funnel for the store: Entry -> Zone Visit -> Billing Queue -> Purchase.
    """
    try:
        if simulated_data_service.has_data(store_id):
            events = simulated_data_service._load_events(store_id)
            cust_events = [e for e in events if not e.get("is_staff", False)]
            
            all_tracks = {e["track_id"] for e in cust_events if e.get("track_id")}
            count_entry = len(all_tracks)
            
            shelf_display_tracks = {
                e["track_id"] for e in cust_events 
                if (e.get("zone_type") in ["SHELF", "DISPLAY"] or e.get("zone_id") in ["Z01", "Z02", "Z03", "Z04", "Z05"]) and e.get("track_id")
            }
            count_zone_visit = len(shelf_display_tracks)
            
            billing_tracks = {
                e["track_id"] for e in cust_events 
                if (e.get("zone_type") == "BILLING" or e.get("zone_id") == "Z06") and e.get("track_id")
            }
            count_billing = len(billing_tracks)
            
            purchase_tracks = {
                e["track_id"] for e in cust_events
                if (e.get("event_type") == "queue_completed" or (e.get("event_type") == "exit" and e.get("camera_id") in ["CAM5", "CAMA"])) and e.get("track_id")
            }
            count_purchase = len(purchase_tracks)
        else:
            db = get_db_client()
            count_entry = await db["customer_sessions"].count_documents({"store_id": store_id})
            
            zone_visit_tracks = await db["spatial_events"].distinct("track_id", {
                "store_id": store_id,
                "zone_type": {"$in": ["SHELF", "DISPLAY"]}
            })
            count_zone_visit = len(zone_visit_tracks)
            
            billing_tracks = await db["spatial_events"].distinct("track_id", {
                "store_id": store_id,
                "zone_type": "BILLING"
            })
            count_billing = len(billing_tracks)
            
            exits_query = {"store_id": store_id, "event_type": "exit"}
            correlation_pipeline = [
                {"$match": exits_query},
                {
                    "$lookup": {
                        "from": "pos_transactions",
                        "let": {"exit_ts": "$timestamp", "s_id": "$store_id"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$store_id", "$$s_id"]},
                                            {"$gte": ["$timestamp", {"$subtract": ["$$exit_ts", 60000]}]},
                                            {"$lte": ["$timestamp", {"$add": ["$$exit_ts", 60000]}]}
                                        ]
                                    }
                                }
                            }
                        ],
                        "as": "matched_txs"
                    }
                },
                {"$match": {"matched_txs": {"$ne": []}}},
                {"$group": {"_id": None, "track_ids": {"$addToSet": "$track_id"}}}
            ]
            try:
                res = await db["spatial_events"].aggregate(correlation_pipeline).to_list(1)
                count_purchase = len(res[0]["track_ids"]) if res else 0
            except Exception:
                count_purchase = 0

        # Calculate percentages and drop-offs
        def pct(c, base):
            return round((c / base) * 100.0, 2) if base > 0 else 0.0

        def drop(c, prev):
            if prev <= 0:
                return 0.0
            return round((1.0 - (c / prev)) * 100.0, 2)

        steps = [
            FunnelStep(step_name="Entry", count=count_entry, percentage=100.0, drop_off_pct=0.0),
            FunnelStep(step_name="Zone Visit", count=count_zone_visit, percentage=pct(count_zone_visit, count_entry), drop_off_pct=drop(count_zone_visit, count_entry)),
            FunnelStep(step_name="Billing Queue", count=count_billing, percentage=pct(count_billing, count_entry), drop_off_pct=drop(count_billing, count_zone_visit)),
            FunnelStep(step_name="Purchase", count=count_purchase, percentage=pct(count_purchase, count_entry), drop_off_pct=drop(count_purchase, count_billing)),
        ]

        return FunnelResponse(
            store_id=store_id,
            timestamp=datetime.now(timezone.utc),
            funnel=steps
        )
    except Exception as e:
        logger.error(f"Error computing funnel for {store_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Store heatmap (Normalized with data confidence check)
# ---------------------------------------------------------------------------

@router.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
async def get_store_heatmap_grid(store_id: str):
    """
    Returns normalized heatmap grid points with data confidence flag.
    """
    try:
        if simulated_data_service.has_data(store_id):
            raw_points = simulated_data_service.get_zone_heatmap(store_id)
            if hasattr(raw_points, "__await__"):
                raw_points = await raw_points
            events = simulated_data_service._load_events(store_id)
            cust_events = [e for e in events if not e.get("is_staff", False)]
            session_count = len({e["track_id"] for e in cust_events if e.get("track_id")})
        else:
            raw_points = await analytics_service.get_zone_heatmap(store_id)
            db = get_db_client()
            session_count = await db["customer_sessions"].count_documents({"store_id": store_id})

        max_intensity = max([float(p.get("intensity", 0)) for p in raw_points]) if raw_points else 0.0
        normalized_points = []
        for p in raw_points:
            pt_copy = dict(p)
            intensity = float(p.get("intensity", 0))
            pt_copy["intensity"] = round((intensity / max_intensity) * 100.0, 2) if max_intensity > 0 else 0.0
            normalized_points.append(pt_copy)

        return HeatmapResponse(
            store_id=store_id,
            timestamp=datetime.now(timezone.utc),
            data_confidence=session_count >= 20,
            heatmap_points=normalized_points
        )
    except Exception as e:
        logger.error(f"Error computing store heatmap grid for {store_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
