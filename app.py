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
import base64
from typing import Optional

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

    # Connect to AssemblyAI Realtime if configured
    aai_ws = None
    aai_task = None
    turn_counter = 0
    pending_llm: Optional[asyncio.Task] = None

    async def connect_aai():
        nonlocal aai_ws
        if not ASSEMBLYAI_API_KEY:
            logging.warning("ASSEMBLYAI_API_KEY not configured; realtime transcription disabled")
            return None
        try:
            import websockets  # lazy import; requires 'websockets' package
            url = "wss://api.assemblyai.com/v2/realtime/ws?sample_rate=16000"
            aai_ws = await websockets.connect(
                url,
                extra_headers={"Authorization": ASSEMBLYAI_API_KEY}
            )
            logging.info("Connected to AssemblyAI Realtime WS")
            return aai_ws
        except Exception as e:
            logging.error(f"Failed to connect to AssemblyAI Realtime: {e}")
            aai_ws = None
            return None

    async def close_aai():
        nonlocal aai_ws
        if aai_ws:
            try:
                await aai_ws.close()
            except Exception:
                pass
            aai_ws = None

    # Reader task: consume incoming frames from client and forward to AssemblyAI
    async def reader():
        try:
            while True:
                message = await websocket.receive()
                if "bytes" in message and message["bytes"] is not None:
                    # Forward PCM16 bytes to AssemblyAI as base64 JSON frames
                    if aai_ws is not None:
                        try:
                            aud_b64 = base64.b64encode(message["bytes"]).decode("ascii")
                            await aai_ws.send(json.dumps({"audio_data": aud_b64}))
                        except Exception as e:
                            logging.error(f"Error forwarding audio to AAI: {e}")
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

    # Writer: hook into AssemblyAI events and trigger LLM/TTS on final
    async def writer():
        nonlocal turn_counter, pending_llm
        try:
            if aai_ws is None:
                # No realtime; nothing to stream from AAI
                return
            while True:
                try:
                    msg = await aai_ws.recv()
                except Exception as e:
                    logging.error(f"AAI recv error: {e}")
                    break
                try:
                    payload = json.loads(msg)
                except Exception:
                    continue

                msg_type = payload.get("message_type") or payload.get("type")
                text = payload.get("text", "")
                is_final = (msg_type == "FinalTranscript") or payload.get("final", False)

                if not text:
                    continue

                # Forward transcription event to client
                await websocket.send_text(json.dumps({
                    "type": "transcription",
                    "transcript": text,
                    "end_of_turn": bool(is_final),
                    "turn_is_formatted": bool(is_final),
                    "turn_order": turn_counter + 1
                }))

                if is_final and text.strip():
                    turn_counter += 1
                    # Cancel any pending LLM task for safety
                    if pending_llm and not pending_llm.done():
                        pending_llm.cancel()
                    pending_llm = asyncio.create_task(_run_llm_and_tts(websocket, session_id, text))
        except WebSocketDisconnect:
            logging.info(f"WebSocket disconnected (writer) for session {session_id}")
        except Exception as e:
            logging.error(f"WebSocket writer error: {e}")
            try:
                await websocket.send_text(json.dumps({"type": "llm_error", "error": "Internal error"}))
            except Exception:
                pass

    # Connect upstream AAI first, then run tasks
    await connect_aai()
    reader_task = asyncio.create_task(reader())
    writer_task = asyncio.create_task(writer())
    try:
        await asyncio.gather(reader_task, writer_task)
    finally:
        for t in (reader_task, writer_task):
            if not t.done():
                t.cancel()
        if pending_llm and not pending_llm.done():
            pending_llm.cancel()
        await close_aai()
        logging.info(f"WebSocket session closed for {session_id}")


async def _run_llm_and_tts(websocket: WebSocket, session_id: str, user_text: str):
    """Runs LLM on the final transcript, streams chunks to client, then sends TTS audio as base64."""
    try:
        # Update history
        chat_history[session_id].append({"role": "user", "parts": [user_text]})

        # LLM start
        await websocket.send_text(json.dumps({"type": "llm_start", "transcript": user_text}))

        # Generate response (sync), then chunk it for streaming
        if not GEMINI_API_KEY:
            llm_text = "(Server not configured with GEMINI_API_KEY)"
        else:
            try:
                model = genai.GenerativeModel('gemini-1.5-flash')
                conversation = model.start_chat(history=chat_history[session_id][:-1])
                llm_response = conversation.send_message(user_text)
                llm_text = (llm_response.text or "").strip()
            except Exception as e:
                logging.error(f"Gemini generation failed: {e}")
                llm_text = "I'm sorry, I couldn't generate a response just now."

        # Stream chunks to client
        if llm_text:
            chunk_size = 60
            full_accum = ""
            for i in range(0, len(llm_text), chunk_size):
                chunk = llm_text[i:i+chunk_size]
                full_accum += chunk
                await websocket.send_text(json.dumps({"type": "llm_chunk", "text": chunk}))
                await asyncio.sleep(0.02)
            await websocket.send_text(json.dumps({"type": "llm_complete", "full_response": full_accum}))
            chat_history[session_id].append({"role": "model", "parts": [full_accum]})

        # TTS via Murf
        if MURF_API_KEY and llm_text:
            try:
                headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
                payload = {"text": llm_text[:2900], "voiceId": "en-US-marcus"}
                async with httpx.AsyncClient(timeout=90) as client:
                    murf_resp = await client.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=payload)
                    murf_resp.raise_for_status()
                    audio_url = murf_resp.json().get("audioFile")
                    if audio_url:
                        # Fetch audio bytes and send as base64 final
                        audio_bin = await client.get(audio_url)
                        audio_bin.raise_for_status()
                        b64 = base64.b64encode(audio_bin.content).decode('ascii')
                        await websocket.send_text(json.dumps({"type": "murf_audio_final", "audio_b64": b64}))
            except Exception as e:
                logging.error(f"Murf TTS failed in WS: {e}
")
                # Client will fallback to /tts if it wants
    except Exception as e:
        logging.error(f"_run_llm_and_tts error: {e}")

