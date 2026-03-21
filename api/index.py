from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
import os
import httpx
from supabase import create_client, Client

app = FastAPI(title="Strava Deep Sync Pro", version="6.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inisialisasi Supabase
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

async def get_new_access_token():
    async with httpx.AsyncClient() as client:
        payload = {
            "client_id": os.getenv("STRAVA_CLIENT_ID"),
            "client_secret": os.getenv("STRAVA_CLIENT_SECRET"),
            "refresh_token": os.getenv("STRAVA_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        }
        response = await client.post("https://www.strava.com/oauth/token", data=payload)
        return response.json().get("access_token")

@app.get("/api/sync/deep", tags=["Integrasi"])
async def deep_sync_strava():
    """Sync mendalam: Mengambil detail per aktivitas untuk mendapatkan Kalori & Data Lengkap."""
    if not supabase: return {"error": "DB not connected"}
    
    access_token = await get_new_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Ambil list ringkasan (Summary)
        list_resp = await client.get(
            "https://www.strava.com/api/v3/athlete/activities?per_page=30", 
            headers=headers
        )
        summary_activities = list_resp.json()
        
        synced_count = 0
        for summary in summary_activities:
            strava_id = summary.get("id")
            
            # 2. DEEP SYNC: Ambil detail lengkap untuk tiap ID
            # Ini kunci untuk mendapatkan 'calories' dan 'device_name' yang akurat
            detail_resp = await client.get(
                f"https://www.strava.com/api/v3/activities/{strava_id}", 
                headers=headers
            )
            
            if detail_resp.status_code != 200:
                continue
                
            act = detail_resp.json()
            start_coords = act.get("start_latlng", [])
            
            # Mapping ke kolom database kamu
            record = {
                "strava_id": str(act.get("id")),
                "name": act.get("name"),
                "distance": act.get("distance"),
                "moving_time": act.get("moving_time"),
                "type": act.get("type"),
                "start_date": act.get("start_date_local"),
                "average_speed": act.get("average_speed"),
                "max_speed": act.get("max_speed"),
                "calories": act.get("calories"), # Sekarang terisi!
                "total_elevation_gain": act.get("total_elevation_gain"),
                "average_heartrate": act.get("average_heartrate"),
                "max_heartrate": act.get("max_heartrate"),
                "summary_polyline": act.get("map", {}).get("summary_polyline"),
                "timezone": act.get("timezone"),
                "device_name": act.get("device_name"), # Sekarang terisi!
                "start_lat": start_coords[0] if len(start_coords) == 2 else None,
                "start_lng": start_coords[1] if len(start_coords) == 2 else None,
            }

            try:
                supabase.table("activities").upsert(record, on_conflict="strava_id").execute()
                synced_count += 1
            except Exception as e:
                print(f"Error upsert {strava_id}: {e}")

    return {
        "status": "Deep Sync Berhasil",
        "synced_count": synced_count,
        "note": "Data kalori dan perangkat sekarang sudah masuk ke DB."
    }
