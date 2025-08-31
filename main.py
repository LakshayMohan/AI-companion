# main.py - AI Voice Companion Backend
"""
FastAPI server providing real-time voice interaction capabilities.

This application combines:
- Real-time audio transcription (AssemblyAI WebSocket streaming)
- LLM conversation (Google Gemini with streaming responses)
- Text-to-speech synthesis (Murf WebSocket API)
- Optional services: weather data, web search (Tavily), Spotify playlist recommendations
- Privacy-focused runtime API key management (no persistent storage)

Key Features:
- WebSocket-based audio streaming for low-latency transcription
- Automatic web search augmentation for factual queries
- Session-based chat history with configurable limits
- Mood detection for music recommendations
- Thread-safe direct event dispatching for faster UI updates
"""

import os
import logging
import json
import base64
from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect, Body, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import websockets
import assemblyai as aai
import google.generativeai as genai
from app_core import config, history, spotify, search as core_search
try:
    from tavily import TavilyClient
except Exception:
    TavilyClient = None
from collections import defaultdict
import asyncio
import secrets
import wave
import io
import time
from datetime import datetime
import time as _time
import re

# --- Logging ---
_app_env = os.getenv("APP_ENV", "dev").lower()
_log_level = logging.WARNING if _app_env in ("prod", "production") else logging.INFO
logging.basicConfig(level=_log_level, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Load Secrets ---
load_dotenv()  # retained for compatibility but keys are NOT pulled at startup anymore
# Runtime-only keys (start as None; populated via /config/keys). We alias local variables for speed/readability.
MURF_API_KEY = config.MURF_API_KEY
ASSEMBLYAI_API_KEY = config.ASSEMBLYAI_API_KEY
GEMINI_API_KEY = config.GEMINI_API_KEY
MURF_CONTEXT_ID = config.MURF_CONTEXT_ID
MURF_WS_URL = config.MURF_WS_URL
OPENCAGE_API_KEY = config.OPENCAGE_API_KEY
TRAVILY_API_KEY = config.TRAVILY_API_KEY
SPOTIFY_CLIENT_ID = config.SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET = config.SPOTIFY_CLIENT_SECRET

# Token cache
_spotify_token: str | None = None
_spotify_token_expires_at: float = 0.0

# Configure dependent SDKs if keys are present (will be reconfigured when keys are set at runtime)
if ASSEMBLYAI_API_KEY:
    try:
        aai.settings.api_key = ASSEMBLYAI_API_KEY
    except Exception:
        pass
else:
    logging.warning("ASSEMBLYAI_API_KEY not set.")

if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception:
        pass
else:
    logging.warning("GEMINI_API_KEY not set.")

# FastAPI app and static assets
app = FastAPI()

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Session Chat Memory ---
chat_history = history.chat_history
append_to_history = history.append_to_history
MAX_HISTORY_LENGTH = history.MAX_HISTORY_LENGTH

# In-memory mapping of temporary TTS identifiers to provider audio URLs (kept server-side)
tts_cache: dict = {}

async def get_spotify_token() -> str | None:
    """Obtain and cache a Spotify client-credentials token for API access.
    
    Uses OAuth2 Client Credentials flow to authenticate with Spotify API.
    Tokens are cached in memory until expiry (with 60s safety margin).
    
    Returns:
        Bearer token string for API authorization, or None if credentials 
        are missing or request fails.
    """
    global _spotify_token, _spotify_token_expires_at
    # return cached token if still valid (with 60s leeway)
    if _spotify_token and _spotify_token_expires_at - 60 > _time.time():
        return _spotify_token

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        logging.warning("Spotify CLIENT_ID/CLIENT_SECRET not set in environment")
        return None

    token_url = "https://accounts.spotify.com/api/token"
    payload = {"grant_type": "client_credentials"}
    # Use HTTP Basic Auth per Spotify spec
    try:
        async with httpx.AsyncClient() as client:
            auth = httpx.BasicAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
            resp = await client.post(token_url, data=payload, auth=auth, timeout=10)
            text = resp.text
            try:
                resp.raise_for_status()
            except Exception as e:
                logging.error(f"Spotify token request failed (status={resp.status_code}): {text}")
                raise
            try:
                data = resp.json()
            except Exception:
                logging.error(f"Spotify token response JSON parse failed: {text}")
                return None
            token = data.get("access_token")
            expires_in = int(data.get("expires_in", 3600))
            if token:
                _spotify_token = token
                _spotify_token_expires_at = _time.time() + expires_in
                logging.info("Obtained Spotify token, expires in %s seconds", expires_in)
                return _spotify_token
    except Exception as e:
        logging.error(f"Failed to obtain Spotify token: {e}")
        return None


def _normalize_mood(q: str) -> str:
    """Normalize mood query strings for improved Spotify search results.
    
    Maps common mood keywords to canonical search terms that work well
    with Spotify's playlist search algorithm.
    
    Args:
        q: Raw mood string from user input or mood detection
        
    Returns:
        Normalized mood string optimized for Spotify search
    """
    m = (q or "").strip().lower()
    mapping = {
        "happy": "happy",
        "sad": "sad",
        "chill": "chill",
        "relax": "chill",
        "energetic": "party",
        "focus": "focus",
        "romantic": "romance",
        "angry": "metal",
        "sleep": "sleep",
    }
    return mapping.get(m, m or "mood")


def _detect_mood_from_text(text: str) -> str:
    """Extract mood indicators from user text using keyword matching.
    
    Analyzes user input for emotional keywords and returns the most specific
    mood detected. Used to trigger relevant Spotify playlist recommendations.
    
    Algorithm:
    1. Tokenize input text into words (handling hyphens)
    2. Check for exact word matches first (higher precision)
    3. Fall back to substring matching for word variants
    4. Return first match found (priority order matters)
    
    Args:
        text: User's spoken or typed input
        
    Returns:
        Detected mood string ('happy', 'sad', 'chill', etc.) or 'mood' 
        as generic fallback when no specific mood is found.
    """
    if not text:
        return "mood"
    t = text.lower()

    # User-provided whitelist of keywords per mood
    checks = {
        'happy': ['happy', 'joy', 'delighted', 'cheerful', 'glad', 'grin', 'smile', 'upbeat'],
        'sad': ['sad', 'depressed', 'unhappy', 'down', 'blue', 'tear', 'melancholy'],
        'chill': ['chill', 'relax', 'calm', 'soothing', 'easy', 'laid-back', 'relaxed'],
        'energetic': ['energetic', 'energ', 'excited', 'pump', 'upbeat', 'dance', 'party'],
        'focus': ['focus', 'concentrate', 'study', 'work', 'productive'],
        'romantic': ['love', 'romantic', 'romance', 'affection', 'dating'],
        'angry': ['angry', 'mad', 'furious', 'rage', 'irritated'],
        'sleep': ['sleep', 'sleepy', 'rest', 'night', 'lullaby']
    }

    # Tokenize using regex to capture words and hyphenated tokens (e.g. 'laid-back')
    words = [w.lower() for w in re.findall(r"[\w-]+", t)]

    # Priority-ordered checks (dictionary preserves insertion order on Python 3.7+)
    for mood, kws in checks.items():
        for kw in kws:
            # match whole word first
            if kw in words:
                return _normalize_mood(mood)
            # then allow substring match to catch variants (e.g. 'energ' in 'energized')
            for w in words:
                if kw and kw in w:
                    return _normalize_mood(mood)

    return 'mood'


async def search_spotify_playlists(mood: str, limit: int = 3):
    """Search Spotify for playlists matching the given mood.
    
    Performs authenticated search using cached access token and returns
    playlist metadata for frontend display.
    
    Args:
        mood: Mood string to search for (will be normalized)
        limit: Maximum number of playlists to return
        
    Returns:
        List of playlist dictionaries with name, url, id fields,
        or empty list if token unavailable or search fails.
    """
    token = await get_spotify_token()
    if not token:
        return []
    q = _normalize_mood(mood)
    search_url = "https://api.spotify.com/v1/search"
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(search_url, params={"q": q, "type": "playlist", "limit": limit}, headers={"Authorization": f"Bearer {await get_spotify_token()}"})
        # We don't need to parse here; the caller uses higher-level helpers.
    


@app.post("/weather")
async def get_weather(payload: dict = Body(...)):
    """Retrieve current weather data for a specified location.
    
    Supports both coordinate-based and text-based location queries.
    Uses OpenCage for geocoding and Open-Meteo for weather data.
    
    Request payload options:
    - {"location": "New York, NY"} - Text location (requires OpenCage API key)
    - {"lat": 40.7, "lon": -74.0} - Direct coordinates
    
    Returns:
        JSON with location coordinates and current weather conditions,
        or HTTP error if location not found or weather service unavailable.
    """
    lat = payload.get("lat")
    lon = payload.get("lon")
    location = payload.get("location")

    # If location string provided, geocode via OpenCage
    if (not lat or not lon) and location:
        if not OPENCAGE_API_KEY:
            raise HTTPException(status_code=500, detail="Geocoding not configured.")
        try:
            geocode_url = "https://api.opencagedata.com/geocode/v1/json"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(geocode_url, params={"q": location, "key": OPENCAGE_API_KEY, "limit": 1})
                resp.raise_for_status()
                data = resp.json()
                results = data.get('results') or []
                if not results:
                    raise HTTPException(status_code=404, detail="Location not found")
                geometry = results[0].get('geometry') or {}
                lat = geometry.get('lat')
                lon = geometry.get('lng')
        except HTTPException:
            raise
        except Exception as e:
            logging.error(f"OpenCage geocode error: {e}")
            raise HTTPException(status_code=502, detail="Geocoding service failed")

    if not lat or not lon:
        raise HTTPException(status_code=400, detail="Missing location coordinates or location string")

    # Query Open-Meteo for current weather
    try:
        weather_url = "https://api.open-meteo.com/v1/forecast"
        params = {"latitude": lat, "longitude": lon, "current_weather": True, "timezone": "auto"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(weather_url, params=params)
            resp.raise_for_status()
            data = resp.json()
            current = data.get('current_weather') or {}
            return {"location": {"lat": lat, "lon": lon}, "current": current}
    except Exception as e:
        logging.error(f"Weather lookup failed: {e}")
        raise HTTPException(status_code=502, detail="Weather lookup failed")


@app.get('/search')
async def web_search(q: str):
    """Perform web search using Tavily API for real-time information.
    
    Provides access to current web content for fact-checking and research.
    Only available when Tavily API key is configured at runtime.
    
    Args:
        q: Search query string (URL parameter)
        
    Returns:
        JSON with query and content fields, or HTTP 503 if Tavily unavailable.
        Content is extracted from top search result or direct answer when available.
    """
    if not q or q.strip() == '':
        raise HTTPException(status_code=400, detail='Missing query')
    q = q.strip()
    # Tavily-only search. If not configured, inform the caller.
    if not (TRAVILY_API_KEY and TavilyClient is not None):
        raise HTTPException(status_code=503, detail='Search service is not configured (missing TRAVILY_API_KEY)')

    try:
        client = TavilyClient(api_key=TRAVILY_API_KEY)
        def call_search():
            return client.search(q)
        res = await asyncio.to_thread(call_search)

        if not res:
            return {"query": q, "content": "No results returned by Tavily"}

        # Normalize to top content string when possible
        if isinstance(res, list) and len(res) > 0:
            top = res[0]
            if isinstance(top, dict):
                content = top.get('content') or top.get('title') or top.get('raw_content')
                if content:
                    return {"query": q, "content": content}
            return {"query": q, "content": str(top)}

        if isinstance(res, dict):
            for key in ("content", "answer", "summary", "results", "data", "items"):
                val = res.get(key)
                if val:
                    if isinstance(val, list) and len(val) > 0:
                        first = val[0]
                        if isinstance(first, dict):
                            return {"query": q, "content": str(first.get('content') or first.get('title') or first.get('raw_content') or first)}
                        return {"query": q, "content": str(first)}
                    return {"query": q, "content": str(val)}
            import json as _json
            return {"query": q, "content": _json.dumps(res)}

        return {"query": q, "content": str(res)}
    except Exception as e:
        logging.error(f'Tavily search failed: {e}')
        raise HTTPException(status_code=502, detail='Search failed: Tavily error')
        # Transient per-session storage
        self.session_websockets = {}
        self.pending_transcriptions = defaultdict(list)
        self.final_transcripts = {}
        self.murf_audio_chunks = defaultdict(list)

class AudioStreamer:
    """Manages real-time audio streaming and transcription sessions.
    
    Coordinates between:
    - AssemblyAI Universal Streaming for real-time transcription
    - WebSocket connections to frontend clients
    - LLM response generation and TTS synthesis
    - Session state and cleanup
    
    Key Features:
    - Thread-safe direct event dispatch (no batching delays)
    - Automatic LLM triggering on end-of-turn transcripts
    - Per-session audio chunk storage for TTS assembly
    - Graceful cleanup on disconnection
    """
    def __init__(self):
        # Track active session state and client connections
        self.active_sessions = {}
        self.streaming_clients = {}
        self.session_websockets = {}
        self.pending_transcriptions = defaultdict(list)  # Deprecated: kept for compatibility
        self.final_transcripts = {}
        self.murf_audio_chunks = defaultdict(list)

    async def start_streaming(self, session_id: str, websocket=None):
        """Initialize a new audio streaming session with AssemblyAI transcription.
        
        Sets up real-time transcription pipeline without local audio recording.
        Configures event handlers for direct WebSocket message dispatch.
        
        Args:
            session_id: Unique identifier for this streaming session
            websocket: WebSocket connection to frontend client
            
        Returns:
            Session ID on success, None if initialization fails
            
        Side Effects:
            - Creates AssemblyAI streaming client with event handlers
            - Caches event loop reference for thread-safe scheduling  
            - Stores session state for cleanup management
        """
        self.websocket = websocket
        
        # Store websocket reference for this session
        if not hasattr(self, 'session_websockets'):
            self.session_websockets = {}
        self.session_websockets[session_id] = websocket
        
        # Initialize AssemblyAI Universal Streaming client
        try:
            if ASSEMBLYAI_API_KEY:
                from assemblyai.streaming.v3 import (
                    BeginEvent,
                    StreamingClient,
                    StreamingClientOptions,
                    StreamingError,
                    StreamingEvents,
                    StreamingParameters,
                    StreamingSessionParameters,
                    TerminationEvent,
                    TurnEvent,
                )
                
                def on_begin(client_instance, event: BeginEvent):
                    """Handle AssemblyAI session start event."""
                    logging.info(f"AssemblyAI session started: {event.id}")
                
                def on_turn(client_instance, event: TurnEvent):
                    """Process real-time transcription events with direct WebSocket dispatch.
                    
                    Key optimization: Events are sent immediately via thread-safe scheduling
                    instead of batching, reducing UI update latency.
                    
                    Args:
                        event: TurnEvent containing transcript, formatting flags, and metadata
                    """
                    if not event.transcript:
                        return
                    ws = self.session_websockets.get(session_id)
                    payload = {
                        "type": "transcription",
                        "transcript": event.transcript,
                        "end_of_turn": event.end_of_turn,
                        "turn_is_formatted": event.turn_is_formatted,
                        "turn_order": event.turn_order
                    }
                    try:
                        loop = getattr(self, 'loop', None)
                        if loop is None:
                            try:
                                loop = asyncio.get_running_loop()
                                self.loop = loop
                            except RuntimeError:
                                loop = None
                        if ws and loop:
                            loop.call_soon_threadsafe(asyncio.create_task, ws.send_text(json.dumps(payload)))
                    except Exception as ex:
                        logging.debug(f"Failed scheduling transcription send: {ex}")
                    if event.end_of_turn and event.turn_is_formatted:
                        if not hasattr(self, 'final_transcripts'):
                            self.final_transcripts = {}
                        self.final_transcripts[session_id] = event.transcript
                
                def on_terminated(client_instance, event: TerminationEvent):
                    """Handle AssemblyAI session termination."""
                    logging.info(f"AssemblyAI session terminated: {event.audio_duration_seconds} seconds")
                
                def on_error(client_instance, error: StreamingError):
                    """Handle AssemblyAI streaming errors."""
                    logging.error(f"AssemblyAI error: {error}")
                
                client = StreamingClient(
                    StreamingClientOptions(
                        api_key=ASSEMBLYAI_API_KEY,
                        api_host="streaming.assemblyai.com",
                    )
                )
                
                client.on(StreamingEvents.Begin, on_begin)
                client.on(StreamingEvents.Turn, on_turn)
                client.on(StreamingEvents.Termination, on_terminated)
                client.on(StreamingEvents.Error, on_error)
                
                # cache loop for callbacks
                try:
                    self.loop = asyncio.get_running_loop()
                except RuntimeError:
                    self.loop = None
                client.connect(
                    StreamingParameters(
                        sample_rate=16000,
                        format_turns=True,
                    )
                )
                
                self.streaming_clients[session_id] = client
                logging.info(f"AssemblyAI Universal Streaming client started for session: {session_id}")
            else:
                logging.warning("AssemblyAI API key not set. Transcription disabled.")
                self.streaming_clients[session_id] = None
        except Exception as e:
            logging.error(f"Failed to initialize AssemblyAI Universal Streaming client: {e}")
            self.streaming_clients[session_id] = None
        
        self.active_sessions[session_id] = {
            'start_time': time.time()
        }
        logging.info(f"Started streaming session {session_id}")
        return session_id
    

    
    async def stream_audio_data(self, session_id: str, audio_data: bytes):
        """Process incoming audio data and manage transcription/LLM pipeline.
        
        Handles the main audio processing workflow:
        1. Forward raw PCM data to AssemblyAI for transcription
        2. Check for completed transcripts and trigger LLM responses
        3. Manage session state and error handling
        
        Args:
            session_id: Session identifier for routing
            audio_data: Raw PCM audio bytes from client
            
        Note: Transcription events are sent directly from AssemblyAI callbacks
        for minimum latency. This method primarily handles LLM triggering.
        """
        if session_id not in self.active_sessions:
            logging.warning(f"Received audio data for unknown session: {session_id}")
            return
        
        # Send audio data to AssemblyAI for real-time transcription
        if session_id in self.streaming_clients and self.streaming_clients[session_id]:
            try:
                # AssemblyAI expects raw PCM audio data
                self.streaming_clients[session_id].stream(audio_data)
            except Exception as e:
                logging.error(f"Error streaming audio to AssemblyAI: {e}")
        # Direct sending handled in callback; just fetch websocket ref
        session_websocket = self.session_websockets.get(session_id)

        # Trigger LLM streaming if final transcript captured
        if hasattr(self, 'final_transcripts') and session_id in self.final_transcripts:
            final_transcript = self.final_transcripts[session_id]
            logging.debug("Processing final transcript with LLM")
            asyncio.create_task(self.stream_llm_response(session_id, final_transcript, session_websocket))
            del self.final_transcripts[session_id]

        logging.debug(f"Streamed {len(audio_data)} bytes to session {session_id}")
    
    async def stop_streaming(self, session_id: str):
        """Clean shutdown of streaming session and associated resources.
        
        Performs comprehensive cleanup:
        - Disconnects AssemblyAI streaming client
        - Removes session from active tracking
        - Cleans up WebSocket references and transcription caches
        - Logs session duration for monitoring
        
        Args:
            session_id: Session to terminate
            
        Returns:
            Session ID if found and cleaned, None if session was unknown
        """
        if session_id not in self.active_sessions:
            logging.warning(f"Attempted to stop unknown streaming session: {session_id}")
            return None
        
        session = self.active_sessions[session_id]
        
        # Close AssemblyAI Universal Streaming client
        if session_id in self.streaming_clients and self.streaming_clients[session_id]:
            try:
                self.streaming_clients[session_id].disconnect(terminate=True)
                logging.debug("AssemblyAI Universal Streaming client disconnected")
            except Exception as e:
                logging.error(f"Error disconnecting AssemblyAI Universal Streaming client: {e}")
            finally:
                del self.streaming_clients[session_id]
        
        duration = time.time() - session['start_time']
        logging.info(f"Stopped streaming session {session_id} (duration: {duration:.2f}s)")
        
        # Clean up
        del self.active_sessions[session_id]
        if hasattr(self, 'session_websockets') and session_id in self.session_websockets:
            del self.session_websockets[session_id]
    # pending_transcriptions deprecated (direct push now)
        if hasattr(self, 'final_transcripts') and session_id in self.final_transcripts:
            del self.final_transcripts[session_id]
        if session_id in self.murf_audio_chunks:
            try:
                del self.murf_audio_chunks[session_id]
            except Exception:
                pass
        return session_id
    
    async def stream_llm_response(self, session_id: str, user_text: str, websocket):
        """Generate and stream LLM responses with integrated TTS and optional services.
        
        Orchestrates the complete response pipeline:
        1. Intent detection (weather queries, factual questions)
        2. Optional web search augmentation via Tavily
        3. Streaming LLM generation with Google Gemini
        4. Concurrent TTS synthesis via Murf WebSocket
        5. Mood detection and Spotify playlist recommendations
        
        Key Features:
        - Automatic web search context injection for factual queries
        - Real-time streaming of both text and audio responses
        - Background playlist recommendations based on detected mood
        - Comprehensive error handling and fallback TTS
        
        Args:
            session_id: Session context for history and state
            user_text: Final transcribed user input
            websocket: Client connection for response streaming
        """
        try:
            if not GEMINI_API_KEY:
                logging.error("Gemini API key not set")
                return
            # Quick intent detection: handle weather queries directly. Web search now auto-augments factual queries.
            def is_weather_query(t: str) -> bool:
                """Detect weather-related queries for direct handling."""
                kws = ['weather', 'temperature', 'forecast']
                lt = (t or '').lower()
                return any(k in lt for k in kws)
            
            def is_fact_query(t: str) -> bool:
                """Heuristic detection of queries that benefit from web search augmentation.
                
                Triggers Tavily search for:
                - Questions (containing '?')
                - Queries starting with interrogative words
                - Requests for current/latest information
                """
                if not t:
                    return False
                lt = t.lower().strip()
                if '?' in lt:
                    return True
                starters = ('who ', 'what ', 'when ', 'where ', 'why ', 'how ', 'latest ', 'current ', 'news ', 'tell me')
                return lt.startswith(starters)

            async def answer_weather(text: str):
                """Provide current weather information for extracted or default location.
                
                Processing pipeline:
                1. Extract location from user text using regex patterns
                2. Geocode location to coordinates via OpenCage API
                3. Fetch weather data from Open-Meteo API
                4. Format response with temperature, wind, and conditions
                
                Args:
                    text: User query containing potential location references
                    
                Returns:
                    Formatted weather string or error message
                """
                # extract location if present ("in London")
                import urllib.parse
                loc = None
                m = re.search(r"in\s+([A-Za-z\s,]+)$", text.strip(), re.IGNORECASE)
                if m:
                    loc = m.group(1).strip()
                # fallback: try to find 'in <place>' anywhere
                if not loc:
                    m2 = re.search(r"in\s+([A-Za-z\s,]+)", text, re.IGNORECASE)
                    if m2:
                        loc = m2.group(1).strip()
                if not loc:
                    loc = 'London'

                # geocode via OpenCage
                if not config.OPENCAGE_API_KEY:
                    return "I cannot look up live weather because geocoding is not configured."
                try:
                    geocode_url = 'https://api.opencagedata.com/geocode/v1/json'
                    async with httpx.AsyncClient(timeout=8) as client:
                        resp = await client.get(geocode_url, params={'q': loc, 'key': config.OPENCAGE_API_KEY, 'limit': 1})
                        resp.raise_for_status()
                        data = resp.json()
                        results = data.get('results') or []
                        if not results:
                            return f"I couldn't find the location {loc}."
                        geom = results[0].get('geometry') or {}
                        lat = geom.get('lat')
                        lon = geom.get('lng')
                except Exception as e:
                    logging.error(f"Geocode error: {e}")
                    return "Geocoding failed."

                # get weather from Open-Meteo
                try:
                    weather_url = 'https://api.open-meteo.com/v1/forecast'
                    params = {'latitude': lat, 'longitude': lon, 'current_weather': True, 'timezone': 'auto'}
                    async with httpx.AsyncClient(timeout=8) as client:
                        resp = await client.get(weather_url, params=params)
                        resp.raise_for_status()
                        w = resp.json().get('current_weather') or {}
                        if not w:
                            return f"No weather data available for {loc}."
                        temp = w.get('temperature')
                        wind = w.get('windspeed')
                        weather_code = w.get('weathercode')
                        return f"Current weather in {loc}: {temp}°C, wind {wind} km/h, weather code {weather_code}."
                except Exception as e:
                    logging.error(f"Weather API error: {e}")
                    return "Weather lookup failed."

            async def answer_search(text: str):
                if not TRAVILY_API_KEY:
                    return "Search unavailable: Tavily key not configured."
                # Prefer centralized helper; if it fails we fall back to direct client (optional)
                result = await core_search.tavily_search(text)
                if result:
                    return result
                # Fallback attempt using Tavily client library if installed
                if TavilyClient is None:
                    return "Search failed (client library unavailable)."
                try:
                    client = TavilyClient(api_key=TRAVILY_API_KEY)
                    raw = await asyncio.to_thread(lambda: client.search(text))
                    if not raw:
                        return f"No search results for: {text}"
                    return str(raw)[:1200]
                except Exception as e:
                    logging.error(f"Tavily fallback search error: {e}")
                    return "Search failed due to an internal error."

            # If user asked for weather or search, handle directly and return
            if is_weather_query(user_text):
                answer = await answer_weather(user_text)
                # send to client as LLM streaming messages
                try:
                    await websocket.send_text(json.dumps({"type": "llm_start", "transcript": user_text}))
                    await websocket.send_text(json.dumps({"type": "llm_chunk", "text": answer, "is_complete": True}))
                    await websocket.send_text(json.dumps({"type": "llm_complete", "full_response": answer, "is_complete": True}))
                    append_to_history(session_id, "user", user_text)
                    append_to_history(session_id, "model", answer)
                except Exception:
                    pass
                return

            # Optional web search augmentation (prepend brief results to model context)
            search_snippet = None
            if TRAVILY_API_KEY and is_fact_query(user_text):
                try:
                    search_snippet = await core_search.tavily_search(user_text)
                except Exception as ex:
                    logging.debug(f"Tavily augmentation failed: {ex}")

            logging.debug("Starting LLM streaming")
            
            # Add user message to chat history (trimmed)
            append_to_history(session_id, "user", user_text)
            
            # Build augmented prompt if we have search context
            augmented_text = user_text
            if search_snippet:
                augmented_text = (
                    "Using the following recent web search context, answer the user's query accurately. "
                    "If the context seems unrelated, rely on general knowledge.\n\n"
                    f"[Web Search Context]\n{search_snippet}\n\n[User Question]\n{user_text}"
                )

            # Initialize Gemini model
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            # Send start message to client
            start_message = {
                "type": "llm_start",
                "transcript": user_text
            }
            await websocket.send_text(json.dumps(start_message))
            logging.debug("Sent LLM start message to client")
            
            # Stream response using synchronous generator in a background thread
            loop = asyncio.get_running_loop()
            full_response_ref = {"text": ""}

            # Murf WebSocket: open once per LLM response, reuse static context_id
            async def murf_streamer(text_stream_queue: asyncio.Queue):
                """Handle real-time TTS synthesis via Murf WebSocket API.
                
                Manages concurrent text-to-speech conversion while LLM generates response:
                1. Establishes WebSocket connection with voice configuration
                2. Streams text chunks as they arrive from LLM
                3. Receives and forwards audio chunks to client
                4. Assembles final WAV file from all chunks
                5. Handles connection errors and cleanup
                
                Args:
                    text_stream_queue: Queue receiving text chunks from LLM generator
                """
                if not MURF_API_KEY:
                    logging.error("MURF_API_KEY not set, skipping TTS stream")
                    return
                uri = f"{MURF_WS_URL}?api-key={MURF_API_KEY}&sample_rate=44100&channel_type=MONO&format=WAV"
                try:
                    async with websockets.connect(uri) as murf_ws:
                        # Send voice_config (optional)
                        voice_config_msg = {
                            "voice_config": {
                                "voiceId": "en-US-amara",
                                "style": "Conversational",
                                "rate": 0,
                                "pitch": 0,
                                "variation": 1
                            },
                            "context_id": MURF_CONTEXT_ID
                        }
                        await murf_ws.send(json.dumps(voice_config_msg))

                        # Task: receiver prints base64 audio to console
                        async def receiver():
                            try:
                                async for msg in murf_ws:
                                    try:
                                        data = json.loads(msg)
                                    except Exception:
                                        continue
                                    # Quickstart schema: base64 in "audio", final flag in "final"
                                    if "audio" in data:
                                        audio_b64 = data.get("audio")
                                        if audio_b64:
                                            # store per-session chunk for possible later retrieval
                                            try:
                                                self.murf_audio_chunks[session_id].append(audio_b64)
                                            except Exception:
                                                pass
                                            # Forward base64 audio to client
                                            try:
                                                await websocket.send_text(json.dumps({
                                                    "type": "murf_audio_chunk",
                                                    "audio": audio_b64
                                                }))
                                            except Exception:
                                                pass
                                    if data.get("final"):
                                        logging.debug("Murf synthesis complete")
                                        # Attempt to assemble the collected chunks into a single WAV
                                        final_b64 = None
                                        try:
                                            chunks = self.murf_audio_chunks.get(session_id, [])
                                            pcm_parts = []
                                            sr = None
                                            nch = None
                                            sw = None
                                            for b64 in chunks:
                                                raw = base64.b64decode(b64)
                                                try:
                                                    with wave.open(io.BytesIO(raw), 'rb') as wf:
                                                        if sr is None:
                                                            sr = wf.getframerate()
                                                            nch = wf.getnchannels()
                                                            sw = wf.getsampwidth()
                                                        pcm = wf.readframes(wf.getnframes())
                                                        pcm_parts.append(pcm)
                                                except wave.Error:
                                                    # not a full wav, append raw
                                                    pcm_parts.append(raw)

                                            out_buf = io.BytesIO()
                                            with wave.open(out_buf, 'wb') as out_wf:
                                                out_wf.setnchannels(nch or 1)
                                                out_wf.setsampwidth(sw or 2)
                                                out_wf.setframerate(sr or 44100)
                                                for part in pcm_parts:
                                                    out_wf.writeframes(part)
                                            final_bytes = out_buf.getvalue()
                                            final_b64 = base64.b64encode(final_bytes).decode('ascii')
                                        except Exception as ex:
                                            logging.error(f"Error assembling final Murf WAV: {ex}")

                                        try:
                                            payload = {
                                                "type": "murf_audio_final",
                                                "context_id": MURF_CONTEXT_ID,
                                                "chunk_count": len(self.murf_audio_chunks.get(session_id, []))
                                            }
                                            if final_b64:
                                                payload["audio_b64"] = final_b64
                                            await websocket.send_text(json.dumps(payload))
                                        except Exception:
                                            pass
                                        # Clean up stored chunks for this session to free memory
                                        try:
                                            if session_id in self.murf_audio_chunks:
                                                del self.murf_audio_chunks[session_id]
                                        except Exception:
                                            pass
                                        break
                            except Exception as ex:
                                logging.error(f"Murf receiver error: {ex}")

                        recv_task = asyncio.create_task(receiver())

                        # Send text chunks from queue
                        chunk_id = 0
                        while True:
                            chunk = await text_stream_queue.get()
                            if chunk is None:
                                break
                            await murf_ws.send(json.dumps({
                                "text": chunk,
                                "context_id": MURF_CONTEXT_ID
                            }))
                            chunk_id += 1

                        # Signal final
                        await murf_ws.send(json.dumps({
                            "text": "",
                            "end": True,
                            "context_id": MURF_CONTEXT_ID
                        }))

                        # Wait briefly for trailing audio
                        try:
                            await asyncio.wait_for(recv_task, timeout=2.0)
                        except asyncio.TimeoutError:
                            recv_task.cancel()
                except Exception as ex:
                    logging.error(f"Murf websocket error: {ex}")

            # Queue to bridge LLM text chunks to Murf
            text_queue: asyncio.Queue[str | None] = asyncio.Queue()
            murf_task = asyncio.create_task(murf_streamer(text_queue))

            def stream_sync():
                """Synchronous LLM streaming function for thread execution.
                
                Handles Gemini API streaming in a separate thread to avoid blocking
                the async event loop. Forwards text chunks to both WebSocket client
                and Murf TTS queue for concurrent processing.
                
                Error handling includes graceful fallback and proper stream resolution.
                """
                try:
                    stream = model.generate_content(
                        augmented_text,
                        stream=True,
                        generation_config=genai.types.GenerationConfig(
                            temperature=0.7,
                            top_p=0.8,
                            top_k=40,
                            max_output_tokens=2048,
                        )
                    )
                    for chunk in stream:
                        try:
                            text_chunk = getattr(chunk, "text", None) or ""
                        except Exception:
                            text_chunk = ""
                        if text_chunk:
                            full_response_ref["text"] += text_chunk
                            msg = json.dumps({
                                "type": "llm_chunk",
                                "text": text_chunk,
                                "is_complete": False
                            })
                            loop.call_soon_threadsafe(asyncio.create_task, websocket.send_text(msg))
                            # Also forward chunk to Murf
                            loop.call_soon_threadsafe(asyncio.create_task, text_queue.put(text_chunk))
                            # chunk logging suppressed for speed
                    # Ensure the stream is fully resolved (no-ops for SDKs that buffer)
                    try:
                        stream.resolve()
                    except Exception:
                        pass
                    # Completion message
                    complete_msg = json.dumps({
                        "type": "llm_complete",
                        "full_response": full_response_ref["text"],
                        "is_complete": True
                    })
                    loop.call_soon_threadsafe(asyncio.create_task, websocket.send_text(complete_msg))
                    # Close Murf queue
                    loop.call_soon_threadsafe(asyncio.create_task, text_queue.put(None))
                    logging.debug("LLM streaming completed")
                except Exception as ex:
                    err_msg = json.dumps({
                        "type": "llm_error",
                        "error": str(ex)
                    })
                    loop.call_soon_threadsafe(asyncio.create_task, websocket.send_text(err_msg))

            # Run streaming in a worker thread and wait for it to finish
            await asyncio.to_thread(stream_sync)
            # Ensure Murf drain completes
            try:
                await asyncio.wait_for(murf_task, timeout=5.0)
            except asyncio.TimeoutError:
                murf_task.cancel()

            # Add AI response to chat history after finishing (trimmed)
            if full_response_ref["text"]:
                append_to_history(session_id, "model", full_response_ref["text"])
                # Detect mood and fetch playlist recommendations only when a concrete mood was found
                try:
                    # Use the user's original transcript to decide whether to recommend music
                    detected_mood = spotify.detect_mood_from_text(user_text)
                    # spotify.detect_mood_from_text returns 'mood' as a generic fallback; skip recommendations in that case
                    if detected_mood and detected_mood != 'mood':
                        logging.info(f"Detected mood '{detected_mood}' from user_text, attempting playlist search")
                        # Use the app_core.spotify helper which handles token fetch and search
                        playlists = await spotify.search_playlists(detected_mood, limit=3)
                        logging.info(f"Playlist lookup for mood '{detected_mood}' returned {len(playlists) if playlists else 0} results")
                        if playlists and len(playlists) > 0:
                            # Send playlist recommendations to client over websocket
                            try:
                                payload = {"type": "playlist_recommendations", "mood": detected_mood, "playlists": playlists}
                                await websocket.send_text(json.dumps(payload))
                                logging.info(f"Sent playlist_recommendations for mood '{detected_mood}' to session {session_id}")
                            except Exception as ex:
                                logging.error(f"Failed to send playlist_recommendations over websocket: {ex}")
                except Exception as ex:
                    logging.debug(f"Playlist recommendation failed: {ex}")
            
        except Exception as e:
            logging.error(f"LLM streaming error: {e}")
            
            # Send error message to client
            error_message = {
                "type": "llm_error",
                "error": str(e)
            }
            try:
                await websocket.send_text(json.dumps(error_message))
            except:
                pass

# Global audio streamer instance
audio_streamer = AudioStreamer()

@app.websocket("/ws/audio/{session_id}")
async def websocket_audio_endpoint(websocket: WebSocket, session_id: str):
    """Main WebSocket endpoint for real-time audio streaming and transcription.
    
    Provides persistent connection for:
    - Receiving raw PCM audio data from browser
    - Sending real-time transcription events
    - Streaming LLM responses and TTS audio
    - Managing session lifecycle and cleanup
    
    Protocol:
    - Client sends: Raw PCM audio bytes
    - Server sends: JSON messages (transcription, llm_chunk, murf_audio_chunk, etc.)
    
    Args:
        session_id: Unique session identifier for state management
        
    Connection Lifecycle:
    1. Accept WebSocket connection
    2. Initialize streaming session with AssemblyAI
    3. Process incoming audio data continuously
    4. Handle disconnection and cleanup gracefully
    """
    await websocket.accept()
    logging.info(f"WebSocket connection established for session: {session_id}")
    
    try:
        # Start streaming (no recording)
        session_id = await audio_streamer.start_streaming(session_id, websocket)
        await websocket.send_text(f"Streaming started: {session_id}")
        
        while True:
            # Receive audio data from client
            data = await websocket.receive_bytes()
            
            # Stream audio data to AssemblyAI (no recording)
            await audio_streamer.stream_audio_data(session_id, data)
            
            # No per-chunk server ack to reduce client console noise
            
    except WebSocketDisconnect:
        logging.info(f"WebSocket disconnected for session: {session_id}")
        # Stop streaming when client disconnects
        await audio_streamer.stop_streaming(session_id)
    except Exception as e:
        logging.error(f"WebSocket error for session {session_id}: {e}")
        # Clean up streaming on error
        await audio_streamer.stop_streaming(session_id)

@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

@app.get("/favicon.ico")
async def favicon():
    """Serve a tiny transparent PNG to satisfy favicon requests."""
    import base64
    transparent_png_base64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAOQWv3kAAAAASUVORK5CYII="
    )
    png_bytes = base64.b64decode(transparent_png_base64)
    return Response(content=png_bytes, media_type="image/png")

@app.get("/proxy-audio/")
async def proxy_audio(url: str, request: Request = None):
    """Proxy an audio URL. If `url` is a relative path (starts with '/'), resolve it
    against the current server base URL so internal endpoints like `/tts/fetch/{id}`
    can be proxied.
    """
    # Resolve relative URLs to absolute using request.base_url when available
    try:
        if not url:
            raise HTTPException(status_code=400, detail="Missing url parameter")

        if url.startswith("/") and request is not None:
            base = str(request.base_url).rstrip('/')
            full_url = base + url
        else:
            full_url = url

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(full_url)
            resp.raise_for_status()
            content_type = resp.headers.get('content-type', 'application/octet-stream')
            # Stream the response content back to the caller
            return StreamingResponse(resp.aiter_bytes(), media_type=content_type, headers={"Access-Control-Allow-Origin": "*"})
    except httpx.HTTPStatusError as e:
        logging.error(f"Audio proxy HTTP error for {full_url}: {e.response.status_code}")
        raise HTTPException(status_code=502, detail="Could not fetch audio (upstream HTTP error).")
    except httpx.RequestError as e:
        logging.error(f"Audio proxy failed: {getattr(e.request, 'url', 'unknown')} - {e}")
        raise HTTPException(status_code=502, detail="Could not fetch audio (request error).")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Audio proxy unexpected error: {e}")
        raise HTTPException(status_code=502, detail="Could not fetch audio.")


@app.post('/config/keys')
async def set_runtime_keys(payload: dict = Body(...)):
    """Configure API keys at runtime for secure, ephemeral operation.
    
    Security Model:
    - Keys stored only in process memory (never persisted to disk)
    - No secrets logged or exposed in responses
    - Validates required keys before accepting configuration
    - Reconfigures dependent SDK clients immediately
    
    Expected JSON payload:
    {
        "murf": "MURF_API_KEY",           // Required: TTS synthesis
        "assemblyai": "ASSEMBLYAI_KEY",   // Required: Speech transcription  
        "gemini": "GEMINI_API_KEY",       // Required: LLM responses
        "opencage": "OPENCAGE_KEY",       // Optional: Geocoding for weather
        "tavily": "TAVILY_API_KEY",       // Optional: Web search augmentation
        "spotify_client_id": "CLIENT_ID", // Optional: Music recommendations
        "spotify_client_secret": "SECRET" // Optional: Music recommendations
    }
    
    Returns:
        JSON with boolean flags indicating which keys were successfully set.
        
    Security Note:
        In production, prefer server-side configuration over client key submission.
    """
    try:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail='Invalid payload')

        murf = (payload.get('murf') or '').strip()
        assembly = (payload.get('assemblyai') or payload.get('assembly') or '').strip()
        gemini = (payload.get('gemini') or '').strip()
        opencage = (payload.get('opencage') or '').strip()
        tavily = (payload.get('tavily') or payload.get('travily') or '').strip()
        spotify_id = (payload.get('spotify_client_id') or payload.get('spotify_id') or '').strip()
        spotify_secret = (payload.get('spotify_client_secret') or payload.get('spotify_secret') or '').strip()

        if not murf or not assembly or not gemini:
            raise HTTPException(status_code=400, detail='Missing required keys')

        # Update required keys
        config.update_runtime_keys(murf, assembly, gemini)
        # Update optional keys directly on config module
        if opencage:
            config.OPENCAGE_API_KEY = opencage
        if tavily:
            config.TRAVILY_API_KEY = tavily
        if spotify_id:
            config.SPOTIFY_CLIENT_ID = spotify_id
        if spotify_secret:
            config.SPOTIFY_CLIENT_SECRET = spotify_secret

        global MURF_API_KEY, ASSEMBLYAI_API_KEY, GEMINI_API_KEY, OPENCAGE_API_KEY, TRAVILY_API_KEY, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
        MURF_API_KEY = config.MURF_API_KEY
        ASSEMBLYAI_API_KEY = config.ASSEMBLYAI_API_KEY
        GEMINI_API_KEY = config.GEMINI_API_KEY
        OPENCAGE_API_KEY = config.OPENCAGE_API_KEY
        TRAVILY_API_KEY = config.TRAVILY_API_KEY
        SPOTIFY_CLIENT_ID = config.SPOTIFY_CLIENT_ID
        SPOTIFY_CLIENT_SECRET = config.SPOTIFY_CLIENT_SECRET

        # Reconfigure dependent libraries where possible
        try:
            aai.settings.api_key = ASSEMBLYAI_API_KEY
        except Exception:
            pass
        try:
            genai.configure(api_key=GEMINI_API_KEY)
        except Exception:
            pass

        logging.info('Runtime API keys updated via /config/keys (values not logged).')
        logging.info('Key presence -> murf:%s assembly:%s gemini:%s opencage:%s tavily:%s spotify_id:%s spotify_secret:%s',
                     bool(MURF_API_KEY), bool(ASSEMBLYAI_API_KEY), bool(GEMINI_API_KEY), bool(OPENCAGE_API_KEY), bool(TRAVILY_API_KEY), bool(SPOTIFY_CLIENT_ID), bool(SPOTIFY_CLIENT_SECRET))
        return JSONResponse({'ok': True, 'keys': {
            'murf': bool(MURF_API_KEY),
            'assemblyai': bool(ASSEMBLYAI_API_KEY),
            'gemini': bool(GEMINI_API_KEY),
            'opencage': bool(OPENCAGE_API_KEY),
            'tavily': bool(TRAVILY_API_KEY),
            'spotify_client_id': bool(SPOTIFY_CLIENT_ID),
            'spotify_client_secret': bool(SPOTIFY_CLIENT_SECRET)
        }})
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f'Error updating runtime keys: {e}')
        raise HTTPException(status_code=500, detail='Failed to update keys')


@app.get("/agent/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    """Return in-memory chat history for a session."""
    # If session is unknown, return an empty history instead of 404 to simplify client logic
    if session_id not in chat_history:
        return {"history": []}
    return {"history": chat_history[session_id]}


@app.get("/health/spotify")
async def spotify_health():
    """Lightweight health probe for Spotify credentials & token fetch.

    Returns JSON with:
    - credentials: bool (client id & secret present in runtime config)
    - token_obtained: bool (was a token successfully fetched right now)
    - expires_in: seconds remaining on cached token (if any)
    Useful on Render to diagnose missing env / runtime key issues.
    """
    have_creds = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)
    remaining = 0
    token_ok = False
    global _spotify_token, _spotify_token_expires_at
    if have_creds:
        try:
            t = await get_spotify_token()
            token_ok = bool(t)
            if token_ok and _spotify_token_expires_at:
                remaining = int(_spotify_token_expires_at - _time.time())
        except Exception as e:
            logging.error("Spotify health check token fetch failed: %s", e)
    return {"credentials": have_creds, "token_obtained": token_ok, "expires_in": remaining}


@app.get("/recommend/{mood}")
async def recommend_playlists(mood: str):
    """Return 2-3 Spotify playlists that match the given mood.

    Uses the Client Credentials flow; token is cached until expiry.
    """
    token = await get_spotify_token()
    if not token:
        raise HTTPException(status_code=500, detail="Spotify credentials not configured or token request failed.")

    q = _normalize_mood(mood)
    search_url = "https://api.spotify.com/v1/search"
    params = {"q": q, "type": "playlist", "limit": 3}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(search_url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("playlists", {}).get("items", [])
            results = []
            for p in items:
                results.append({
                    "name": p.get("name"),
                    "url": p.get("external_urls", {}).get("spotify"),
                    "id": p.get("id")
                })
            return {"mood": mood, "query": q, "playlists": results}
    except httpx.HTTPStatusError as e:
        logging.error(f"Spotify API error: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=502, detail="Spotify API returned an error.")
    except Exception as e:
        logging.error(f"Spotify request failed: {e}")
        raise HTTPException(status_code=502, detail="Failed to query Spotify.")


@app.get("/spotify/playlist/{playlist_id}")
async def get_playlist_tracks(playlist_id: str):
    """Return tracks for a playlist (name, artists, preview_url, duration_ms, track_url, image).

    Uses cached Spotify token.
    """
    token = await get_spotify_token()
    if not token:
        raise HTTPException(status_code=500, detail="Spotify credentials not configured or token request failed.")

    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
    params = {"fields": "items(track(name,artists(name),preview_url,external_urls,duration_ms,album(images)))", "limit": 100}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params, headers=headers)
            text = resp.text
            try:
                resp.raise_for_status()
            except Exception:
                logging.error(f"Spotify playlist tracks status error (status={resp.status_code}): {text}")
                try:
                    logging.error("Spotify error body: %s", resp.json())
                except Exception:
                    pass
                raise HTTPException(status_code=502, detail="Spotify API error fetching playlist tracks")

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
                    "image": image
                })
            return {"playlist_id": playlist_id, "tracks": tracks}


        @app.post("/weather")
        async def get_weather(payload: dict = Body(...)):
            """Return current weather for a location.

            Payload options:
            - { "location": "New York, NY" }
            - { "lat": 40.7, "lon": -74.0 }
            """
            lat = payload.get("lat")
            lon = payload.get("lon")
            location = payload.get("location")

            # If location string provided, geocode via OpenCage
            if (not lat or not lon) and location:
                if not OPENCAGE_API_KEY:
                    raise HTTPException(status_code=500, detail="Geocoding not configured.")
                try:
                    geocode_url = "https://api.opencagedata.com/geocode/v1/json"
                    async with httpx.AsyncClient(timeout=10) as client:
                        resp = await client.get(geocode_url, params={"q": location, "key": OPENCAGE_API_KEY, "limit": 1})
                        resp.raise_for_status()
                        data = resp.json()
                        results = data.get('results') or []
                        if not results:
                            raise HTTPException(status_code=404, detail="Location not found")
                        geometry = results[0].get('geometry') or {}
                        lat = geometry.get('lat')
                        lon = geometry.get('lng')
                except HTTPException:
                    raise
                except Exception as e:
                    logging.error(f"OpenCage geocode error: {e}")
                    raise HTTPException(status_code=502, detail="Geocoding service failed")

            if not lat or not lon:
                raise HTTPException(status_code=400, detail="Missing location coordinates or location string")

            # Query Open-Meteo for current weather
            try:
                weather_url = "https://api.open-meteo.com/v1/forecast"
                params = {"latitude": lat, "longitude": lon, "current_weather": True, "timezone": "auto"}
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(weather_url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    current = data.get('current_weather') or {}
                    return {"location": {"lat": lat, "lon": lon}, "current": current}
            except Exception as e:
                logging.error(f"Weather lookup failed: {e}")
                raise HTTPException(status_code=502, detail="Weather lookup failed")


        @app.get('/search')
        async def web_search(q: str):
            """Perform a simple web search. If TRAVILY_API_KEY is set, call Travily, otherwise use DuckDuckGo instant answer as a fallback."""
            if not q or q.strip() == '':
                raise HTTPException(status_code=400, detail='Missing query')
            q = q.strip()
            # Tavily-only search. If not configured, inform the caller.
            if not (TRAVILY_API_KEY and TavilyClient is not None):
                raise HTTPException(status_code=503, detail='Search service is not configured (missing TRAVILY_API_KEY)')

            try:
                client = TavilyClient(api_key=TRAVILY_API_KEY)
                def call_search():
                    return client.search(q)
                res = await asyncio.to_thread(call_search)

                if not res:
                    return {"query": q, "content": "No results returned by Tavily"}

                # Normalize to top content string when possible
                if isinstance(res, list) and len(res) > 0:
                    top = res[0]
                    if isinstance(top, dict):
                        content = top.get('content') or top.get('title') or top.get('raw_content')
                        if content:
                            return {"query": q, "content": content}
                    return {"query": q, "content": str(top)}

                if isinstance(res, dict):
                    for key in ("content", "answer", "summary", "results", "data", "items"):
                        val = res.get(key)
                        if val:
                            if isinstance(val, list) and len(val) > 0:
                                first = val[0]
                                if isinstance(first, dict):
                                    return {"query": q, "content": str(first.get('content') or first.get('title') or first.get('raw_content') or first)}
                                return {"query": q, "content": str(first)}
                            return {"query": q, "content": str(val)}
                    import json as _json
                    return {"query": q, "content": _json.dumps(res)}

                return {"query": q, "content": str(res)}
            except Exception as e:
                logging.error(f'Tavily search failed: {e}')
                raise HTTPException(status_code=502, detail='Search failed: Tavily error')
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Failed to fetch playlist tracks: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch playlist tracks")


@app.post("/session/switch")
async def switch_session(payload: dict):
    """Switch to a new session; optionally clear an old session's memory.

    Expected JSON: { "old_session_id": "session_xxx" }
    Returns: { "session_id": "new_session_id" }
    """
    old = payload.get("old_session_id") if isinstance(payload, dict) else None
    if old:
        # clear chat history and stop streaming for old session
        if old in chat_history:
            del chat_history[old]
        try:
            await audio_streamer.stop_streaming(old)
        except Exception:
            pass

    new_id = f"session_{int(time.time() * 1000)}_{secrets.token_hex(4)}"
    chat_history[new_id] = []
    # Also clear all runtime API keys so each session requires fresh entry
    try:
        config.MURF_API_KEY = None
        config.ASSEMBLYAI_API_KEY = None
        config.GEMINI_API_KEY = None
        config.OPENCAGE_API_KEY = None
        config.TRAVILY_API_KEY = None
        config.SPOTIFY_CLIENT_ID = None
        config.SPOTIFY_CLIENT_SECRET = None
        global MURF_API_KEY, ASSEMBLYAI_API_KEY, GEMINI_API_KEY, OPENCAGE_API_KEY, TRAVILY_API_KEY, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
        MURF_API_KEY = None
        ASSEMBLYAI_API_KEY = None
        GEMINI_API_KEY = None
        OPENCAGE_API_KEY = None
        TRAVILY_API_KEY = None
        SPOTIFY_CLIENT_ID = None
        SPOTIFY_CLIENT_SECRET = None
        # Best-effort: reset external SDK configs (some SDKs may not support un-setting keys, so ignore errors)
        try:
            aai.settings.api_key = None
        except Exception:
            pass
        # Gemini SDK has no direct de-configure; leaving as-is without key won't work until reconfigured.
        logging.info('Cleared all runtime API keys on session switch.')
    except Exception as e:
        logging.warning(f'Failed clearing runtime keys on session switch: {e}')
    return {"session_id": new_id}

# --- TTS Endpoint ---
@app.post("/tts")
async def generate_tts(payload: dict = Body(...)):
    """Generate TTS from posted JSON {"text": "..."} and return {"audio_url": "..."}.

    Older code expected a raw `text` param; the client now POSTs JSON. This handler
    reads the JSON body and forwards the text to Murf.
    """
    text = (payload or {}).get("text") if isinstance(payload, dict) else None
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' in request body.")

    if not MURF_API_KEY:
        logging.error("TTS endpoint called but MURF_API_KEY missing.")
        raise HTTPException(status_code=500, detail="TTS service not configured.")

    headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
    req_payload = {"text": text, "voiceId": "en-US-natalie"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=req_payload)
            resp.raise_for_status()
            data = resp.json()
            audio_url = data.get("audioFile")
            if not audio_url:
                logging.error("Murf API succeeded but no audioFile.")
                raise HTTPException(status_code=502, detail="TTS API error: no audio.")

            # Store audio_url server-side and return an opaque tts_id so raw URLs are never exposed
            tts_id = secrets.token_urlsafe(8)
            tts_cache[tts_id] = audio_url
            return {"tts_id": tts_id, "tts_available": True}
    except httpx.HTTPStatusError as e:
        logging.error(f"Murf TTS error: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=502, detail="Failed to communicate with TTS.")
    except Exception as e:
        logging.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail="TTS internal error.")


@app.get('/tts/fetch/{tts_id}')
async def fetch_tts_audio(tts_id: str):
    """Proxy endpoint to fetch generated TTS audio by opaque tts_id.

    This keeps upstream audio URLs private. The server fetches the audio
    from the TTS provider and streams bytes to the caller.
    """
    audio_url = tts_cache.get(tts_id)
    if not audio_url:
        raise HTTPException(status_code=404, detail='TTS id not found')

    try:
        # Stream the audio content from the provider and proxy it to the client
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(audio_url, timeout=60)
            resp.raise_for_status()
            content_type = resp.headers.get('content-type', 'application/octet-stream')
            return Response(content=resp.content, media_type=content_type)
    except Exception as e:
        logging.error(f"Failed to fetch tts audio for id {tts_id}: {e}")
        raise HTTPException(status_code=502, detail='Failed to fetch TTS audio')

async def generate_fallback_audio(msg = "I'm sorry, I'm having trouble connecting right now. Please try again later."):
    if not MURF_API_KEY: return None
    headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
    payload = {"text": msg, "voiceId": "en-US-marcus"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            murf_resp = await client.post(
                "https://api.murf.ai/v1/speech/generate", headers=headers, json=payload)
            murf_resp.raise_for_status()
            return murf_resp.json().get("audioFile")
    except Exception as e:
        logging.error(f"Fallback audio error: {e}")
    return None

# --- Main Chat LLM endpoint ---
@app.post("/agent/chat/{session_id}")
async def agent_chat(session_id: str, file: UploadFile = File(...)):
    try:
        if not ASSEMBLYAI_API_KEY: raise ValueError("AssemblyAI API key not set.")
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(file.file)
        if transcript.error: raise RuntimeError(f"Transcription Error: {transcript.error}")
        user_text = (transcript.text or "").strip()
        if not user_text:
            return JSONResponse(status_code=400, content={"error": "No speech detected. Please speak clearly."})
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        fallback_audio_url = await generate_fallback_audio()
        if fallback_audio_url:
            # stash fallback audio and return tts_id so the raw URL isn't exposed
            tts_id = secrets.token_urlsafe(8)
            tts_cache[tts_id] = fallback_audio_url
            return JSONResponse(status_code=503, content={"error": "Could not process your audio.", "tts_id": tts_id, "tts_available": True})
        return JSONResponse(status_code=503, content={"error": "Speech-to-text unavailable."})

    append_to_history(session_id, "user", user_text)

    try:
        if not GEMINI_API_KEY: raise ValueError("Gemini API key not set.")
        model = genai.GenerativeModel('gemini-1.5-flash')
        conversation = model.start_chat(history=chat_history[session_id][:-1])
        llm_response = conversation.send_message(user_text)
        llm_text = (llm_response.text or "").strip()
        if not llm_text: raise RuntimeError("LLM returned empty response.")
    except Exception as e:
        logging.error(f"LLM error: {e}")
        chat_history[session_id].pop()
        fallback_audio_url = await generate_fallback_audio("The AI model is currently unavailable.")
        if fallback_audio_url:
            tts_id = secrets.token_urlsafe(8)
            tts_cache[tts_id] = fallback_audio_url
            return JSONResponse(status_code=503, content={"error": "AI Model unavailable.", "tts_id": tts_id, "tts_available": True, "transcription": user_text})
        return JSONResponse(status_code=503, content={"error": "AI Model unavailable."})

    append_to_history(session_id, "model", llm_text)

    # automatically fetch playlists for the model reply only when a concrete mood is detected
    playlists_for_response = []
    try:
        # Use the user's original transcript to decide whether to recommend music
        detected_mood = spotify.detect_mood_from_text(user_text)
        if detected_mood and detected_mood != 'mood':
            playlists_for_response = await spotify.search_playlists(detected_mood, limit=3)
    except Exception:
        playlists_for_response = []

    try:
        if not MURF_API_KEY: raise ValueError("Murf API key not set.")
        murf_text = llm_text[:2900]
        headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
        payload = {"text": murf_text, "voiceId": "en-US-marcus"}
        async with httpx.AsyncClient(timeout=90) as client:
            murf_resp = await client.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=payload)
            murf_resp.raise_for_status()
            # Do not log or expose the raw audio URL in server logs or responses
            murf_json = murf_resp.json()
            audio_url = murf_json.get("audioFile")
            if not audio_url:
                raise RuntimeError("Murf API no audio URL.")
    except Exception as e:
        logging.error(f"TTS error (audio generation failed)")
        return JSONResponse(
            status_code=503,
            content={
                "error": "Voice generation unavailable.",
                "transcription": user_text,
                "llm_response": llm_text
            }
        )
    resp_payload = {
        "tts_available": True,
        "transcription": user_text,
    "llm_response": llm_text,
    }
    if playlists_for_response:
        resp_payload["playlists"] = playlists_for_response
    return resp_payload
# @app.get("/agent/chat/history/{session_id}")
# async def get_chat_history(session_id: str):
#     if session_id not in chat_history:
#         return JSONResponse(status_code=404, content={"error": "Session not found."})
#     return {"history": chat_history[session_id]}    