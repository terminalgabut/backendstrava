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

# --- ENGINES ---

async def get_weather_engine(lat, lng, start_date_local=None):
    """MENGAMBIL CUACA HISTORIS (Saat Kejadian) via Open-Meteo Archive API."""
    fallback = {"temp": 28.0, "wind": 12.0, "hum": 65}
    if not lat or not lng: return fallback
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if start_date_local:
                # Parsing tanggal dan jam lari
                dt = datetime.fromisoformat(start_date_local.replace('Z', ''))
                date_str = dt.strftime('%Y-%m-%d')
                hour_idx = dt.hour
                url = f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lng}&start_date={date_str}&end_date={date_str}&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m"
            else:
                url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
            
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if start_date_local:
                    return {
                        "temp": round(data['hourly']['temperature_2m'][hour_idx], 1),
                        "wind": round(data['hourly']['wind_speed_10m'][hour_idx], 1),
                        "hum": int(data['hourly']['relative_humidity_2m'][hour_idx])
                    }
                else:
                    curr = data.get("current", {})
                    return {
                        "temp": round(curr.get("temperature_2m", 28.0), 1),
                        "wind": round(curr.get("wind_speed_10m", 12.0), 1),
                        "hum": int(curr.get("relative_humidity_2m", 65))
                    }
    except Exception as e:
        print(f"Weather Engine Error: {e}")
    return fallback

async def fetch_detailed_location(lat, lng):
    """MENGAMBIL NAMA LOKASI MANUSIAWI (Desa, Kecamatan)."""
    if not lat or not lng: return "Global Area"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lng}&zoom=15",
                headers={"Accept-Language": "id", "User-Agent": "LariSehatApp/2.0 (admin@larisehat.com)"}
            )
            data = resp.json()
            addr = data.get("address", {})
            
            # Prioritas: Desa/Kelurahan -> Kecamatan -> Kota
            village = addr.get("village") or addr.get("suburb") or addr.get("hamlet") or addr.get("neighbourhood") or ""
            district = addr.get("city_district") or addr.get("district") or addr.get("town") or ""
            
            clean_parts = [p for p in [village, district] if p]
            if clean_parts:
                return ", ".join(clean_parts)
            
            # Jika gagal menyusun, ambil nama paling depan dari display_name
            return data.get("display_name", "").split(',')[0] or f"{lat}, {lng}"
    except Exception as e:
        return f"{lat}, {lng}"

# --- CORE LOGIC ---

async def process_single_activity(strava_id: str, headers: dict):
    """Enrichment: Strava -> Cuaca Historis -> Lokasi -> DB."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"https://www.strava.com/api/v3/activities/{strava_id}", headers=headers)
        if resp.status_code != 200: return False
        
        act = resp.json()
        start_coords = act.get("start_latlng", [])
        lat = start_coords[0] if len(start_coords) == 2 else None
        lng = start_coords[1] if len(start_coords) == 2 else None
        start_date_local = act.get("start_date_local")

        # Jalankan parallel
        location_task = fetch_detailed_location(lat, lng)
        weather_task = get_weather_engine(lat, lng, start_date_local)
        
        location_name, weather_data = await asyncio.gather(location_task, weather_task)

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
            "location_name": location_name,
            "weather_temp": weather_data["temp"],
            "weather_wind": weather_data["wind"],
            "weather_humidity": weather_data["hum"],
            "splits_metric": act.get("splits_metric")
        }

        supabase.table("activities").upsert(record, on_conflict="strava_id").execute()
        return True

async def clean_old_weather_data():
    """Update row lama yang masih pake suhu statis 28."""
    try:
        supabase.table("activities").update({
            "weather_temp": None,
            "weather_wind": None,
            "weather_humidity": None
        }).eq("weather_temp", 28.0).execute()
        print("Cleanup: Data statis siap diperbarui.")
    except Exception as e:
        print(f"Cleanup Error: {e}")

# --- ENDPOINTS ---

@app.get("/api/sync")
async def sync_bulk(background_tasks: BackgroundTasks):
    background_tasks.add_task(clean_old_weather_data)
    access_token = await get_new_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with httpx.AsyncClient() as client:
        list_resp = await client.get("https://www.strava.com/api/v3/athlete/activities?per_page=30", headers=headers)
        activities = list_resp.json()
    
    count = 0
    for act in activities:
        if await process_single_activity(act['id'], headers): 
            count += 1
            
    return {"status": "success", "synced": count, "timestamp": get_wib_now()}

@app.post("/api/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """Menerima notifikasi otomatis dari Strava."""
    event = await request.json()
    if event.get("object_type") == "activity" and event.get("aspect_type") == "create":
        strava_id = event.get("object_id")
        access_token = await get_new_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
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
