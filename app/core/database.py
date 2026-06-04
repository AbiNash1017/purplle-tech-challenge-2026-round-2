import os
import csv
import logging
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.upstash_redis import upstash_redis

logger = logging.getLogger(__name__)

# Singletons
db_client = None
db = None

def get_db_client():
    global db_client, db
    if db_client is None:
        db_client = AsyncIOMotorClient(settings.MONGO_URI)
        db_name = settings.MONGO_URI.split('/')[-1].split('?')[0]
        if not db_name or db_name == "":
            db_name = "store_intelligence"
        db = db_client[db_name]
    return db

def get_redis_client():
    """Returns the Upstash REST-backed Redis client."""
    return upstash_redis

async def close_db_connections():
    global db_client
    if db_client:
        db_client.close()
        db_client = None
    await upstash_redis.close()

async def init_db():
    database = get_db_client()
    redis_conn = get_redis_client()
    
    # 1. Check or create Time-Series collection for spatial_events
    existing_collections = await database.list_collection_names()
    if "spatial_events" not in existing_collections:
        logger.info("Creating spatial_events time-series collection...")
        try:
            await database.create_collection(
                "spatial_events",
                timeseries={
                    "timeField": "timestamp",
                    "metaField": "store_id",
                    "granularity": "seconds"
                }
            )
        except Exception as e:
            logger.error(f"Error creating time-series collection: {e}")
            
    # 2. Indexes
    # zones 2dsphere index on geometry
    zones_col = database["zones"]
    await zones_col.create_index([("geometry", "2dsphere")])
    await zones_col.create_index([("store_id", 1), ("zone_id", 1)], unique=True)
    
    # pos_transactions compound index
    pos_col = database["pos_transactions"]
    await pos_col.create_index([("store_id", 1), ("timestamp", -1)])
    await pos_col.create_index([("order_id", 1)], unique=True)
    
    # staff_tracks compound index
    staff_col = database["staff_tracks"]
    await staff_col.create_index([("store_id", 1), ("track_id", 1)])
    
    # customer_sessions index
    sessions_col = database["customer_sessions"]
    await sessions_col.create_index([("store_id", 1), ("id_token", 1)])
    
    # 3. Seed zones
    await seed_zones(zones_col)
    
    # 4. Seed POS Transactions
    await seed_pos_transactions(pos_col)

async def seed_zones(zones_col):
    # Store 1 (ST1076) zones
    store_1_zones = [
        {
            "store_id": "ST1076",
            "zone_id": "Z01",
            "zone_name": "Left Wall Shelves (Salm/TFS)",
            "zone_type": "SHELF",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.0], [0.38, 0.0], [0.38, 0.25], [0.0, 0.25], [0.0, 0.0]]]
            }
        },
        {
            "store_id": "ST1076",
            "zone_id": "Z02",
            "zone_name": "Right Wall Shelves (Minimalis/Aqualogi/Foxtal/JC)",
            "zone_type": "SHELF",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.52, 0.0], [1.0, 0.0], [1.0, 0.25], [0.52, 0.25], [0.52, 0.0]]]
            }
        },
        {
            "store_id": "ST1076",
            "zone_id": "Z03",
            "zone_name": "F.O.H Center (Fragrance/Nail)",
            "zone_type": "DISPLAY",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.3, 0.3], [0.55, 0.3], [0.55, 0.65], [0.3, 0.65], [0.3, 0.3]]]
            }
        },
        {
            "store_id": "ST1076",
            "zone_id": "Z04",
            "zone_name": "Makeup Unit Center",
            "zone_type": "DISPLAY",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.52, 0.3], [0.75, 0.3], [0.75, 0.65], [0.52, 0.65], [0.52, 0.3]]]
            }
        },
        {
            "store_id": "ST1076",
            "zone_id": "Z05",
            "zone_name": "Bottom Wall (Fac/Mars/Mens/Lo'real/Beaut)",
            "zone_type": "SHELF",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.1, 0.75], [0.95, 0.75], [0.95, 1.0], [0.1, 1.0], [0.1, 0.75]]]
            }
        },
        {
            "store_id": "ST1076",
            "zone_id": "Z06",
            "zone_name": "Billing Counter Queue",
            "zone_type": "BILLING",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.82, 0.25], [1.0, 0.25], [1.0, 0.75], [0.82, 0.75], [0.82, 0.25]]]
            }
        },
        {
            "store_id": "ST1076",
            "zone_id": "Z07",
            "zone_name": "Entrance Corridor",
            "zone_type": "ENTRANCE",
            "is_revenue_zone": False,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.3], [0.18, 0.3], [0.18, 0.7], [0.0, 0.7], [0.0, 0.3]]]
            }
        }
    ]
    
    # Store 2 (ST1008) zones
    store_2_zones = [
        {
            "store_id": "ST1008",
            "zone_id": "Z01",
            "zone_name": "Left Wall Units (Wall Unit 1-6)",
            "zone_type": "SHELF",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.35], [0.12, 0.35], [0.12, 1.0], [0.0, 1.0], [0.0, 0.35]]]
            }
        },
        {
            "store_id": "ST1008",
            "zone_id": "Z02",
            "zone_name": "Top Wall Units (Wall Unit 7-13)",
            "zone_type": "SHELF",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.35], [1.0, 0.35], [1.0, 0.48], [0.0, 0.48], [0.0, 0.35]]]
            }
        },
        {
            "store_id": "ST1008",
            "zone_id": "Z03",
            "zone_name": "Right Wall Units (Wall Unit 14-19)",
            "zone_type": "SHELF",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.88, 0.35], [1.0, 0.35], [1.0, 1.0], [0.88, 1.0], [0.88, 0.35]]]
            }
        },
        {
            "store_id": "ST1008",
            "zone_id": "Z04",
            "zone_name": "MK-Gondola Center Displays",
            "zone_type": "DISPLAY",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.15, 0.55], [0.45, 0.55], [0.45, 0.95], [0.15, 0.95], [0.15, 0.55]]]
            }
        },
        {
            "store_id": "ST1008",
            "zone_id": "Z05",
            "zone_name": "Makeup Units (Right-Center)",
            "zone_type": "DISPLAY",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.58, 0.6], [0.85, 0.6], [0.85, 0.9], [0.58, 0.9], [0.58, 0.6]]]
            }
        },
        {
            "store_id": "ST1008",
            "zone_id": "Z06",
            "zone_name": "Billing Counter Queue",
            "zone_type": "BILLING",
            "is_revenue_zone": True,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.38, 0.42], [0.62, 0.42], [0.62, 0.58], [0.38, 0.58], [0.38, 0.42]]]
            }
        },
        {
            "store_id": "ST1008",
            "zone_id": "Z07",
            "zone_name": "Main Entrance",
            "zone_type": "ENTRANCE",
            "is_revenue_zone": False,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.3, 0.9], [0.7, 0.9], [0.7, 1.0], [0.3, 1.0], [0.3, 0.9]]]
            }
        }
    ]
    
    for zone in store_1_zones + store_2_zones:
        await zones_col.update_one(
            {"store_id": zone["store_id"], "zone_id": zone["zone_id"]},
            {"$set": zone},
            upsert=True
        )
    logger.info("Successfully seeded zones.")

async def seed_pos_transactions(pos_col):
    # Check if pos_transactions is empty
    count = await pos_col.count_documents({})
    if count > 0:
        logger.info(f"pos_transactions already has {count} documents. Skipping seed.")
        return

    csv_paths = [
        "POS - sample transactionsb1e826f.csv",
        "d:/purpell/v2/POS - sample transactionsb1e826f.csv",
        "/app/POS - sample transactionsb1e826f.csv"
    ]
    
    csv_path = None
    for path in csv_paths:
        if os.path.exists(path):
            csv_path = path
            break
            
    if not csv_path:
        logger.warning("POS transaction sample CSV file not found. Skipping POS seed.")
        return
        
    logger.info(f"Seeding POS transactions from {csv_path}...")
    try:
        transactions = []
        with open(csv_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Parse date and time:
                # order_date: 10-04-2026
                # order_time: 12:15:05
                date_str = row.get("order_date", "").strip()
                time_str = row.get("order_time", "").strip()
                
                dt = datetime.now(timezone.utc)
                try:
                    dt = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
                except Exception as ex:
                    try:
                        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass
                
                transactions.append({
                    "order_id": row.get("order_id", "").strip(),
                    "store_id": row.get("store_id", "").strip(),
                    "timestamp": dt,
                    "product_id": row.get("product_id", "").strip(),
                    "brand_name": row.get("brand_name", "").strip(),
                    "total_amount": float(row.get("total_amount", "0").strip() or 0)
                })
                
        if transactions:
            # Let's handle duplicates by using unique order_id. We can do bulk upsert or just insert_many
            # since the collection is verified empty, insert_many is fine.
            # Avoid inserting duplicate order_ids if they exist in CSV
            seen_ids = set()
            unique_transactions = []
            for t in transactions:
                if t["order_id"] not in seen_ids:
                    seen_ids.add(t["order_id"])
                    unique_transactions.append(t)
            
            await pos_col.insert_many(unique_transactions)
            logger.info(f"Successfully seeded {len(unique_transactions)} POS transactions.")
    except Exception as e:
        logger.error(f"Error seeding POS transactions: {e}")
