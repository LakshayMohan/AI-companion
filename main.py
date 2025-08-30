# app.py
import os
import logging
import json
import base64
from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import websockets
import assemblyai as aai
import google.generativeai as genai
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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Load Secrets ---
load_dotenv()
MURF_API_KEY = os.getenv("MURF_API_KEY")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MURF_CONTEXT_ID = os.getenv("MURF_CONTEXT_ID", "murf_context_global_1")
MURF_WS_URL = os.getenv("MURF_WS_URL", "wss://api.murf.ai/v1/speech/stream-input")

# --- Spotify credentials (for mood-based recommendations) ---
SPOTIFY_CLIENT_ID = os.getenv("CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("CLIENT_SECRET")

# Token cache
_spotify_token: str | None = None
_spotify_token_expires_at: float = 0.0

async def get_spotify_token() -> str | None:
    """Obtain and cache a Spotify client-credentials token.

    Returns the Bearer token string or None on failure.
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
    """Small normalization for mood queries to improve Spotify search results."""
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
    """Mood detector using a configurable checks mapping.

    The `checks` mapping contains for each canonical mood a list of allowed keywords
    (synonyms, short stems, etc.). We return the normalized mood as soon as one of
    the keywords is found in the user's text (whole-word or contained within a word).

    Returns the normalized mood string or 'mood' when nothing matches.
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
    """Search Spotify for playlists matching the mood and return list of {name,url,id} dicts."""
    token = await get_spotify_token()
    if not token:
        return []
    q = _normalize_mood(mood)
    search_url = "https://api.spotify.com/v1/search"
    params = {"q": q, "type": "playlist", "limit": limit}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(search_url, params=params, headers=headers)
            text = resp.text
            # Spotify may return an error payload even with non-200
            try:
                resp.raise_for_status()
            except Exception:
                logging.error(f"Spotify search status error (status={resp.status_code}): {text}")
                # try to parse body for error details
                try:
                    err = resp.json()
                    logging.error("Spotify error body: %s", err)
                except Exception:
                    pass
                return []

            try:
                data = resp.json()
            except Exception:
                logging.error(f"Spotify search returned non-JSON: {text}")
                return []

            # handle explicit error structure
            if isinstance(data, dict) and data.get("error"):
                logging.error("Spotify search returned error payload: %s", data.get("error"))
                return []

            if not isinstance(data, dict):
                logging.error("Unexpected Spotify search response type: %s", type(data))
                return []

            playlists_block = data.get("playlists") or {}
            if not isinstance(playlists_block, dict):
                logging.error("Spotify playlists block missing or invalid: %s", playlists_block)
                return []

            items = playlists_block.get("items") or []
            results = []
            for p in items:
                if not p or not isinstance(p, dict):
                    continue
                image_url = None
                try:
                    images = p.get("images") or []
                    if images and isinstance(images, list):
                        image_url = images[0].get("url") if images[0] else None
                except Exception:
                    image_url = None

                results.append({
                    "name": p.get("name"),
                    "url": (p.get("external_urls") or {}).get("spotify"),
                    "id": p.get("id"),
                    "image": image_url
                })
            return results
    except Exception as e:
        logging.error(f"Spotify search failed: {e}")
        return []

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
MAX_HISTORY_LENGTH = 40  # max messages (user+model) to keep per session

def append_to_history(session_id: str, role: str, text: str):
    """Append a message to session history and trim to MAX_HISTORY_LENGTH."""
    if not session_id:
        return
    entry = {"role": role, "parts": [text], "timestamp": datetime.utcnow().isoformat()}
    chat_history[session_id].append(entry)
    # Trim oldest messages if over limit
    if len(chat_history[session_id]) > MAX_HISTORY_LENGTH:
        # drop the oldest entries
        chat_history[session_id] = chat_history[session_id][-MAX_HISTORY_LENGTH:]



# --- WebSocket Audio Streaming with AssemblyAI Universal Streaming (No Recording) ---
class AudioStreamer:
    def __init__(self):
        self.active_sessions = {}
        self.streaming_clients = {}  # Store AssemblyAI streaming clients per session
        # Transient per-session storage
        self.session_websockets = {}
        self.pending_transcriptions = defaultdict(list)
        self.final_transcripts = {}
        self.murf_audio_chunks = defaultdict(list)

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
                        print(f"Sent transcription to client: {message['transcript']}")
                    # Clear the pending messages after sending
                    self.pending_transcriptions[session_id] = []
                except Exception as e:
                    logging.error(f"Error sending pending transcriptions to client: {e}")
        
        # Check if we have a final transcript to process with LLM
        if hasattr(self, 'final_transcripts') and session_id in self.final_transcripts:
            final_transcript = self.final_transcripts[session_id]
            print(f"Processing final transcript with LLM: {final_transcript}")
            
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
        if session_id in self.murf_audio_chunks:
            try:
                del self.murf_audio_chunks[session_id]
            except Exception:
                pass
        return session_id
    
    async def stream_llm_response(self, session_id: str, user_text: str, websocket):
        """Stream LLM response using Google's Gemini API"""
        try:
            if not GEMINI_API_KEY:
                print("❌ Gemini API key not set")
                return
            
            print(f"Starting LLM streaming for: {user_text}")
            
            # Add user message to chat history (trimmed)
            append_to_history(session_id, "user", user_text)
            
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
                                        print("Murf synthesis complete")
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

            # Add AI response to chat history after finishing (trimmed)
                if full_response_ref["text"]:
                    append_to_history(session_id, "model", full_response_ref["text"])
                    # Detect mood and fetch playlist recommendations only when a concrete mood was found
                    try:
                        # Use the user's original transcript to decide whether to recommend music
                        detected_mood = _detect_mood_from_text(user_text)
                        # _detect_mood_from_text returns 'mood' as a generic fallback; skip recommendations in that case
                        if detected_mood and detected_mood != 'mood':
                            playlists = await search_spotify_playlists(detected_mood, limit=3)
                            if playlists:
                                # Send playlist recommendations to client over websocket
                                try:
                                    await websocket.send_text(json.dumps({
                                        "type": "playlist_recommendations",
                                        "mood": detected_mood,
                                        "playlists": playlists
                                    }))
                                except Exception:
                                    pass
                    except Exception as ex:
                        logging.debug(f"Playlist recommendation failed: {ex}")
            
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


@app.get("/agent/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    """Return in-memory chat history for a session."""
    # If session is unknown, return an empty history instead of 404 to simplify client logic
    if session_id not in chat_history:
        return {"history": []}
    return {"history": chat_history[session_id]}


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
            return JSONResponse(status_code=503, content={"error": "AI Model unavailable.", "audio_url": fallback_audio_url, "transcription": user_text})
        return JSONResponse(status_code=503, content={"error": "AI Model unavailable."})

    append_to_history(session_id, "model", llm_text)

    # automatically fetch playlists for the model reply only when a concrete mood is detected
    playlists_for_response = []
    try:
        # Use the user's original transcript to decide whether to recommend music
        detected_mood = _detect_mood_from_text(user_text)
        if detected_mood and detected_mood != 'mood':
            playlists_for_response = await search_spotify_playlists(detected_mood, limit=3)
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
    resp_payload = {
        "audio_url": audio_url,
        "transcription": user_text,
        "llm_response": llm_text
    }
    if playlists_for_response:
        resp_payload["playlists"] = playlists_for_response
    return resp_payload
# @app.get("/agent/chat/history/{session_id}")
# async def get_chat_history(session_id: str):
#     if session_id not in chat_history:
#         return JSONResponse(status_code=404, content={"error": "Session not found."})
#     return {"history": chat_history[session_id]}    