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
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

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

if not TAVILY_API_KEY:
    logging.warning("TAVILY_API_KEY is not set. Web search will be disabled.")


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


# --- NEW: Tavily web search helper and intent extraction ---
async def tavily_search(query: str, max_results: int = 5, search_depth: str = "basic"):
    if not TAVILY_API_KEY:
        raise ValueError("Tavily API key is not configured.")

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": search_depth,
        "max_results": max_results,
        "include_images": False,
        "include_answer": False,
        "include_domains": [],
        "exclude_domains": []
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logging.error(f"Tavily API request failed: {e.response.status_code} - {e.response.text}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error while calling Tavily: {e}")
        raise


def extract_search_query(user_text: str):
    """Very simple heuristic extraction for web search intent; returns query or None."""
    if not user_text:
        return None
    text = user_text.strip().lower()
    prefixes = [
        "search the web for ",
        "search for ",
        "web search for ",
        "look up ",
        "google ",
        "find information on ",
        "find info on ",
        "what does the internet say about ",
        "browse for "
    ]
    for p in prefixes:
        if text.startswith(p):
            return user_text[len(p):].strip()
    # Also handle: "search the web: ..." style
    markers = [
        "search the web: ",
        "web search: ",
        "search: "
    ]
    for m in markers:
        idx = text.find(m)
        if idx != -1:
            return user_text[idx + len(m):].strip()
    return None


# --- NEW: Summarize Tavily results with Gemini if available; otherwise format links ---
def build_search_summary_prompt(query: str, tavily_results: dict) -> str:
    lines = [
        "You are a helpful assistant. Summarize the web search results to answer the user's query concisely and accurately.",
        "Include 3-6 key points and cite sources inline like [1], [2].",
        "Add a short list of source URLs at the end.",
        f"\nUser query: {query}\n",
        "Search results:" 
    ]
    results = tavily_results.get("results") or []
    for i, r in enumerate(results[:6], start=1):
        title = r.get("title") or "(no title)"
        url = r.get("url") or ""
        content = (r.get("content") or "").strip()
        snippet = content[:500]
        lines.append(f"[{i}] {title}\nURL: {url}\nSnippet: {snippet}\n")
    lines.append("Please provide a current, factual answer and avoid speculation.")
    return "\n".join(lines)

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

    # 2a. Detect and handle web search intent via Tavily
    search_query = extract_search_query(user_text)
    if search_query:
        try:
            if not TAVILY_API_KEY:
                raise ValueError("Tavily API key is not configured.")

            tavily_results = await tavily_search(search_query)

            # Summarize results
            llm_text = None
            if GEMINI_API_KEY:
                model = genai.GenerativeModel('gemini-1.5-flash')
                prompt = build_search_summary_prompt(search_query, tavily_results)
                llm_resp = model.generate_content(prompt)
                llm_text = (getattr(llm_resp, 'text', None) or "").strip()
            if not llm_text:
                # Fallback: compact list of links
                items = []
                for r in (tavily_results.get("results") or [])[:5]:
                    title = r.get("title") or "(no title)"
                    url = r.get("url") or ""
                    items.append(f"- {title}: {url}")
                llm_text = "Here are relevant results I found:\n" + "\n".join(items)

            # Append to history
            chat_history[session_id].append({"role": "model", "parts": [llm_text]})

            # Generate TTS for the summary
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

            return {
                "audio_url": audio_url,
                "transcription": user_text,
                "llm_response": llm_text,
                "used_web_search": True
            }
        except Exception as e:
            logging.error(f"Error during Tavily web search or TTS: {e}")
            fallback_audio_url = await generate_fallback_audio("I'm having trouble searching the web right now.")
            if fallback_audio_url:
                return JSONResponse(status_code=503, content={"error": "Web search is currently unavailable.", "audio_url": fallback_audio_url, "transcription": user_text})
            return JSONResponse(status_code=503, content={"error": "Web search is currently unavailable."})

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

