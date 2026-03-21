from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
import os
import httpx
from supabase import create_client, Client

app = FastAPI(title="Strava Hybrid Sync", version="7.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inisialisasi Supabase
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
STRAVA_VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN", "my_secret_token")

def get_wib_now():
    return datetime.now(timezone(timedelta(hours=7)))

async def get_new_access_token():
    async with httpx.AsyncClient() as client:
        payload = {
            "client_id": os.getenv("STRAVA_CLIENT_ID"),
            "client_secret": os.getenv("STRAVA_CLIENT_SECRET"),
            "refresh_token": os.getenv("STRAVA_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        }
        response = await client.post("https://www.strava.com/oauth/token", data=payload)
        response.raise_for_status()
        return response.json().get("access_token")

async def process_single_activity(strava_id: str, headers: dict):
    """Fungsi inti untuk Deep Sync satu ID aktivitas."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"https://www.strava.com/api/v3/activities/{strava_id}", headers=headers)
        if resp.status_code != 200: return False
        
        act = resp.json()
        start_coords = act.get("start_latlng", [])
        
        record = {
            "strava_id": str(act.get("id")),
            "name": act.get("name"),
            "distance": act.get("distance"),
            "moving_time": act.get("moving_time"),
            "type": act.get("type"),
            "start_date": act.get("start_date_local"),
            "average_speed": act.get("average_speed"),
            "max_speed": act.get("max_speed"),
            "calories": act.get("calories"),
            "total_elevation_gain": act.get("total_elevation_gain"),
            "average_heartrate": act.get("average_heartrate"),
            "max_heartrate": act.get("max_heartrate"),
            "summary_polyline": act.get("map", {}).get("summary_polyline"),
            "timezone": act.get("timezone"),
            "device_name": act.get("device_name"),
            "start_lat": start_coords[0] if len(start_coords) == 2 else None,
            "start_lng": start_coords[1] if len(start_coords) == 2 else None,
            "splits_metric": act.get("splits_metric")
        }
        supabase.table("activities").upsert(record, on_conflict="strava_id").execute()
        return True

# --- ENDPOINT 1: CRON SYNC (Untuk Bulk Update) ---
@app.get("/api/sync")
async def sync_bulk():
    access_token = await get_new_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with httpx.AsyncClient() as client:
        list_resp = await client.get("https://www.strava.com/api/v3/athlete/activities?per_page=10", headers=headers)
        activities = list_resp.json()
        
    count = 0
    for act in activities:
        success = await process_single_activity(act['id'], headers)
        if success: count += 1
            
    return {"status": "success", "synced": count, "mode": "bulk_cron"}

# --- ENDPOINT 2: WEBHOOK VERIFICATION (Handshake) ---
@app.get("/api/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == STRAVA_VERIFY_TOKEN:
        return {"hub.challenge": params.get("hub.challenge")}
    return {"error": "Unauthorized"}

# --- ENDPOINT 3: WEBHOOK RECEIVER (Real-time Update) ---
@app.post("/api/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()
    
    # Hanya proses jika ada aktivitas baru (create)
    if event.get("object_type") == "activity" and event.get("aspect_type") == "create":
        strava_id = event.get("object_id")
        access_token = await get_new_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Jalankan di background agar Strava tidak timeout (harus respon < 2 detik)
        background_tasks.add_task(process_single_activity, strava_id, headers)
        
    return {"status": "event_received"}
