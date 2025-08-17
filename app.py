# app.py
from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import httpx
import assemblyai as aai
import google.generativeai as genai
from collections import defaultdict
import logging
import uuid # NEW: For generating unique filenames

# Basic Logging Configuration
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

# --- CORS Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- In-Memory Store and Static Files ---
chat_history = defaultdict(list)
app.mount("/static", StaticFiles(directory="static"), name="static")

# NEW: Create a directory for storing recordings if it doesn't exist
RECORDINGS_DIR = "recordings"
os.makedirs(RECORDINGS_DIR, exist_ok=True)


@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

# --- NEW: WebSocket Endpoint for Audio Streaming ---
# This endpoint handles the real-time streaming of audio from the client.
@app.websocket("/ws/audio/{session_id}")
async def websocket_audio_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    
    # Generate a unique, secure filename for the recording
    # Using UUID prevents filename conflicts and path traversal attacks.
    safe_filename = f"recording_{session_id}_{uuid.uuid4()}.webm"
    file_path = os.path.join(RECORDINGS_DIR, safe_filename)

    logging.info(f"Client '{session_id}' connected. Saving audio to '{file_path}'")
    
    try:
        # Open the file in binary write mode
        with open(file_path, "wb") as audio_file:
            # Loop to receive audio chunks from the client
            while True:
                data = await websocket.receive_bytes()
                audio_file.write(data)
    except WebSocketDisconnect:
        logging.info(f"Client '{session_id}' disconnected. Audio stream saved successfully to '{file_path}'.")
    except Exception as e:
        logging.error(f"An error occurred for client '{session_id}': {e}")
        # Clean up the partial file in case of an error
        if os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"Removed partial file '{file_path}' due to error.")
    # Note: For production, you might add limits on file size or stream duration
    # to prevent resource exhaustion from malicious clients.


# --- Existing Endpoints (Remain for potential future use) ---

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
            logging.error(f"Failed to proxy audio from {e.request.url}: {e}")
            raise HTTPException(status_code=502, detail="Could not fetch audio from the provider.")

class TTSRequest(BaseModel):
    text: str
    voiceId: str = "en-US-natalie"

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

async def generate_fallback_audio(error_message: str = "I'm sorry, I'm having trouble connecting right now. Please try again later."):
    if not MURF_API_KEY:
        return None
    
    headers = {"api-key": MURF_API_KEY, "Content-Type": "application/json"}
    payload = {"text": error_message, "voiceId": "en-US-marcus"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            murf_resp = await client.post("https://api.murf.ai/v1/speech/generate", headers=headers, json=payload)
            murf_resp.raise_for_status()
            return murf_resp.json().get("audioFile")
    except Exception as e:
        logging.error(f"Failed to generate fallback audio: {e}")
        return None

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
    
    chat_history[session_id].append({"role": "user", "parts": [user_text]})
    
    # 2. Generate response with Gemini LLM
    try:
        if not GEMINI_API_KEY:
            raise ValueError("Gemini API key is not configured.")
            
        model = genai.GenerativeModel('gemini-1.5-flash')
        conversation = model.start_chat(history=chat_history[session_id][:-1])
        llm_response = conversation.send_message(user_text)
        
        llm_text = (llm_response.text or "").strip()
        if not llm_text:
            raise RuntimeError("LLM returned an empty response.")
            
    except Exception as e:
        logging.error(f"Error during LLM generation: {e}")
        chat_history[session_id].pop()
        fallback_audio_url = await generate_fallback_audio("The AI model is currently unavailable.")
        if fallback_audio_url:
            return JSONResponse(status_code=503, content={"error": "The AI model is currently unavailable.", "audio_url": fallback_audio_url, "transcription": user_text})
        return JSONResponse(status_code=503, content={"error": "The AI model service is unavailable."})
        
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
