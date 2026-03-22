import httpx
import os

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

async def get_weather_data(lat: float, lng: float):
    """Mengambil data cuaca asli berdasarkan koordinat lari."""
    if not lat or not lng or not OPENWEATHER_API_KEY:
        return {"temp": 28.0, "wind": 12.0, "hum": 65} # Fallback jika gagal

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Gunakan API Current Weather atau One Call
            url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lng}&appid={OPENWEATHER_API_KEY}&units=metric"
            resp = await client.get(url)
            
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "temp": round(data["main"]["temp"], 1),
                    "wind": round(data["wind"]["speed"] * 3.6, 1), # Konversi m/s ke km/h
                    "hum": data["main"]["humidity"]
                }
    except Exception as e:
        print(f"Weather Engine Error: {e}")
    
    return {"temp": 28.0, "wind": 12.0, "hum": 65}
