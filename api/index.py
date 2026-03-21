from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
import os
import httpx
from supabase import create_client, Client

app = FastAPI(title="Strava to Database Sync", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inisialisasi Supabase
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key) if url and key else None

def get_wib_now():
    return datetime.now(timezone(timedelta(hours=7)))

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
    """Mengambil semua data dari Strava dan menyimpannya ke Database."""
    if not supabase:
        return {"error": "Database tidak terkonfigurasi"}

    access_token = await get_new_access_token()
    
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {access_token}"}
        # Mengambil 200 aktivitas terbaru (maksimal per halaman di Strava)
        response = await client.get(
            "https://www.strava.com/api/v3/athlete/activities?per_page=200",
            headers=headers
        )
    
    if response.status_code != 200:
        return {"error": "Gagal ambil data Strava", "details": response.json()}

    activities = response.json()
    synced_count = 0

    for act in activities:
        # Menyiapkan data untuk database
        data = {
            "strava_id": str(act.get("id")),
            "name": act.get("name"),
            "distance": act.get("distance"), # dalam meter
            "moving_time": act.get("moving_time"), # dalam detik
            "type": act.get("type"),
            "start_date": act.get("start_date_local"),
            "average_speed": act.get("average_speed"),
            "max_speed": act.get("max_speed")
        }

        # Simpan ke tabel 'activities' (Gunakan upsert agar tidak duplikat berdasarkan strava_id)
        try:
            supabase.table("activities").upsert(data, on_conflict="strava_id").execute()
            synced_count += 1
        except Exception as e:
            print(f"Error syncing {data['strava_id']}: {e}")

    return {
        "status": "success",
        "total_processed": len(activities),
        "total_synced": synced_count,
        "timestamp_wib": get_wib_now().isoformat()
    }
