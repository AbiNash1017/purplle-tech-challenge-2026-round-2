import time
import uuid
import math
from typing import Dict, List, Tuple, Optional

class GroupDetector:
    def __init__(self, time_threshold_seconds: float = 10.0, distance_threshold: float = 0.08):
        """
        Clusters active tracks if they remain close to each other for a sustained period.
        - time_threshold_seconds: Continuous duration (seconds) required to form a group.
        - distance_threshold: Max distance in normalized coordinates (0.0 to 1.0).
        """
        self.time_threshold = time_threshold_seconds
        self.distance_threshold = distance_threshold
        
        # Track active groups: group_id -> set(track_ids)
        self.groups: Dict[str, set] = {}
        
        # Track pairs that are currently close: frozenset(tid1, tid2) -> timestamp_first_seen_close
        self.close_pairs: Dict[frozenset, float] = {}

    def update_groups(self, active_tracks: Dict[str, Dict], current_timestamp: float) -> Dict[str, str]:
        """
        Evaluates proximity of all active tracks.
        Returns a mapping of track_id -> group_id for tracks that are in groups.
        Keeps individual IDs constant.
        """
        track_ids = list(active_tracks.keys())
        current_close_pairs = set()
        
        # Calculate pairwise distances
        for i in range(len(track_ids)):
            for j in range(i + 1, len(track_ids)):
                t1, t2 = track_ids[i], track_ids[j]
                
                # Do not group staff members into shopper groups (or vice versa) if is_staff is known
                s1 = active_tracks[t1].get("is_staff", False)
                s2 = active_tracks[t2].get("is_staff", False)
                if s1 != s2:
                    continue
                    
                x1, y1 = active_tracks[t1].get("x", 0), active_tracks[t1].get("y", 0)
                x2, y2 = active_tracks[t2].get("x", 0), active_tracks[t2].get("y", 0)
                
                dist = math.hypot(x1 - x2, y1 - y2)
                
                if dist <= self.distance_threshold:
                    pair = frozenset([t1, t2])
                    current_close_pairs.add(pair)
                    
                    if pair not in self.close_pairs:
                        self.close_pairs[pair] = current_timestamp
                    else:
                        elapsed = current_timestamp - self.close_pairs[pair]
                        if elapsed >= self.time_threshold:
                            self._merge_into_group(t1, t2)
        
        # Remove pairs that are no longer close (reset their timer)
        stale_pairs = set(self.close_pairs.keys()) - current_close_pairs
        for p in stale_pairs:
            del self.close_pairs[p]
            
        # Clean up empty groups
        self._cleanup_groups(track_ids)
            
        # Build mapping for return
        track_to_group = {}
        for g_id, members in self.groups.items():
            for m in members:
                track_to_group[m] = g_id
                
        return track_to_group

    def _merge_into_group(self, t1: str, t2: str):
        # Find if either already belongs to a group
        g1, g2 = None, None
        for g_id, members in self.groups.items():
            if t1 in members: g1 = g_id
            if t2 in members: g2 = g_id
            
        if g1 and g2 and g1 != g2:
            # Merge g2 into g1
            self.groups[g1].update(self.groups[g2])
            del self.groups[g2]
        elif g1 and not g2:
            self.groups[g1].add(t2)
        elif g2 and not g1:
            self.groups[g2].add(t1)
        elif not g1 and not g2:
            # Create new group
            new_id = f"G_{uuid.uuid4().hex[:6]}"
            self.groups[new_id] = {t1, t2}

    def _cleanup_groups(self, active_track_ids: List[str]):
        """Remove inactive tracks from groups, and delete groups with < 2 members."""
        active_set = set(active_track_ids)
        to_delete = []
        for g_id, members in list(self.groups.items()):
            members.intersection_update(active_set)
            if len(members) < 2:
                to_delete.append(g_id)
                
        for g_id in to_delete:
            del self.groups[g_id]

    # Backward compatibility stub for detect.py entrance check (if still called)
    def check_group(self, track_id: str, x: float, y: float) -> Tuple[Optional[str], Optional[int]]:
        return None, None
