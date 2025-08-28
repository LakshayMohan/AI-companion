# app.py
"""FastAPI backend for a minimal voice agent.

- Speech-to-Text (STT): AssemblyAI
- Large Language Model (LLM): Google Gemini
- Text-to-Speech (TTS): Murf

This service accepts audio from the client, transcribes it, generates a
response using the LLM, and returns both the text and a synthesized audio URL
for playback. Static assets for the demo UI are served from the `static/`
directory.

Environment variables are loaded from `.env` at startup.
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import re # NEW: simple weather-intent detection
import os
import httpx
import assemblyai as aai
import google.generativeai as genai
from collections import defaultdict
import logging # NEW: Import logging library

# --- NEW: Basic Logging Configuration ---
# In a production app, you would configure this more extensively (e.g., log to files).
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()
MURF_API_KEY = os.getenv("MURF_API_KEY")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENCAGE_API_KEY = os.getenv("OPENCAGE_API_KEY") # NEW: OpenCage key for geocoding

# Set API Keys
if ASSEMBLYAI_API_KEY:
    aai.settings.api_key = ASSEMBLYAI_API_KEY
else:
    logging.warning("ASSEMBLYAI_API_KEY is not set.")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    logging.warning("GEMINI_API_KEY is not set.")

if not MURF_API_KEY:
    logging.warning("MURF_API_KEY is not set.")

# Optional: warn if OpenCage is not set (weather will be skipped)
if not OPENCAGE_API_KEY:
    logging.warning("OPENCAGE_API_KEY is not set; weather queries will fall back to LLM.")


app = FastAPI()

# In-Memory Chat History Store (for demo purposes)
chat_history = defaultdict(list)

# Mount static directory for frontend files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_index():
    """Serve the simple demo UI from `static/index.html`."""
    return FileResponse("static/index.html")

class TTSRequest(BaseModel):
    """Request body for the `/tts` endpoint.

    - `text`: The text to synthesize
    - `voiceId`: Optional Murf voice identifier (defaults to a friendly US voice)
    """
    text: str
    voiceId: str = "en-US-natalie"


# -------------------------
# Weather integration (NEW)
# -------------------------
async def get_coordinates(city: str, countrycode: str | None = None) -> tuple[float | None, float | None]:
    """Forward-geocode city name to (lat, lon) using OpenCage.

    When `countrycode` is provided (ISO 3166-1 alpha-2), the query is disambiguated.
    Returns (None, None) on failure.
    """
    if not city or not OPENCAGE_API_KEY:
        return (None, None)

    params = {"q": city, "key": OPENCAGE_API_KEY, "limit": 1}
    if countrycode:
        params["countrycode"] = countrycode

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.opencagedata.com/geocode/v1/json", params=params)
            r.raise_for_status()
            data = r.json() or {}
            results = data.get("results") or []
            if not results:
                return (None, None)
            first = results[0] or {}
            geometry = first.get("geometry") or {}
            lat = geometry.get("lat")
            lon = geometry.get("lng")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                return (float(lat), float(lon))
            return (None, None)
    except httpx.HTTPStatusError as e:
        logging.error(f"OpenCage status error {e.response.status_code}: {e.response.text}")
        return (None, None)
    except Exception as e:
        logging.error(f"OpenCage request failed: {e}")
        return (None, None)


async def get_weather(lat: float, lon: float) -> dict | None:
    """Fetch a simple daily forecast summary from Open-Meteo for given coordinates.

    Returns a dict with arrays for date, temp_max, temp_min, and precipitation, or None on error.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            r.raise_for_status()
            j = r.json() or {}
            daily = j.get("daily") or {}
            return {
                "date": daily.get("time") or [],
                "temp_max": daily.get("temperature_2m_max") or [],
                "temp_min": daily.get("temperature_2m_min") or [],
                "rain": daily.get("precipitation_sum") or [],
            }
    except httpx.HTTPStatusError as e:
        logging.error(f"Open-Meteo status error {e.response.status_code}: {e.response.text}")
        return None
    except Exception as e:
        logging.error(f"Open-Meteo request failed: {e}")
        return None


def format_weather_summary(city: str, fc: dict) -> str:
    """Make a short, TTS-friendly weather sentence for today."""
    date = (fc.get("date") or [None])[0]
    tmax = (fc.get("temp_max") or [None])[0]
    tmin = (fc.get("temp_min") or [None])[0]
    rain = (fc.get("rain") or [None])[0]

    def fmt(v):
        return "unknown" if v is None else str(v)

    when = "today" if date else "today"
    return f"Weather for {city} {when}: high {fmt(tmax)}°C, low {fmt(tmin)}°C, precipitation {fmt(rain)} mm."

# --- REFINED: Text-to-Speech Endpoint with Better Error Handling ---
@app.post("/tts")
async def generate_tts(request: TTSRequest):
    """Generate speech audio via Murf and return a signed audio URL.

    This endpoint is useful for client fallbacks (e.g., non-streaming TTS) or
    for synthesizing arbitrary text from the UI.
    """
    if not MURF_API_KEY:
        logging.error("TTS endpoint called but MURF_API_KEY is missing.")
        raise HTTPException(status_code=500, detail="TTS service is not configured on the server.")

    headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
    payload = {"text": request.text, "voiceId": request.voiceId}
    
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            audio_url = data.get("audioFile")
            if not audio_url:
                logging.error("Murf API succeeded but returned no audioFile.")
                raise HTTPException(status_code=502, detail="TTS service failed to generate audio.")
            return {"audio_url": audio_url}
    except httpx.HTTPStatusError as e:
        logging.error(f"Murf API request failed: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=502, detail="Failed to communicate with the TTS service.")
    except Exception as e:
        logging.error(f"An unexpected error occurred in TTS generation: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred.")


# --- NEW: Helper function for generating fallback audio ---
async def generate_fallback_audio(error_message: str = "I'm sorry, I'm having trouble connecting right now. Please try again later."):
    """Generates a generic audio error message using Murf."""
    if not MURF_API_KEY:
        return None # Cannot generate audio without the key
    
    headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
    payload = {"text": error_message, "voiceId": "en-US-marcus"} # Using a standard voice for errors
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            murf_resp = await client.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=payload)
            murf_resp.raise_for_status()
            return murf_resp.json().get("audioFile")
    except Exception as e:
        logging.error(f"Failed to generate fallback audio: {e}")
        return None


# --- REBUILT: Conversational Agent with Robust, Step-by-Step Error Handling ---
@app.post("/agent/chat/{session_id}")
async def agent_chat(session_id: str, file: UploadFile = File(...)):
    """Perform a full voice turn: STT → LLM → TTS.

    Steps:
    1) Transcribe the uploaded audio with AssemblyAI
    2) Generate a response with Gemini using recent `chat_history`
    3) Synthesize the response with Murf TTS and return an audio URL

    The endpoint responds with a JSON object containing the transcription,
    the model's text reply, and a temporary URL to the generated audio.
    """
    # 1. Transcribe audio with AssemblyAI
    try:
        if not ASSEMBLYAI_API_KEY:
            raise ValueError("AssemblyAI API key is not configured.")
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(file.file)

        if transcript.error:
            raise RuntimeError(f"Transcription Error: {transcript.error}")

        user_text = (transcript.text or "").strip()
        if not user_text:
            return JSONResponse(status_code=400, content={"error": "No speech was detected in the audio. Please speak clearly and try again."})
            
    except Exception as e:
        logging.error(f"Error during transcription: {e}")
        fallback_audio_url = await generate_fallback_audio()
        if fallback_audio_url:
            return JSONResponse(status_code=503, content={"error": "Could not process your audio.", "audio_url": fallback_audio_url})
        return JSONResponse(status_code=503, content={"error": "The speech-to-text service is unavailable."})

    # Append user's message to history
    chat_history[session_id].append({"role": "user", "parts": [user_text]})

    # 1.5) Weather intent short-circuit (NEW): if the user explicitly asks for weather,
    # handle it with OpenCage + Open-Meteo and return without invoking the LLM.
    try:
        # Match e.g. "weather in Paris", "what's the weather in Delhi, IN"
        m = re.search(r"\bweather(?:\s+in)?\s+([A-Za-z\s\-\.',]+)$", user_text, re.IGNORECASE)
        if m and OPENCAGE_API_KEY:
            raw_city = (m.group(1) or "").strip(" .,!?:;")
            cc_match = re.search(r",\s*([a-z]{2})$", raw_city, re.IGNORECASE)
            countrycode = cc_match.group(1).lower() if cc_match else None
            city = raw_city if not cc_match else raw_city[:cc_match.start()].strip()
            lat, lon = await get_coordinates(city, countrycode)
            if lat is not None and lon is not None:
                fc = await get_weather(lat, lon)
                if fc:
                    summary = format_weather_summary(city, fc)
                    # Store assistant reply in history
                    chat_history[session_id].append({"role": "model", "parts": [summary]})

                    # Synthesize and return immediately
                    try:
                        if not MURF_API_KEY:
                            raise ValueError("Murf API key is not configured.")
                        speak_text = summary[:2900]
                        headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
                        payload = {"text": speak_text, "voiceId": "en-US-marcus"}
                        async with httpx.AsyncClient(timeout=60) as client:
                            murf_resp = await client.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=payload)
                            murf_resp.raise_for_status()
                            audio_url = murf_resp.json().get("audioFile")
                            if not audio_url:
                                raise RuntimeError("Murf API returned no audio URL.")
                    except Exception as e:
                        logging.error(f"TTS error (weather): {e}")
                        return {
                            "audio_url": None,
                            "transcription": user_text,
                            "llm_response": summary
                        }

                    return {
                        "audio_url": audio_url,
                        "transcription": user_text,
                        "llm_response": summary
                    }
    except Exception as e:
        # Non-fatal; fall back to LLM path
        logging.debug(f"Weather intent handling skipped: {e}")

    # 2. Generate response with Gemini LLM
    try:
        if not GEMINI_API_KEY:
            raise ValueError("Gemini API key is not configured.")
            
        model = genai.GenerativeModel('gemini-1.5-flash')
        # Pass prior messages, excluding the last user entry we will send separately
        conversation = model.start_chat(history=chat_history[session_id][:-1])
        llm_response = conversation.send_message(user_text)
        
        llm_text = (llm_response.text or "").strip()
        if not llm_text:
            raise RuntimeError("LLM returned an empty response.")
            
    except Exception as e:
        logging.error(f"Error during LLM generation: {e}")
        chat_history[session_id].pop() # Remove the user message from history if LLM fails
        fallback_audio_url = await generate_fallback_audio()
        if fallback_audio_url:
            return JSONResponse(status_code=503, content={"error": "The AI model is currently unavailable.", "audio_url": fallback_audio_url, "transcription": user_text})
        return JSONResponse(status_code=503, content={"error": "The AI model service is unavailable."})

    # Append LLM's response to history
    chat_history[session_id].append({"role": "model", "parts": [llm_text]})

    # 3. Generate TTS with Murf from LLM response
    try:
        if not MURF_API_KEY:
            raise ValueError("Murf API key is not configured.")
            
        # Murf API has practical limits; keep a safety margin on very long texts
        murf_text = llm_text[:2900]
        headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
        payload = {"text": murf_text, "voiceId": "en-US-marcus"}
        
        async with httpx.AsyncClient(timeout=90) as client:
            murf_resp = await client.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=payload)
        murf_resp.raise_for_status()
        audio_url = murf_resp.json().get("audioFile")

        if not audio_url:
            raise RuntimeError("Murf API returned no audio URL.")

    except Exception as e:
        logging.error(f"Error during TTS generation for LLM response: {e}")
        # We can't generate fallback audio here because the TTS service itself is failing.
        # We return the text response so the user can at least read it.
        return JSONResponse(
            status_code=503,
            content={
                "error": "The voice generation service is unavailable.",
                "transcription": user_text,
                "llm_response": llm_text
            }
        )

    # 4. Return final successful result
    return {
        "audio_url": audio_url,
        "transcription": user_text,
        "llm_response": llm_text
    }

