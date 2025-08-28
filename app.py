# app.py

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
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


app = FastAPI()

# In-Memory Chat History Store (for demo purposes)
chat_history = defaultdict(list)
session_config_overrides = defaultdict(dict)

# Mount static directory for frontend files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

class TTSRequest(BaseModel):
    text: str
    voiceId: str = "en-US-natalie"

class SessionConfig(BaseModel):
    MURF_API_KEY: str | None = None
    ASSEMBLYAI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    SPOTIFY_CLIENT_ID: str | None = None
    SPOTIFY_CLIENT_SECRET: str | None = None
    OPENCAGE_API_KEY: str | None = None

# --- REFINED: Text-to-Speech Endpoint with Better Error Handling ---
@app.post("/tts")
async def generate_tts(request: TTSRequest):
    # Allow per-session override if voice generation is triggered with a session header
    request_session_id = None
    # In this simple example, TTS is not session-specific from the client, so we use global or last-set env
    effective_murf_key = MURF_API_KEY
    if not effective_murf_key:
        logging.error("TTS endpoint called but MURF_API_KEY is missing.")
        raise HTTPException(status_code=500, detail="TTS service is not configured on the server.")

    headers = {"api-key": effective_murf_key, "Content-Type": "application/json"}
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
    # Resolve per-session effective keys (fallback to env)
    overrides = session_config_overrides.get(session_id, {})
    effective_assembly_key = overrides.get("ASSEMBLYAI_API_KEY") or ASSEMBLYAI_API_KEY
    effective_gemini_key = overrides.get("GEMINI_API_KEY") or GEMINI_API_KEY
    effective_murf_key = overrides.get("MURF_API_KEY") or MURF_API_KEY

    # 1. Transcribe audio with AssemblyAI
    try:
        if not effective_assembly_key:
            raise ValueError("AssemblyAI API key is not configured.")
        aai.settings.api_key = effective_assembly_key
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

    # 2. Generate response with Gemini LLM
    try:
        if not effective_gemini_key:
            raise ValueError("Gemini API key is not configured.")
        genai.configure(api_key=effective_gemini_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        conversation = model.start_chat(history=chat_history[session_id][:-1]) # Pass history without the latest user message
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
        if not effective_murf_key:
            raise ValueError("Murf API key is not configured.")
            
        murf_text = llm_text[:2900]
        headers = {"api-key": effective_murf_key, "Content-Type": "application/json"}
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


# --- NEW: Session configuration endpoint ---
@app.post("/config/{session_id}")
async def set_session_config(session_id: str, cfg: SessionConfig):
    # Store only provided keys; ignore None/empty
    clean = {}
    for k, v in cfg.dict().items():
        if v:
            clean[k] = v
    session_config_overrides[session_id].update(clean)
    # Log minimal info for debugging without exposing secrets
    redacted = {k: ("***" if v else None) for k, v in clean.items()}
    logging.info(f"Updated session config for {session_id}: {redacted}")
    return {"ok": True}

