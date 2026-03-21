from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
import os
import httpx
from supabase import create_client, Client

app = FastAPI(title="Strava Deep Sync Pro", version="6.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inisialisasi Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None

def get_wib_now():
    return datetime.now(timezone(timedelta(hours=7)))

async def get_new_access_token():
    """Mengambil access token baru menggunakan refresh token."""
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

@app.get("/", tags=["Sistem"])
async def root():
    return {"status": "online", "message": "Backend Strava siap. Gunakan /api/sync untuk sinkronisasi."}

@app.get("/api/sync", tags=["Integrasi"])
async def sync_strava():
    if not supabase:
        return {"error": "Database Supabase tidak terhubung."}
    
    try:
        access_token = await get_new_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
    except Exception as e:
        return {"error": "Gagal refresh token Strava", "details": str(e)}
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Ambil list ringkasan
        list_resp = await client.get(
            "https://www.strava.com/api/v3/athlete/activities?per_page=30", 
            headers=headers
        )
        
        if list_resp.status_code != 200:
            return {"error": "Gagal mengambil list aktivitas", "details": list_resp.json()}
            
        summary_activities = list_resp.json()
        synced_count = 0
        errors = []

        for summary in summary_activities:
            strava_id = summary.get("id")
            if not strava_id: continue
            
            try:
                # 2. DEEP SYNC: Ambil detail (untuk Kalori, Device, dan LAPS/SPLITS)
                detail_resp = await client.get(
                    f"https://www.strava.com/api/v3/activities/{strava_id}", 
                    headers=headers
                )
                
                if detail_resp.status_code != 200:
                    errors.append(f"ID {strava_id} skip: Detail tidak ditemukan.")
                    continue
                    
                act = detail_resp.json()
                start_coords = act.get("start_latlng", [])
                
                # Mapping data lengkap ke database
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
                    
                    # DATA LAPS MASUK KE SINI
                    "splits_metric": act.get("splits_metric")
                }

                # Simpan/Update ke Supabase
                supabase.table("activities").upsert(record, on_conflict="strava_id").execute()
                synced_count += 1
                
            except Exception as e:
                errors.append(f"ID {strava_id} error: {str(e)}")

    return {
        "status": "success",
        "synced_count": synced_count,
        "errors": errors if errors else "none",
        "timestamp_wib": get_wib_now().strftime("%Y-%m-%d %H:%M:%S")
    }
