from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional, Dict, Any

class StaffTrack(BaseModel):
    store_id: str
    track_id: str
    first_seen: datetime
    last_seen: datetime
    reason: str  # 'velocity', 'revisits', 'uniform'
    revisits_count: int = 0
    velocity_history: List[float] = Field(default_factory=list)

class VisitSegment(BaseModel):
    entered_at: datetime
    exited_at: Optional[datetime] = None

class CustomerSession(BaseModel):
    store_id: str
    id_token: str  # Re-ID matched stable token
    first_seen: datetime
    last_seen: datetime
    reentry_count: int = 0
    visit_segments: List[VisitSegment] = Field(default_factory=list)
    demographics: Dict[str, Any] = Field(default_factory=dict) # gender, age, age_bucket
    group_id: Optional[str] = None
    group_size: Optional[int] = None
