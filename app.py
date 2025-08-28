# app.py

from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
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
import asyncio
import json

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

# Mount static directory for frontend files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

class TTSRequest(BaseModel):
    text: str
    voiceId: str = "en-US-natalie"


# --- Optional: Simple session management helpers for the frontend ---
class SessionSwitchRequest(BaseModel):
    old_session_id: str | None = None


@app.get("/agent/chat/history/{session_id}")
async def get_history(session_id: str):
    return {"history": chat_history.get(session_id, [])}


@app.post("/session/switch")
async def switch_session(payload: SessionSwitchRequest):
    new_session_id = f"session_{int(asyncio.get_event_loop().time()*1000)}"
    return {"session_id": new_session_id}

# --- REFINED: Text-to-Speech Endpoint with Better Error Handling ---
@app.post("/tts")
async def generate_tts(request: TTSRequest):
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

    # 2. Generate response with Gemini LLM
    try:
        if not GEMINI_API_KEY:
            raise ValueError("Gemini API key is not configured.")
            
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
        if not MURF_API_KEY:
            raise ValueError("Murf API key is not configured.")
            
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


# --- NEW: Basic WebSocket endpoint for streaming audio ---
@app.websocket("/ws/audio/{session_id}")
async def websocket_audio(websocket: WebSocket, session_id: str):
    """
    Minimal WebSocket handler that accepts binary audio frames from the client.
    For demo purposes, it simulates LLM streaming messages back to the client.
    """
    await websocket.accept()
    logging.info(f"WebSocket connected for session {session_id}")

    # Reader task: consume incoming frames to keep the connection alive
    async def reader():
        try:
            while True:
                message = await websocket.receive()
                if "bytes" in message and message["bytes"] is not None:
                    # Discard or buffer PCM frames; here we just count them
                    continue
                if "text" in message and message["text"] is not None:
                    # Basic ping/pong support
                    try:
                        payload = json.loads(message["text"]) if message["text"].strip().startswith("{") else None
                        if payload and payload.get("type") == "ping":
                            await websocket.send_text(json.dumps({"type": "pong"}))
                    except Exception:
                        pass
        except WebSocketDisconnect:
            logging.info(f"WebSocket disconnected (reader) for session {session_id}")
        except Exception as e:
            logging.error(f"WebSocket reader error: {e}")

    # Writer: simulate transcription + LLM streaming sequence
    async def writer():
        try:
            # Simulated partial transcription
            await websocket.send_text(json.dumps({
                "type": "transcription",
                "transcript": "Listening…",
                "end_of_turn": False,
                "turn_is_formatted": False,
                "turn_order": 1
            }))
            await asyncio.sleep(0.4)
            await websocket.send_text(json.dumps({
                "type": "transcription",
                "transcript": "Sample question captured",
                "end_of_turn": True,
                "turn_is_formatted": True,
                "turn_order": 1
            }))

            # Simulated LLM streaming
            await websocket.send_text(json.dumps({"type": "llm_start", "transcript": "Sample question captured"}))
            chunks = ["Here's a short ", "demo response ", "from the server."]
            full = ""
            for c in chunks:
                await asyncio.sleep(0.25)
                full += c
                await websocket.send_text(json.dumps({"type": "llm_chunk", "text": c}))
            await websocket.send_text(json.dumps({"type": "llm_complete", "full_response": full}))
        except WebSocketDisconnect:
            logging.info(f"WebSocket disconnected (writer) for session {session_id}")
        except Exception as e:
            logging.error(f"WebSocket writer error: {e}")
            try:
                await websocket.send_text(json.dumps({"type": "llm_error", "error": "Internal error"}))
            except Exception:
                pass

    reader_task = asyncio.create_task(reader())
    writer_task = asyncio.create_task(writer())
    try:
        await asyncio.gather(reader_task, writer_task)
    finally:
        for t in (reader_task, writer_task):
            if not t.done():
                t.cancel()
        logging.info(f"WebSocket session closed for {session_id}")

