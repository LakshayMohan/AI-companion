# app.py
import os
import logging
from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import assemblyai as aai
import google.generativeai as genai
from collections import defaultdict
import asyncio
import wave
import io
import time
from datetime import datetime

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Load Secrets ---
load_dotenv()
MURF_API_KEY = os.getenv("MURF_API_KEY")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if ASSEMBLYAI_API_KEY:
    aai.settings.api_key = ASSEMBLYAI_API_KEY
else:
    logging.warning("ASSEMBLYAI_API_KEY not set.")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    logging.warning("GEMINI_API_KEY not set.")

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
chat_history = defaultdict(list)

# --- Test endpoint to list recordings ---
@app.get("/recordings")
async def list_recordings():
    """List all saved audio recordings"""
    try:
        recordings_dir = "recordings"
        if not os.path.exists(recordings_dir):
            return {"recordings": [], "message": "No recordings directory found"}
        
        recordings = []
        for filename in os.listdir(recordings_dir):
            if filename.endswith('.wav'):
                filepath = os.path.join(recordings_dir, filename)
                file_size = os.path.getsize(filepath)
                recordings.append({
                    "filename": filename,
                    "size_bytes": file_size,
                    "size_mb": round(file_size / (1024 * 1024), 2)
                })
        
        return {
            "recordings": recordings,
            "count": len(recordings),
            "message": f"Found {len(recordings)} recordings"
        }
    except Exception as e:
        logging.error(f"Error listing recordings: {e}")
        raise HTTPException(status_code=500, detail="Error listing recordings")

# --- WebSocket Audio Recording ---
class AudioRecorder:
    def __init__(self):
        self.active_recordings = {}
    
    async def start_recording(self, session_id: str):
        """Start a new audio recording session"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recordings/audio_{session_id}_{timestamp}.wav"
        
        # Ensure recordings directory exists
        os.makedirs("recordings", exist_ok=True)
        
        # Create WAV file with proper headers
        with wave.open(filename, 'wb') as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(44100)  # 44.1kHz
        
        self.active_recordings[session_id] = {
            'filename': filename,
            'start_time': time.time(),
            'data_chunks': []
        }
        logging.info(f"Started recording session {session_id} -> {filename}")
        return filename
    
    async def add_audio_data(self, session_id: str, audio_data: bytes):
        """Add audio data to the current recording"""
        if session_id not in self.active_recordings:
            logging.warning(f"Received audio data for unknown session: {session_id}")
            return
        
        recording = self.active_recordings[session_id]
        recording['data_chunks'].append(audio_data)
        logging.debug(f"Added {len(audio_data)} bytes to session {session_id}")
    
    async def stop_recording(self, session_id: str):
        """Stop recording and save the complete audio file"""
        if session_id not in self.active_recordings:
            logging.warning(f"Attempted to stop unknown recording session: {session_id}")
            return None
        
        recording = self.active_recordings[session_id]
        filename = recording['filename']
        
        # Combine all audio chunks and write to file
        try:
            with wave.open(filename, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(44100)  # 44.1kHz
                
                # Write all audio data
                for chunk in recording['data_chunks']:
                    wav_file.writeframes(chunk)
            
            duration = time.time() - recording['start_time']
            logging.info(f"Stopped recording session {session_id} -> {filename} (duration: {duration:.2f}s)")
            
            # Clean up
            del self.active_recordings[session_id]
            return filename
            
        except Exception as e:
            logging.error(f"Error saving audio file for session {session_id}: {e}")
            if session_id in self.active_recordings:
                del self.active_recordings[session_id]
            return None

# Global audio recorder instance
audio_recorder = AudioRecorder()

@app.websocket("/ws/audio/{session_id}")
async def websocket_audio_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    logging.info(f"WebSocket connection established for session: {session_id}")
    
    try:
        # Start recording
        filename = await audio_recorder.start_recording(session_id)
        await websocket.send_text(f"Recording started: {filename}")
        
        while True:
            # Receive audio data from client
            data = await websocket.receive_bytes()
            
            # Add audio data to recording
            await audio_recorder.add_audio_data(session_id, data)
            
            # Send acknowledgment
            await websocket.send_text("Audio data received")
            
    except WebSocketDisconnect:
        logging.info(f"WebSocket disconnected for session: {session_id}")
        # Stop recording when client disconnects
        final_filename = await audio_recorder.stop_recording(session_id)
        if final_filename:
            logging.info(f"Recording saved: {final_filename}")
    except Exception as e:
        logging.error(f"WebSocket error for session {session_id}: {e}")
        # Clean up recording on error
        await audio_recorder.stop_recording(session_id)

@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

@app.get("/proxy-audio/")
async def proxy_audio(url: str):
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url)
            r.raise_for_status()
            return StreamingResponse(
                iter([r.content]),
                media_type="audio/mpeg",
                headers={"Access-Control-Allow-Origin": "*"}
            )
        except httpx.RequestError as e:
            logging.error(f"Audio proxy failed: {e.request.url} - {e}")
            raise HTTPException(status_code=502, detail="Could not fetch audio.")

# --- TTS Endpoint ---
@app.post("/tts")
async def generate_tts(text: str):
    if not MURF_API_KEY:
        logging.error("TTS endpoint called but MURF_API_KEY missing.")
        raise HTTPException(status_code=500, detail="TTS service not configured.")
    headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
    payload = {"text": text, "voiceId": "en-US-natalie"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            audio_url = data.get("audioFile")
            if not audio_url:
                logging.error("Murf API succeeded but no audioFile.")
                raise HTTPException(status_code=502, detail="TTS API error: no audio.")
            return {"audio_url": audio_url}
    except httpx.HTTPStatusError as e:
        logging.error(f"Murf TTS error: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=502, detail="Failed to communicate with TTS.")
    except Exception as e:
        logging.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail="TTS internal error.")

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
            return JSONResponse(status_code=503, content={"error": "Could not process your audio.", "audio_url": fallback_audio_url})
        return JSONResponse(status_code=503, content={"error": "Speech-to-text unavailable."})

    chat_history[session_id].append({"role": "user", "parts": [user_text]})

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
            return JSONResponse(status_code=503, content={"error": "AI Model unavailable.", "audio_url": fallback_audio_url, "transcription": user_text})
        return JSONResponse(status_code=503, content={"error": "AI Model unavailable."})

    chat_history[session_id].append({"role": "model", "parts": [llm_text]})

    try:
        if not MURF_API_KEY: raise ValueError("Murf API key not set.")
        murf_text = llm_text[:2900]
        headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
        payload = {"text": murf_text, "voiceId": "en-US-marcus"}
        async with httpx.AsyncClient(timeout=90) as client:
            murf_resp = await client.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=payload)
            murf_resp.raise_for_status()
            audio_url = murf_resp.json().get("audioFile")
            if not audio_url: raise RuntimeError("Murf API no audio URL.")
    except Exception as e:
        logging.error(f"TTS error: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "error": "Voice generation unavailable.",
                "transcription": user_text,
                "llm_response": llm_text
            }
        )
    return {
        "audio_url": audio_url,
        "transcription": user_text,
        "llm_response": llm_text
    }
# @app.get("/agent/chat/history/{session_id}")
# async def get_chat_history(session_id: str):
#     if session_id not in chat_history:
#         return JSONResponse(status_code=404, content={"error": "Session not found."})
#     return {"history": chat_history[session_id]}    