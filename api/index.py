from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
import os

# 1. Inisialisasi FastAPI dengan Metadata yang Rapi
app = FastAPI(
    title="Python Backend API",
    description="Backend Serverless untuk Vercel dengan sinkronisasi WIB (UTC+7)",
    version="2.0.0"
)

# 2. Konfigurasi CORS (Agar bisa diakses dari frontend mana pun)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Helper: Fungsi Waktu Indonesia Barat (WIB)
def get_wib_now():
    # Membuat timezone UTC+7
    wib_tz = timezone(timedelta(hours=7))
    return datetime.now(wib_tz)

# 4. Endpoints
@app.get("/", tags=["Utama"])
async def read_root():
    """Halaman utama untuk verifikasi status server."""
    now = get_wib_now()
    return {
        "server_status": "online",
        "wib_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Jakarta (UTC+7)",
        "framework": "FastAPI on Vercel"
    }

@app.get("/health", tags=["Monitoring"])
async def health_check():
    """Mengecek kesehatan sistem dan status konfigurasi API."""
    # Mengecek apakah variabel Strava sudah terpasang di Vercel Settings
    strava_configured = all([
        os.getenv("STRAVA_CLIENT_ID"),
        os.getenv("STRAVA_CLIENT_SECRET"),
        os.getenv("STRAVA_REFRESH_TOKEN")
    ])
    
    return {
        "status": "healthy",
        "timestamp_wib": get_wib_now().isoformat(),
        "config_check": {
            "strava_api": strava_configured,
            "database": bool(os.getenv("DATABASE_URL")),
            "vercel_env": os.getenv("VERCEL_ENV", "production")
        }
    }

@app.get("/api/info", tags=["Utama"])
async def get_info():
    """Metadata aplikasi dan informasi pengembang."""
    return {
        "project": "Backend Python",
        "author": "Mochammad Farid",
        "tech_stack": ["FastAPI", "Python 3.x", "Vercel Serverless"],
        "repository": "GitHub Connected"
    }

# 5. Placeholder Endpoint untuk Integrasi Strava
@app.get("/api/strava/status", tags=["Integrasi"])
async def strava_status():
    """Endpoint khusus untuk memantau status koneksi Strava."""
    client_id = os.getenv("STRAVA_CLIENT_ID")
    if not client_id:
        return {
            "status": "missing_configuration",
            "instruction": "Tambahkan STRAVA_CLIENT_ID di Environment Variables Vercel."
        }
    return {
        "status": "ready",
        "client_id_detected": True,
        "last_check_wib": get_wib_now().strftime("%H:%M:%S")
    }
