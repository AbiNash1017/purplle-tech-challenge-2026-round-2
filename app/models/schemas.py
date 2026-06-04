from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone

class TrackingEvent(BaseModel):
    event_id: Optional[str] = Field(default=None, description="Unique event identifier for idempotency")
    event_type: str = Field(..., description="Type of event: entry, exit, zone_entered, zone_exited, queue_completed, queue_abandoned, zone_update, heartbeat")
    store_id: str = Field(..., description="Unified store identifier, e.g., ST1076")
    track_id: str = Field(..., description="Unique track identifier for the individual")
    camera_id: str = Field(..., description="Camera ID emitting the event")
    timestamp: datetime = Field(..., description="UTC event timestamp")
    is_staff: bool = Field(default=False)
    
    # Customer Demographics
    gender: Optional[str] = None
    age: Optional[float] = None
    age_bucket: Optional[str] = None
    is_face_hidden: Optional[bool] = False
    
    # Group context
    group_id: Optional[str] = None
    group_size: Optional[int] = None
    
    # Re-entry session metadata
    reentry_count: int = Field(default=0)
    
    # Spatial Location (GeoJSON Point format)
    location: Optional[Dict[str, Any]] = None
    
    # Zone context (optional for zone/billing events)
    zone_id: Optional[str] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    is_revenue_zone: Optional[Union[bool, str]] = None
    
    # Queue / Billing context
    queue_event_id: Optional[str] = None
    queue_join_ts: Optional[datetime] = None
    queue_served_ts: Optional[datetime] = None
    queue_exit_ts: Optional[datetime] = None
    wait_seconds: Optional[float] = None
    queue_position_at_join: Optional[int] = None
    abandoned: Optional[bool] = None

    @model_validator(mode='before')
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
            
        # 1. Normalize store_code -> store_id
        store_code = data.get("store_code")
        store_id = data.get("store_id")
        if store_code and not store_id:
            # Map store_1076 to ST1076, store_1008 to ST1008, etc.
            code_num = "".join(filter(str.isdigit, store_code))
            data["store_id"] = f"ST{code_num}" if code_num else store_code
        elif store_id:
            # Clean store_id if it's ST1076
            data["store_id"] = store_id
            
        # 2. Normalize id_token / track_id
        id_token = data.get("id_token")
        track_id = data.get("track_id")
        if id_token is not None and track_id is None:
            data["track_id"] = str(id_token)
        elif track_id is not None:
            data["track_id"] = str(track_id)
            
        # 3. Normalize timestamp / event_timestamp / event_time
        ts = data.get("timestamp") or data.get("event_timestamp") or data.get("event_time") or data.get("queue_exit_ts") or data.get("queue_join_ts")
        if ts:
            if isinstance(ts, str):
                try:
                    data["timestamp"] = datetime.fromisoformat(ts)
                except ValueError:
                    # Try other parsing if needed, but ISO should work
                    data["timestamp"] = datetime.now(timezone.utc)
            else:
                data["timestamp"] = ts
        else:
            data["timestamp"] = datetime.now(timezone.utc)
            
        # 4. Normalize gender_pred / gender, age_pred / age
        gender_pred = data.get("gender_pred")
        if gender_pred and not data.get("gender"):
            data["gender"] = gender_pred
            
        age_pred = data.get("age_pred")
        if age_pred is not None and data.get("age") is None:
            data["age"] = float(age_pred)
            
        # 5. Normalize Location (Coordinates normalization)
        x = data.get("zone_hotspot_x") or data.get("x")
        y = data.get("zone_hotspot_y") or data.get("y")
        loc = data.get("location")
        
        if loc is None and (x is not None and y is not None):
            x_val = float(x)
            y_val = float(y)
            # Normalize to [0.0, 1.0] if in pixels (assume 1000px ref size if > 1.0)
            if x_val > 1.0:
                x_val = min(x_val / 1000.0, 1.0)
            if y_val > 1.0:
                y_val = min(y_val / 1000.0, 1.0)
            data["location"] = {
                "type": "Point",
                "coordinates": [x_val, y_val]
            }
            
        # 6. Normalize queue times
        for q_field in ["queue_join_ts", "queue_served_ts", "queue_exit_ts"]:
            q_val = data.get(q_field)
            if isinstance(q_val, str):
                try:
                    data[q_field] = datetime.fromisoformat(q_val)
                except ValueError:
                    pass

        return data

class POSTransaction(BaseModel):
    order_id: str
    store_id: str
    timestamp: datetime
    product_id: str
    brand_name: str
    total_amount: float

class ZoneHeatmapPoint(BaseModel):
    zone_id: str
    zone_name: str
    zone_type: str
    dwell_seconds: float
    visit_count: int

class MetricsResponse(BaseModel):
    store_id: str
    timestamp: datetime
    active_customers: int
    footfall_count: int
    avg_dwell_seconds: float
    queue_wait_seconds_avg: float
    queue_abandon_rate: float
    queue_depth: int
    pos_correlation_rate: float
    zone_breakdown: List[ZoneHeatmapPoint]

class AnomalyDetail(BaseModel):
    type: str  # queue_spike, conversion_drop, dead_zone
    severity: str  # INFO, WARN, CRITICAL
    message: str
    suggested_action: str
    timestamp: datetime

class FunnelStep(BaseModel):
    step_name: str
    count: int
    percentage: float
    drop_off_pct: float

class FunnelResponse(BaseModel):
    store_id: str
    timestamp: datetime
    funnel: List[FunnelStep]

class HeatmapResponse(BaseModel):
    store_id: str
    timestamp: datetime
    data_confidence: bool
    heatmap_points: List[Dict[str, Any]]

class BatchIngestError(BaseModel):
    index: int
    error: str

class BatchIngestResponse(BaseModel):
    status: str
    processed: int
    failed: int
    errors: List[BatchIngestError]
