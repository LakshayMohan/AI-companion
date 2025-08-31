"""Spotify helper for mood / track & playlist retrieval.

Centralizes:
 - Client credentials token acquisition (cached in-process)
 - Mood normalization & lightweight detection
 - Searching playlists and tracks
 - Mapping mood -> query heuristics

All network I/O is async via httpx. Functions degrade gracefully when
credentials are absent.
"""
from __future__ import annotations
import time
import base64
from typing import Optional, Dict, Any, List
import re
import httpx

from . import config

_SPOTIFY_TOKEN: Optional[str] = None
_SPOTIFY_TOKEN_EXP: float = 0

async def _fetch_token() -> Optional[str]:
    global _SPOTIFY_TOKEN, _SPOTIFY_TOKEN_EXP
    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        return None
    if _SPOTIFY_TOKEN and time.time() < _SPOTIFY_TOKEN_EXP - 30:
        return _SPOTIFY_TOKEN
    basic = base64.b64encode(
        f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode()
    ).decode()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}"},
        )
    if r.status_code == 200:
        data = r.json()
        _SPOTIFY_TOKEN = data.get("access_token")
        _SPOTIFY_TOKEN_EXP = time.time() + int(data.get("expires_in", 3600))
    return _SPOTIFY_TOKEN

async def search_track(query: str) -> Optional[Dict[str, Any]]:
    token = await _fetch_token()
    if not token:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "https://api.spotify.com/v1/search",
            params={"q": query, "type": "track", "limit": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code != 200:
        return None
    data = r.json()
    items = data.get("tracks", {}).get("items", [])
    return items[0] if items else None

MOOD_TO_QUERY = {
    "happy": "happy upbeat pop",
    "sad": "acoustic mellow",
    "energetic": "energetic electronic",
    "calm": "calm ambient",
    "chill": "chill lofi beats",
    "focus": "focus study concentration",
    "romantic": "romantic love songs",
    "angry": "heavy metal",
    "sleep": "sleep relaxation ambient",
}

_MOOD_KEYWORDS = {
    'happy': ['happy', 'joy', 'delighted', 'cheerful', 'glad', 'grin', 'smile', 'upbeat'],
    'sad': ['sad', 'depressed', 'unhappy', 'down', 'blue', 'tear', 'melancholy'],
    'chill': ['chill', 'relax', 'calm', 'soothing', 'easy', 'laid-back', 'relaxed'],
    'energetic': ['energetic', 'energ', 'excited', 'pump', 'upbeat', 'dance', 'party'],
    'focus': ['focus', 'concentrate', 'study', 'work', 'productive'],
    'romantic': ['love', 'romantic', 'romance', 'affection', 'dating'],
    'angry': ['angry', 'mad', 'furious', 'rage', 'irritated'],
    'sleep': ['sleep', 'sleepy', 'rest', 'night', 'lullaby']
}

def _normalize_mood(m: str) -> str:
    mapping = {
        "relax": "chill",
        "relaxed": "chill",
        "energ": "energetic",
    }
    m = (m or '').lower()
    return mapping.get(m, m or 'mood')

def detect_mood_from_text(text: str) -> str:
    """Best-effort mood classification from freeform user text.

    Returns a canonical mood string or 'mood' fallback when unknown.
    """
    if not text:
        return 'mood'
    words = [w.lower() for w in re.findall(r"[\w-]+", text.lower())]
    for mood, kws in _MOOD_KEYWORDS.items():
        for kw in kws:
            if kw in words:
                return _normalize_mood(mood)
            for w in words:
                if kw and kw in w:
                    return _normalize_mood(mood)
    return 'mood'

async def track_for_mood(mood: str) -> Optional[Dict[str, Any]]:
    query = MOOD_TO_QUERY.get(mood.lower()) or f"{mood} song"
    return await search_track(query)

async def search_playlists(mood: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Search Spotify playlists for a mood keyword.

    Returns list of {name,url,id,image} dicts. Empty list when unavailable.
    """
    token = await _fetch_token()
    if not token:
        return []
    q = _normalize_mood(mood)
    params = {"q": q, "type": "playlist", "limit": limit}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get("https://api.spotify.com/v1/search", params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            items = (data.get("playlists") or {}).get("items") or []
            out = []
            for p in items:
                if not isinstance(p, dict):
                    continue
                image_url = None
                try:
                    imgs = p.get("images") or []
                    if imgs and isinstance(imgs, list):
                        image_url = imgs[0].get("url")
                except Exception:
                    image_url = None
                out.append({
                    "name": p.get("name"),
                    "url": (p.get("external_urls") or {}).get("spotify"),
                    "id": p.get("id"),
                    "image": image_url,
                })
            return out
    except Exception:
        return []

async def playlist_tracks(playlist_id: str) -> List[Dict[str, Any]]:
    token = await _fetch_token()
    if not token:
        return []
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
    params = {"fields": "items(track(name,artists(name),preview_url,external_urls,duration_ms,album(images)))", "limit": 100}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items") or []
            tracks = []
            for it in items:
                track = (it or {}).get("track") or {}
                if not track:
                    continue
                artists = [a.get("name") for a in (track.get("artists") or []) if a.get("name")]
                image = None
                try:
                    imgs = (track.get("album") or {}).get("images") or []
                    if imgs and isinstance(imgs, list):
                        image = imgs[0].get("url")
                except Exception:
                    image = None
                tracks.append({
                    "name": track.get("name"),
                    "artists": artists,
                    "preview_url": track.get("preview_url"),
                    "duration_ms": track.get("duration_ms"),
                    "external_url": (track.get("external_urls") or {}).get("spotify"),
                    "image": image,
                })
            return tracks
    except Exception:
        return []

__all__ = [
    "track_for_mood",
    "search_track",
    "search_playlists",
    "playlist_tracks",
    "detect_mood_from_text",
]
