from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

# 1. Inisialisasi App dengan Metadata
app = FastAPI(
    title="Python Backend API",
    description="Serverless Backend running on Vercel",
    version="1.0.0"
)

# 2. Konfigurasi CORS (Penting jika diakses dari Web/Frontend lain)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Ganti dengan domain frontend kamu saat production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Struktur Endpoint yang Lebih Rapi
@app.get("/", tags=["Root"])
async def read_root():
    """Endpoint utama untuk mengecek status aplikasi."""
    return {
        "status": "online",
        "framework": "FastAPI",
        "environment": os.getenv("VERCEL_ENV", "development"),
        "message": "FastAPI on Vercel is running smoothly!"
    }

@app.get("/health", tags=["Monitoring"])
async def health_check():
    """Endpoint untuk health check sistem."""
    return {
        "status": "healthy",
        "uptime": "stable"
    }

# 4. Contoh Penanganan Error Sederhana
@app.get("/api/info")
async def get_info():
    return {
        "project": "Backend Python",
        "author": "Mochammad Farid",
        "features": ["FastAPI", "Serverless", "Vercel"]
    }
