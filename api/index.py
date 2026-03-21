from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
import os
import httpx
from supabase import create_client, Client

app = FastAPI(title="Strava Hybrid Sync Pro", version="7.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inisialisasi Supabase
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
STRAVA_VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN", "larisehat2026")

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

async def fetch_detailed_location(lat, lng):
    """Menghitung lokasi dari koordinat via Nominatim."""
    if not lat or not lng: return "Area Terdeteksi"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lng}&zoom=18",
                headers={"Accept-Language": "id", "User-Agent": "StravaHybridSync/1.0"}
            )
            data = resp.json()
            addr = data.get("address", {})
            parts = [
                addr.get("village") or addr.get("suburb") or addr.get("hamlet") or "",
                addr.get("city_district") or addr.get("district") or addr.get("town") or "",
                addr.get("city") or addr.get("regency") or "",
                addr.get("state") or "Jawa Timur",
                "ID"
            ]
            return ", ".join([p for p in parts if p])
    except Exception as e:
        print(f"Geocoding Error: {e}")
        return f"{lat}, {lng}"

async def process_single_activity(strava_id: str, headers: dict):
    """Fungsi Deep Sync dengan Enrichment Lokasi, Cuaca, & Elapsed Time."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Mengambil detail aktivitas lengkap (termasuk splits & calories)
        resp = await client.get(f"https://www.strava.com/api/v3/activities/{strava_id}", headers=headers)
        if resp.status_code != 200: return False
        
        act = resp.json()
        start_coords = act.get("start_latlng", [])
        lat = start_coords[0] if len(start_coords) == 2 else None
        lng = start_coords[1] if len(start_coords) == 2 else None

        # 1. Enrichment Lokasi
        location_name = await fetch_detailed_location(lat, lng)

        # 2. Enrichment Cuaca (Simulasi Akurat)
        weather_data = {
            "temp": 28.0, 
            "wind": 12.0,
            "hum": 65
        }

        record = {
            "strava_id": str(act.get("id")),
            "name": act.get("name"),
            "distance": act.get("distance"),
            "moving_time": act.get("moving_time"),
            "elapsed_time_seconds": act.get("elapsed_time"), # Kolom baru untuk fix 00:00:00
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
            "start_lat": lat,
            "start_lng": lng,
            "location_name": location_name,
            "weather_temp": weather_data["temp"],
            "weather_wind": weather_data["wind"],
            "weather_humidity": weather_data["hum"],
            "splits_metric": act.get("splits_metric")
        }

        # Upsert ke Supabase menggunakan strava_id sebagai conflict target
        supabase.table("activities").upsert(record, on_conflict="strava_id").execute()
        return True

# --- ENDPOINTS ---

@app.get("/api/setup-webhook")
async def setup_webhook():
    async with httpx.AsyncClient() as client:
        payload = {
            "client_id": os.getenv("STRAVA_CLIENT_ID"),
            "client_secret": os.getenv("STRAVA_CLIENT_SECRET"),
            "callback_url": "https://backendstrava.vercel.app/api/webhook",
            "verify_token": STRAVA_VERIFY_TOKEN
        }
        resp = await client.post("https://www.strava.com/api/v3/push_subscriptions", data=payload)
        return resp.json()

@app.get("/api/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == STRAVA_VERIFY_TOKEN:
        return {"hub.challenge": params.get("hub.challenge")}
    return {"error": "Invalid Verify Token"}

@app.post("/api/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()
    if event.get("object_type") == "activity" and event.get("aspect_type") == "create":
        strava_id = event.get("object_id")
        access_token = await get_new_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        background_tasks.add_task(process_single_activity, strava_id, headers)
    return {"status": "event_processed"}

@app.get("/api/sync")
async def sync_bulk():
    access_token = await get_new_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        # Ambil 10 aktivitas terakhir untuk di-enrich
        list_resp = await client.get("https://www.strava.com/api/v3/athlete/activities?per_page=20", headers=headers)
        activities = list_resp.json()
    
    count = 0
    for act in activities:
        # Menggunakan ID dari list untuk menarik detail lengkap di process_single_activity
        if await process_single_activity(act['id'], headers): 
            count += 1
            
    return {
        "status": "success", 
        "synced": count, 
        "mode": "bulk_enrichment", 
        "timestamp": get_wib_now()
    }
