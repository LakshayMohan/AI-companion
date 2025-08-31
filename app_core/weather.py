"""Weather lookup combining geocoding (OpenCage) and forecast (Open-Meteo)."""
from __future__ import annotations
from typing import Optional, Dict, Any
import httpx
from . import config

async def geocode_location(location: str) -> Optional[Dict[str, float]]:
    if not config.OPENCAGE_API_KEY:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "https://api.opencagedata.com/geocode/v1/json",
            params={"q": location, "key": config.OPENCAGE_API_KEY, "limit": 1},
        )
    if r.status_code != 200:
        return None
    data = r.json()
    results = data.get("results") or []
    if not results:
        return None
    g = results[0].get("geometry", {})
    return {"lat": g.get("lat"), "lng": g.get("lng")}

async def fetch_weather(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lng, "current_weather": True},
        )
    if r.status_code != 200:
        return None
    return r.json().get("current_weather")

async def get_weather_for_location(location: str) -> Optional[str]:
    geo = await geocode_location(location)
    if not geo:
        return None
    wx = await fetch_weather(geo["lat"], geo["lng"])
    if not wx:
        return None
    return (
        f"Weather in {location}: {wx.get('temperature')}°C, wind "
        f"{wx.get('windspeed')} km/h"
    )

__all__ = ["get_weather_for_location"]
