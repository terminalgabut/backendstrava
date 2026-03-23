import os
import httpx
import asyncio
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client

app = FastAPI(title="Strava Hybrid Sync Pro", version="8.2.0")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIG & CLIENTS ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
STRAVA_VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN", "larisehat2026")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- UTILS ---
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
        
        
# --- CORE LOGIC ---

async def process_single_activity(strava_id: str, headers: dict):
    """Hanya mengambil data mentah dari Strava dan menyimpannya ke Supabase."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"https://www.strava.com/api/v3/activities/{strava_id}", headers=headers)
        if resp.status_code != 200: return False
        
        act = resp.json()
        start_coords = act.get("start_latlng", [])
        lat = start_coords[0] if len(start_coords) == 2 else None
        lng = start_coords[1] if len(start_coords) == 2 else None
        start_date_local = act.get("start_date_local")      

        record = {
            "strava_id": str(act.get("id")),
            "name": act.get("name"),
            "distance": act.get("distance"),
            "moving_time": act.get("moving_time"),
            "elapsed_time_seconds": act.get("elapsed_time"),
            "type": act.get("type"),
            "start_date": start_date_local,
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
            "splits_metric": act.get("splits_metric"),
            "average_watts": act.get("average_watts"), # Penting untuk Ride
            "kilojoules": act.get("kilojoules"),       # Penting untuk Ride
            "device_watts": act.get("device_watts"),   # True jika pakai Power Meter
            "athlete_weight": act.get("athlete_weight")
        }

        supabase.table("activities").upsert(record, on_conflict="strava_id").execute()
        return True

async def get_athlete_profile(headers: dict):
    """Mengambil data profil atlet dari Strava."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get("https://www.strava.com/api/v3/athlete", headers=headers)
        if resp.status_code != 200:
            return None
        
        athlete = resp.json()
        
        # Mapping data profil untuk Supabase
        profile_record = {
            "id": str(athlete.get("id")), # Strava Athlete ID
            "username": athlete.get("username"),
            "first_name": athlete.get("firstname"),
            "last_name": athlete.get("lastname"),
            "city": athlete.get("city"),
            "state": athlete.get("state"),
            "sex": athlete.get("sex"), # 'M' atau 'F'
            "weight": athlete.get("weight"), # Berat badan dalam KG
            "profile_medium": athlete.get("profile_medium"), # Foto profil
            "updated_at": get_wib_now().isoformat()
        }
        
        # Simpan ke tabel 'profiles' di Supabase
        supabase.table("profile").upsert(profile_record, on_conflict="id").execute()
        return profile_record

# --- ENDPOINTS ---

@app.get("/api/sync")
async def sync_bulk(background_tasks: BackgroundTasks):
    access_token = await get_new_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with httpx.AsyncClient() as client:
        list_resp = await client.get("https://www.strava.com/api/v3/athlete/activities?per_page=30", headers=headers)
        activities = list_resp.json()

    athlete_info = await get_athlete_profile(headers) 
    
    count = 0
    for act in activities:
        if await process_single_activity(act['id'], headers): 
            count += 1
            
    return {"status": "success", "athlete": athlete_info.get("first_name") if athlete_info else "Unknown", "synced": count, "timestamp": get_wib_now()}

@app.post("/api/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()
    if event.get("object_type") == "activity" and event.get("aspect_type") == "create":
        strava_id = event.get("object_id")
        access_token = await get_new_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Tambahkan tugas update profil juga di background
        background_tasks.add_task(get_athlete_profile, headers)
        background_tasks.add_task(process_single_activity, strava_id, headers)
        
    return {"status": "event_processed"}

@app.get("/api/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == STRAVA_VERIFY_TOKEN:
        return {"hub.challenge": params.get("hub.challenge")}
    return {"error": "Invalid Verify Token"}

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
