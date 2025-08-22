# app.py
import os
import logging
import json
from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import websockets
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
MURF_CONTEXT_ID = os.getenv("MURF_CONTEXT_ID", "murf_context_global_1")
MURF_WS_URL = os.getenv("MURF_WS_URL", "wss://api.murf.ai/v1/speech/stream-input")

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



# --- WebSocket Audio Streaming with AssemblyAI Universal Streaming (No Recording) ---
class AudioStreamer:
    def __init__(self):
        self.active_sessions = {}
        self.streaming_clients = {}  # Store AssemblyAI streaming clients per session
    
    async def start_streaming(self, session_id: str, websocket=None):
        """Start a new audio streaming session with AssemblyAI transcription (no recording)"""
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
                    print(f"AssemblyAI session started: {event.id}")
                    logging.info(f"AssemblyAI session started: {event.id}")
                
                def on_turn(client_instance, event: TurnEvent):
                    if event.transcript:
                        print(f"TRANSCRIPTION: {event.transcript}")
                        logging.info(f"Transcription: {event.transcript}")
                        
                        # Store transcription data for later sending
                        if not hasattr(self, 'pending_transcriptions'):
                            self.pending_transcriptions = {}
                        if session_id not in self.pending_transcriptions:
                            self.pending_transcriptions[session_id] = []
                        
                        message = {
                            "type": "transcription",
                            "transcript": event.transcript,
                            "end_of_turn": event.end_of_turn,
                            "turn_is_formatted": event.turn_is_formatted,
                            "turn_order": event.turn_order
                        }
                        self.pending_transcriptions[session_id].append(message)
                        print(f"Queued transcription for session {session_id}: {event.transcript}")
                        
                        # If this is the final formatted transcript, trigger LLM streaming
                        if event.end_of_turn and event.turn_is_formatted:
                            print(f"Triggering LLM streaming for final transcript: {event.transcript}")
                            # Store the final transcript for LLM processing
                            if not hasattr(self, 'final_transcripts'):
                                self.final_transcripts = {}
                            self.final_transcripts[session_id] = event.transcript
                
                def on_terminated(client_instance, event: TerminationEvent):
                    print(f"AssemblyAI session terminated: {event.audio_duration_seconds} seconds processed")
                    logging.info(f"AssemblyAI session terminated: {event.audio_duration_seconds} seconds")
                
                def on_error(client_instance, error: StreamingError):
                    print(f"TRANSCRIPTION ERROR: {error}")
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
        """Stream audio data to AssemblyAI for real-time transcription (no recording)"""
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
        
        # Send any pending transcriptions to the client
        session_websocket = self.session_websockets.get(session_id)
        if session_websocket and hasattr(self, 'pending_transcriptions') and session_id in self.pending_transcriptions:
            pending_messages = self.pending_transcriptions[session_id]
            if pending_messages:
                try:
                    for message in pending_messages:
                        await session_websocket.send_text(json.dumps(message))
                        print(f"✅ Sent transcription to client: {message['transcript']}")
                    # Clear the pending messages after sending
                    self.pending_transcriptions[session_id] = []
                except Exception as e:
                    logging.error(f"Error sending pending transcriptions to client: {e}")
        
        # Check if we have a final transcript to process with LLM
        if hasattr(self, 'final_transcripts') and session_id in self.final_transcripts:
            final_transcript = self.final_transcripts[session_id]
            print(f"🤖 Processing final transcript with LLM: {final_transcript}")
            
            # Start LLM streaming in background
            asyncio.create_task(self.stream_llm_response(session_id, final_transcript, session_websocket))
            
            # Remove the processed transcript
            del self.final_transcripts[session_id]
        
        logging.debug(f"Streamed {len(audio_data)} bytes to session {session_id}")
    
    async def stop_streaming(self, session_id: str):
        """Stop streaming session (no recording)"""
        if session_id not in self.active_sessions:
            logging.warning(f"Attempted to stop unknown streaming session: {session_id}")
            return None
        
        session = self.active_sessions[session_id]
        
        # Close AssemblyAI Universal Streaming client
        if session_id in self.streaming_clients and self.streaming_clients[session_id]:
            try:
                self.streaming_clients[session_id].disconnect(terminate=True)
                print("AssemblyAI Universal Streaming client disconnected")
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
        if hasattr(self, 'pending_transcriptions') and session_id in self.pending_transcriptions:
            del self.pending_transcriptions[session_id]
        if hasattr(self, 'final_transcripts') and session_id in self.final_transcripts:
            del self.final_transcripts[session_id]
        return session_id
    
    async def stream_llm_response(self, session_id: str, user_text: str, websocket):
        """Stream LLM response using Google's Gemini API"""
        try:
            if not GEMINI_API_KEY:
                print("❌ Gemini API key not set")
                return
            
            print(f"🤖 Starting LLM streaming for: {user_text}")
            
            # Add user message to chat history
            if session_id not in chat_history:
                chat_history[session_id] = []
            chat_history[session_id].append({"role": "user", "parts": [user_text]})
            
            # Initialize Gemini model
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            # Send start message to client
            start_message = {
                "type": "llm_start",
                "transcript": user_text
            }
            await websocket.send_text(json.dumps(start_message))
            print(f"Sent LLM start message to client")
            
            # Stream response using synchronous generator in a background thread
            loop = asyncio.get_running_loop()
            full_response_ref = {"text": ""}

            # Murf WebSocket: open once per LLM response, reuse static context_id
            async def murf_streamer(text_stream_queue: asyncio.Queue):
                if not MURF_API_KEY:
                    print("❌ MURF_API_KEY not set, skipping TTS stream")
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
                                            print(f"Murf audio chunk (base64): {audio_b64[:80]}...")
                                    if data.get("final"):
                                        print("Murf synthesis complete")
                                        break
                            except Exception as ex:
                                print(f"❌ Murf receiver error: {ex}")

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
                    print(f"❌ Murf websocket error: {ex}")

            # Queue to bridge LLM text chunks to Murf
            text_queue: asyncio.Queue[str | None] = asyncio.Queue()
            murf_task = asyncio.create_task(murf_streamer(text_queue))

            def stream_sync():
                try:
                    stream = model.generate_content(
                        user_text,
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
                            print(f"Sent LLM chunk: {text_chunk}")
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
                    print(f"LLM streaming completed: {full_response_ref['text']}")
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

            # Add AI response to chat history after finishing
            if full_response_ref["text"]:
                chat_history[session_id].append({"role": "model", "parts": [full_response_ref["text"]]})
            
        except Exception as e:
            print(f"❌ Error in LLM streaming: {e}")
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
            
            # Send acknowledgment
            await websocket.send_text("Audio data streamed")
            
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