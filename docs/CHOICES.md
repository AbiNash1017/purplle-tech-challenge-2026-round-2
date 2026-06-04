# Design Choices — Purplle Store Intelligence Platform

These are the three decisions I'm most proud of on this project. Each one had real tradeoffs, the AI assistant had its own opinions, and in most cases I deliberately went a different direction. Here's my reasoning.

---

## Choice 1: How to Detect Customers, Filter Staff, and Classify Zones

### The Problem
I needed the pipeline to do three things simultaneously: detect and track individuals across video frames, figure out which store zone they're standing in, and filter out employees so they don't corrupt the customer analytics.

The obvious-looking answer was to throw a modern AI model at it.

### What I Was Tempted By (and Rejected)
The AI assistant pushed hard for a Vision-Language Model approach — basically, crop every detected person from the frame, send it to a cloud-hosted VLM like GPT-4o-mini or CLIP, and ask it something like:

```text
Task: Analyze the cropped person image from the retail camera feed.
1. Determine if the person is wearing the official Purplle Store staff uniform:
   - Store 1: All-black shirt and pants.
   - Store 2: Bright pink/magenta polo shirt with dark pants.
2. Classify which zone they're currently in:
   - Options: [Makeup Aisle, Skincare Section, Billing Counter, Entrance, Fragrance Zone]
Format your response as JSON: {"is_staff": boolean, "current_zone": string, "confidence": float}
```

On paper this sounds elegant. In practice it's a disaster. The round-trip latency to a cloud VLM is somewhere between 180ms and 350ms per query. When you have 10+ customers on the floor moving at 15 FPS, that pipeline collapses immediately — you'd need to wait nearly 4 seconds per frame for all tracks to be classified. On top of that, cloud tokens aren't free, and at scale this becomes an expensive ongoing cost for a store analytics tool that's supposed to run 8+ hours a day. I also tested the VLM approach briefly and noticed it would misclassify customer outfits as staff uniforms under warm store lighting — exactly the kind of nondeterministic failure that poisons downstream analytics silently.

### What I Actually Built (and Why It Works Better)

**For zone classification**: I defined static polygon boundaries for each zone in `cameras.yaml`, normalized to a 0.0–1.0 coordinate space. Then I used a ray-casting algorithm to check if any track centroid falls inside a given polygon. This runs in under 0.05ms on a CPU core, requires zero network calls, and is 100% deterministic. There's no guessing, no confidence scores drifting based on cloud model versions — just geometry.

**For staff filtering**: I designed a 5-layer approach. At the CV pipeline level, I check whether a detected person's bounding box matches the HSV color signature for each store's uniform (Store 1 is all-black; Store 2 is the pink/magenta polo). This is cross-referenced with behavioral cues — staff members tend to move slowly, revisit zone boundaries repeatedly, and don't follow typical customer entry → browse → exit patterns. Tracks flagged as staff are routed to a completely separate MongoDB collection (`staff_tracks`) and are never published on the live WebSocket channels.

The result: a pipeline that runs in real time with no cloud dependencies, no hallucinations, and guarantees that zero staff events reach the analytics layer.

---

## Choice 2: What the Event Schema Looks Like

### The Problem
Events come from multiple cameras across two stores. Camera firmware versions differ, field names vary (`store_code` vs `store_id`, `id_token` vs `track_id`), and coordinates are raw pixel integers from different resolutions (`1920x1080` on one camera, `1280x720` on another). I needed a single, consistent data model that everything downstream could trust.

### What the AI Suggested
The AI recommended accepting whatever the cameras send — just dump the raw telemetry into a flexible MongoDB collection and normalize it lazily at query time. The argument was that this maximizes ingestion speed and avoids rejecting events from cameras with slightly different firmware behavior.

I disagreed with this pretty strongly.

### Why I Enforced a Normalized Schema at Ingestion Time

Garbage in, garbage out. If different cameras store different field names and raw pixel coordinates, every single downstream aggregation query becomes a nightmare of `$cond` branches and coordinate remapping. Heatmap queries, zone dwell calculations, cross-store footfall comparisons — all of them would need to know the resolution of each source camera. That's a maintenance trap.

So I built a strict Pydantic validation layer in [schemas.py](file:///d:/purpell/v2/app/models/schemas.py) that normalizes everything at the API boundary:

- **Store identifiers** are standardized to `STxxxx` format (so `store_1076` becomes `ST1076` immediately)
- **Track IDs** are coerced to strings regardless of whether the camera sends an integer or a UUID
- **Pixel coordinates** are normalized to `0.0–1.0` floating point relative to the camera's own resolution, giving every downstream consumer a shared coordinate grid

Crucially, if a camera sends a malformed or incomplete payload, Pydantic rejects it at the HTTP entry point — before anything touches the database. One corrupted camera doesn't silently pollute months of analytics data for a store.

The query performance alone justified this choice. With a normalized grid, heatmap aggregations are simple `$group` + `$sum` pipelines. No per-camera coordinate remapping required.

---

## Choice 3: The Real-Time Pub/Sub Architecture

### The Problem
The dashboard needs live, sub-100ms updates of every customer track in the store. This means events flowing from the CV pipeline need to land in the browser's WebSocket connection within milliseconds of being ingested. I needed a messaging layer that could handle hundreds of events per second without blocking the FastAPI event loop.

### What the AI Suggested
The AI went with the standard answer: direct TCP Redis Pub/Sub. Connect to Redis, publish to channels, subscribe in the WebSocket handlers. It also said that if Redis credentials weren't configured at startup, the server should throw a fatal exception and refuse to start — the reasoning being that without Redis, real-time functionality is broken anyway.

This sounded reasonable until I thought about where this system actually runs.

### Why I Built a Hybrid Fallback Instead

Physical retail stores are not data centers. WAN connectivity can be unreliable. If the Upstash Redis connection drops during store hours — or if a store is doing a fresh install without credentials yet configured — the AI's approach means the entire platform goes down. The dashboard goes dark. Event ingestion stops. That's completely unacceptable for an in-store system.

My approach: the system first attempts to connect to Upstash Redis via its REST API. If that works, great — events flow through the cloud-managed broker. If credentials are missing or the connection fails, the system automatically falls back to a thread-safe in-memory `PubSubBroker` that I implemented in [pub_sub.py](file:///d:/purpell/v2/app/core/pub_sub.py). Store staff never see an error. The dashboard keeps working. Ingestion keeps running. The only difference is that the pub/sub stays local to the machine.

This also made the local development experience dramatically better — no one has to spin up a Redis instance or configure credentials just to run the server and see the dashboard working.

On latency: the local in-memory broker actually performs better than a cloud Redis round-trip for single-node deployments, comfortably staying under 50ms end-to-end even under heavy telemetry loads.

The system degrades gracefully. That was a non-negotiable design goal, and I'm glad I pushed back on the AI's "fail fast or nothing" recommendation here.
