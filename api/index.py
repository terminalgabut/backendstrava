from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
import os
import httpx

app = FastAPI(title="Backend Strava WIB", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_wib_now():
    return datetime.now(timezone(timedelta(hours=7)))

@app.get("/", tags=["Sistem"])
async def read_root():
    return {
        "status": "online",
        "wib_time": get_wib_now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": "Siap menerima callback dari Strava"
    }

# ENDPOINT KRUSIAL: Menukar Code menjadi Refresh Token
@app.get("/api/strava/exchange", tags=["Integrasi"])
async def exchange_token(code: str):
    client_id = os.getenv("STRAVA_CLIENT_ID")
    client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        return {"error": "Isi STRAVA_CLIENT_ID & SECRET di Environment Variables Vercel dulu!"}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
            }
        )
    
    res_data = response.json()
    if response.status_code == 200:
        return {
            "status": "BERHASIL",
            "refresh_token": res_data.get("refresh_token"),
            "message": "SALIN refresh_token di atas ke Environment Variables Vercel kamu sekarang!"
        }
    return {"status": "GAGAL", "detail": res_data}
