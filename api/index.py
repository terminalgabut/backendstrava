import os
import httpx
import asyncio
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client

app = FastAPI(title="Strava Hybrid Sync Pro", version="7.5.0")

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
# Ubah ini sementara
# WEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY") 
WEATHER_API_KEY = "5dee788f89a5f364fb6e184b03d13cb4" # Tulis langsung di sini
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

async def get_weather_engine(lat, lng):
    """Mengambil data cuaca asli dari OpenWeather."""
    # Fallback jika data tidak tersedia
    fallback = {"temp": 28.0, "wind": 12.0, "hum": 65}
    
    if not lat or not lng or not WEATHER_API_KEY:
        return fallback
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Menggunakan API Current Weather
            url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lng}&appid={WEATHER_API_KEY}&units=metric"
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "temp": round(data["main"]["temp"], 1),
                    "wind": round(data["wind"]["speed"] * 3.6, 1), # m/s ke km/h
                    "hum": int(data["main"]["humidity"])
                }
    except Exception as e:
        print(f"Weather Engine Error: {e}")
    
    return fallback

async def fetch_detailed_location(lat, lng):
    """Menghitung lokasi dari koordinat via Nominatim (OpenStreetMap)."""
    if not lat or not lng: return "Global Area"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lng}&zoom=15",
                headers={"Accept-Language": "id", "User-Agent": "StravaHybridSync/1.0"}
            )
            data = resp.json()
            addr = data.get("address", {})
            # Susun alamat dari yang terkecil (Desa -> Kecamatan -> Kota)
            parts = [
                addr.get("village") or addr.get("suburb") or addr.get("hamlet") or "",
                addr.get("city_district") or addr.get("district") or addr.get("town") or "",
                addr.get("city") or addr.get("regency") or addr.get("state") or ""
            ]
            clean_parts = [p for p in parts if p]
            return ", ".join(clean_parts) if clean_parts else f"{lat}, {lng}"
    except Exception as e:
        print(f"Geocoding Error: {e}")
        return f"{lat}, {lng}"

# --- CORE LOGIC ---

async def process_single_activity(strava_id: str, headers: dict):
    """Proses enrichment data: Ambil Strava -> Fetch Cuaca & Lokasi -> Simpan ke DB."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"https://www.strava.com/api/v3/activities/{strava_id}", headers=headers)
        if resp.status_code != 200: return False
        
        act = resp.json()
        start_coords = act.get("start_latlng", [])
        lat = start_coords[0] if len(start_coords) == 2 else None
        lng = start_coords[1] if len(start_coords) == 2 else None

        # Jalankan Engine secara paralel agar cepat
        location_task = fetch_detailed_location(lat, lng)
        weather_task = get_weather_engine(lat, lng)
        
        location_name, weather_data = await asyncio.gather(location_task, weather_task)

        record = {
            "strava_id": str(act.get("id")),
            "name": act.get("name"),
            "distance": act.get("distance"),
            "moving_time": act.get("moving_time"),
            "elapsed_time_seconds": act.get("elapsed_time"),
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

        # Upsert: Update jika strava_id sudah ada, Insert jika belum ada
        supabase.table("activities").upsert(record, on_conflict="strava_id").execute()
        return True

async def clean_old_weather_data():
    """Menghapus data cuaca '28' statis agar bisa diperbarui dengan data asli."""
    try:
        # Cari semua row yang suhunya persis 28.0 (placeholder lama)
        supabase.table("activities").update({
            "weather_temp": None,
            "weather_wind": None,
            "weather_humidity": None
        }).eq("weather_temp", 28.0).execute()
        print("Cleanup: Data cuaca statis berhasil dikosongkan.")
    except Exception as e:
        print(f"Cleanup Error: {e}")

# --- ENDPOINTS ---

@app.get("/api/sync")
async def sync_bulk(background_tasks: BackgroundTasks):
    """Endpoint untuk sinkronisasi massal dan pembersihan data lama."""
    # 1. Bersihkan data 28 derajat di background
    background_tasks.add_task(clean_old_weather_data)
    
    # 2. Ambil token baru
    access_token = await get_new_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # 3. Ambil 30 aktivitas terbaru dari Strava
    async with httpx.AsyncClient() as client:
        list_resp = await client.get("https://www.strava.com/api/v3/athlete/activities?per_page=30", headers=headers)
        activities = list_resp.json()
    
    count = 0
    for act in activities:
        # Jalankan deep sync per aktivitas
        if await process_single_activity(act['id'], headers): 
            count += 1
            
    return {
        "status": "success", 
        "synced": count, 
        "mode": "bulk_enrichment", 
        "timestamp": get_wib_now()
    }

@app.post("/api/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """Menerima lari baru secara otomatis dari Strava."""
    event = await request.json()
    if event.get("object_type") == "activity" and event.get("aspect_type") == "create":
        strava_id = event.get("object_id")
        access_token = await get_new_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        # Jalankan di background agar Strava tidak timeout
        background_tasks.add_task(process_single_activity, strava_id, headers)
    return {"status": "event_processed"}

@app.get("/api/webhook")
async def verify_webhook(request: Request):
    """Verifikasi webhook saat setup pertama kali."""
    params = request.query_params
    if params.get("hub.verify_token") == STRAVA_VERIFY_TOKEN:
        return {"hub.challenge": params.get("hub.challenge")}
    return {"error": "Invalid Verify Token"}

@app.get("/api/setup-webhook")
async def setup_webhook():
    """Memanggil Strava untuk mendaftarkan URL webhook ini."""
    async with httpx.AsyncClient() as client:
        payload = {
            "client_id": os.getenv("STRAVA_CLIENT_ID"),
            "client_secret": os.getenv("STRAVA_CLIENT_SECRET"),
            "callback_url": "https://backendstrava.vercel.app/api/webhook",
            "verify_token": STRAVA_VERIFY_TOKEN
        }
        resp = await client.post("https://www.strava.com/api/v3/push_subscriptions", data=payload)
        return resp.json()
