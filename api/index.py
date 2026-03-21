from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
import os
import httpx
from supabase import create_client, Client

app = FastAPI(title="Strava & Weather Sync Pro", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inisialisasi Clients
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")) if os.getenv("SUPABASE_URL") else None

def get_wib_now():
    return datetime.now(timezone(timedelta(hours=7)))

async def get_weather(lat, lon, dt_string):
    """Fungsi pembantu untuk mengambil data cuaca historis (Opsional)."""
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key or not lat or not lon:
        return None
    
    # Mengubah start_date Strava ke Unix Timestamp
    dt_obj = datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
    timestamp = int(dt_obj.timestamp())

    async with httpx.AsyncClient() as client:
        # Menggunakan OpenWeather Timemachine API (atau Current jika waktu dekat)
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric"
        resp = await client.get(url)
        if resp.status_code == 200:
            w_data = resp.json()
            return {
                "temp": w_data.get("main", {}).get("temp"),
                "condition": w_data.get("weather", [{}])[0].get("main")
            }
    return None

async def get_new_access_token():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": os.getenv("STRAVA_CLIENT_ID"),
                "client_secret": os.getenv("STRAVA_CLIENT_SECRET"),
                "refresh_token": os.getenv("STRAVA_REFRESH_TOKEN"),
                "grant_type": "refresh_token",
            }
        )
    return response.json().get("access_token")

@app.get("/api/sync", tags=["Integrasi"])
async def sync_strava_to_db():
    if not supabase:
        return {"error": "Database/Supabase belum dikonfigurasi"}

    access_token = await get_new_access_token()
    
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {access_token}"}
        # Mengambil 100 aktivitas terbaru (disarankan per-batch agar tidak timeout)
        response = await client.get(
            "https://www.strava.com/api/v3/athlete/activities?per_page=100",
            headers=headers
        )
    
    if response.status_code != 200:
        return {"error": "Gagal kontak Strava", "details": response.json()}

    activities = response.json()
    synced_count = 0

    for act in activities:
        start_latlng = act.get("start_latlng", [])
        has_coords = len(start_latlng) == 2
        
        # 1. Logic Map & Geolocation
        lat = start_latlng[0] if has_coords else None
        lng = start_latlng[1] if has_coords else None
        polyline = act.get("map", {}).get("summary_polyline")

        # 2. Logic Weather (Hanya dipanggil jika data cuaca belum ada di DB)
        weather = None
        # Jika kamu ingin performa cepat, bagian cuaca ini bisa diproses secara async/background
        # weather = await get_weather(lat, lng, act.get("start_date_local"))

        # 3. Data Mapping Lengkap
        data = {
            "strava_id": str(act.get("id")),
            "name": act.get("name"),
            "distance": act.get("distance"),
            "moving_time": act.get("moving_time"),
            "elapsed_time": act.get("elapsed_time"),
            "type": act.get("type"),
            "start_date": act.get("start_date_local"),
            "average_speed": act.get("average_speed"),
            "max_speed": act.get("max_speed"),
            "total_elevation_gain": act.get("total_elevation_gain"),
            "summary_polyline": polyline,
            "start_lat": lat,
            "start_lng": lng,
            "average_heartrate": act.get("average_heartrate"),
            "max_heartrate": act.get("max_heartrate"),
            "calories": act.get("calories") # Hanya muncul jika ditarik dari detail id
        }

        try:
            supabase.table("activities").upsert(data, on_conflict="strava_id").execute()
            synced_count += 1
        except Exception as e:
            print(f"Error pada ID {act.get('id')}: {e}")

    return {
        "status": "success",
        "synced": synced_count,
        "timestamp_wib": get_wib_now().isoformat()
    }
