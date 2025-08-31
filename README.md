# AI Voice Companion (Murf / AssemblyAI / Gemini / Tavily / OpenCage / Spotify)

Comprehensive real‑time voice + AI companion with:

* Low‑latency browser → server PCM audio streaming (WebSocket)
* AssemblyAI Universal Streaming real‑time transcription
* Google Gemini (Generative AI) response streaming
* Murf TTS (text → natural voice) with secure proxy + chunk assembly
* Weather lookup (OpenCage Geocoding + Open‑Meteo current weather)
* Web search (Tavily) for live information queries
* Mood → Spotify playlist recommendations (client credential flow)
* Session memory & chat history management
* In‑browser secure(ish) config modal with runtime key injection endpoint

---

## 1. Architecture Overview

```
┌────────────┐   PCM (16kHz)   ┌──────────────┐         ┌─────────────┐
│  Browser   │ ───────────────▶│  FastAPI WS  │──PCM──▶ │ AssemblyAI   │
│ (Audio API │                  │  /ws/audio   │         │  Streaming   │
│  + UI)     │                  │              │◀ events │  (STT)       │
└────┬───────┘                  └──────┬───────┘         └─────────────┘
	 │ LLM stream (JSON events)        │ final transcript
	 ▼                                 ▼
┌────────────┐   prompt/history  ┌─────────────┐  weather/search  ┌──────────┐
│  Chat UI   │◀──────────────────│ Gemini API  │◀────────────────▶│ Tavily   │
└────────────┘                    └─────────────┘                  └──────────┘
	   ▲                                      │
	   │ audio (proxy /tts/fetch/{id})        │
	   │                                      ▼
   ┌────────┐     WS (upstream)       ┌────────────┐
   │ Player │◀────────────────────────│  Murf TTS  │
   └────────┘                         └────────────┘
	   ▲                                     ▲
	   │ mood detection                      │ playlist search
	   │                                     │
	   └────────────── Spotify API ◀─────────┘
```

Key flows:
1. User presses Record → client captures mic (AudioWorklet or MediaStreamTrackProcessor) → 16kHz PCM Int16 frames → WebSocket `/ws/audio/{session_id}`.
2. Server forwards PCM to AssemblyAI streaming client → partial & formatted turn events buffered → when final formatted transcript appears, Gemini LLM stream begins.
3. Intent shortcuts: if transcript matches weather or search (and corresponding keys configured) server fetches live data and injects it into response events before/alongside LLM.
4. Gemini final response triggers Murf TTS stream (if Murf key present). Audio is chunked and/or assembled; an opaque `tts_id` issued. Browser fetches audio via server `/tts/fetch/{tts_id}` (never seeing provider URL directly).
5. Chat history stored in memory keyed by `session_id` (rotated via `/session/switch`).

---

## 2. Features Summary

| Area | Capability |
|------|------------|
| Audio Capture | Modern AudioWorklet (primary) + MediaStreamTrackProcessor fallback; 16 kHz mono downsampling. |
| Streaming STT | AssemblyAI Universal Streaming (turn events, formatted end-of-turn detection). |
| LLM | Gemini model (`gemini-1.5-flash`) streaming partial and final tokens. |
| TTS | Murf real-time streaming via WebSocket or REST fallback; secured by proxy & opaque IDs. |
| Weather | Geocode (OpenCage) → current weather (Open‑Meteo). |
| Web Search | Tavily API (if TRAVILY_API_KEY present). |
| Spotify | Mood extraction → playlist recommendations (client credentials). |
| Session Memory | Per-session in-memory chat history (trimmed to max length). |
| Config UI | Modal overlay with API key validation, runtime server key injection. |
| Security Enhancements | No direct Murf audio URL exposure, sessionStorage for required keys client-side, server-side key update endpoint. |

---

## 3. Technology Stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI, httpx, asyncio, websockets (Murf TTS streaming), AssemblyAI SDK, Google Generative AI SDK, Tavily SDK |
| Frontend | Vanilla HTML/CSS/JS, AudioContext, AudioWorklet, MediaStreamTrackProcessor fallback |
| Audio Format | 16 kHz, 16‑bit PCM mono frames (Int16) over WebSocket |
| Deployment | Uvicorn (development) |

---

## 4. Project Structure (abridged)

```
.
├── main.py                # FastAPI app (endpoints, WebSocket, AI orchestration)
├── static/
│   ├── index.html         # UI markup + config modal
│   ├── script.js          # Client logic (audio, websocket, chat, config)
│   ├── styles.css         # Visual design, responsive + modal styling
│   └── recorderWorklet.js # AudioWorklet processor (capturing PCM frames)
├── requirements.txt       # Python dependencies
└── README.md              # This documentation
```

---

## 5. Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `MURF_API_KEY` | Yes (for TTS) | Murf text-to-speech API key. |
| `ASSEMBLYAI_API_KEY` | Yes (for STT) | Streaming speech-to-text. |
| `GEMINI_API_KEY` | Yes (for LLM) | Google Gemini responses. |
| `OPENCAGE_API_KEY` | Optional | Geocoding for weather lookups. |
| `TRAVILY_API_KEY` (or `TAVILY_API_KEY`) | Optional | Web search. |
| `CLIENT_ID` / `CLIENT_SECRET` | Optional | Spotify client credentials. |
| `MURF_WS_URL` | Optional | Override Murf streaming endpoint. |
| `MURF_CONTEXT_ID` | Optional | Murf voice context identifier. |
| (Future) `ADMIN_SECRET` | Recommended | Protect runtime key update endpoint (see Security). |

> IMPORTANT: Never commit real production secrets to version control. Rotate any keys already exposed publicly.

---

## 6. Setup & Run

### 6.1 Create & Populate `.env`
```
MURF_API_KEY=...
ASSEMBLYAI_API_KEY=...
GEMINI_API_KEY=...
OPENCAGE_API_KEY=...          # optional
TRAVILY_API_KEY=...           # optional
CLIENT_ID=...                 # optional (Spotify)
CLIENT_SECRET=...             # optional (Spotify)
```

### 6.2 Install Dependencies
```bash
pip install -r requirements.txt
```

### 6.3 Launch Dev Server
```bash
uvicorn main:app --reload
```

### 6.4 Access UI
Visit: http://localhost:8000

---

## 7. Runtime Configuration Modal

1. Click **Config** (gear) button.
2. Enter required keys (Murf / AssemblyAI / Gemini). Save is blocked until all are present.
3. On Save:
   * Client validates required fields.
   * Keys are POSTed to `/config/keys` (runtime injection) — server updates in‑memory configuration.
   * Required keys stored in `sessionStorage` (not persisted after tab close) to minimize surface area.
   * Optional keys (weather/search/music) stored in `localStorage` for convenience.
4. Record button becomes enabled immediately when all required keys are available.

> Production: Disable or protect `/config/keys` behind authentication / secret header (see Security).

---

## 8. Primary Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve main UI. |
| WS | `/ws/audio/{session_id}` | Receive raw PCM audio frames, forward to AssemblyAI, emit events. |
| POST | `/config/keys` | (Dev) Runtime update required API keys (in‑memory). |
| POST | `/session/switch` | Start a new session; resets chat + streaming state. |
| GET | `/agent/chat/history/{session_id}` | Retrieve stored chat history. |
| GET | `/proxy-audio/?url=...` | Server-side audio proxy (for Murf or other audio). |
| GET | `/spotify/playlist/{playlist_id}` | Playlist track details. |
| GET | `/recommend/{mood}` | Mood-based playlist suggestions. |
| POST | `/weather` | Current weather (requires geocode or coords). |
| GET | `/search?q=...` | Tavily web search (if configured). |
| (Internal) | `/tts/fetch/{tts_id}` | Opaque proxy to Murf audio (not exposed directly to client code listing). |

WebSocket outbound event types include (non-exhaustive): `transcription`, `llm_start`, `llm_chunk`, `llm_complete`, `llm_error`, `murf_audio_chunk`, `murf_audio_final`, `playlist_recommendations`.

---

## 9. Audio & Streaming Pipeline

| Stage | Detail |
|-------|--------|
| Capture | AudioWorklet node collects Float32 PCM frames from mic. |
| Downsample | Resampled / truncated to 16 kHz mono Int16 for STT efficiency. |
| Transport | WebSocket binary frames (little-endian Int16 buffer). |
| STT | AssemblyAI Universal Streaming client: emits partial → formatted turn events. |
| LLM | Gemini invoked after final formatted transcript (plus optional weather/search context). |
| TTS | Murf streaming or fallback REST; audio chunks base64 aggregated; final WAV served via proxy. |
| Playback | Browser decodes Blob URL; visualizer/pulse indicates playback state. |

Fallback Hierarchy: AudioWorklet → MediaStreamTrackProcessor → (legacy) ScriptProcessor (removed / deprecated). The code favors modern APIs; legacy branch retained only for broad compatibility if required.

---

## 10. Chat & Intent Handling

* Each session keeps ordered messages (role, parts) up to `MAX_HISTORY_LENGTH` (default 40).
* Lightweight intent detection: pattern checks for weather queries or search queries; if matched the system fetches live data and augments/short-circuits LLM response.
* Mood detection heuristics map transcript text to canonical moods for Spotify.

---

## 11. Security Considerations

| Aspect | Current | Recommendation |
|--------|---------|----------------|
| Secrets in Repo | (If committed) must be rotated. | Remove secrets from commits, rotate exposed keys. |
| `/config/keys` | Open (no auth) for dev convenience. | Require header token (e.g. `ADMIN_SECRET`) or disable entirely in prod. |
| CORS | `*` (wide-open). | Restrict to allowed origins in production. |
| Client Key Storage | Required keys in sessionStorage, optional in localStorage. | Move all secrets to server-only; never send to browser. |
| Murf Audio URL | Hidden via proxy & opaque IDs. | Add expiring cache + size limits. |
| WebSocket | No auth layer. | Add session auth / JWT or signed session IDs. |
| Logging | Keys not logged. | Continue redaction; add structured logging with rotation. |

Hardening Steps:
1. Add `ADMIN_SECRET` env var; require `X-Admin-Secret` header on `/config/keys`.
2. Implement rate limiting (e.g. using `slowapi`).
3. Enforce HTTPS + secure cookie-based auth for sessions.
4. Set strict CSP & other security headers (FastAPI middleware or reverse proxy). 
5. Use persistent datastore (encrypted) for chat history if compliance needed.

---

## 12. Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Record button disabled | Missing required keys | Open Config, provide Murf + AssemblyAI + Gemini keys. |
| No transcription events | AssemblyAI key invalid / network issue | Check server logs, verify key, network egress. |
| LLM says it lacks real-time info | Gemini key missing or intent intercept not triggered | Confirm key & weather/search keys; try a more explicit question. |
| No TTS audio | Murf key missing or TTS error | Inspect server logs for Murf response, verify key. |
| Weather query fails | Missing OpenCage key | Set `OPENCAGE_API_KEY`. |
| Search fails (503) | Tavily key absent | Provide `TRAVILY_API_KEY` or disable search feature. |
| Spotify results empty | Mood not detected / invalid token | Confirm mood word or rotate Spotify credentials. |
| Network error saving keys | `/config/keys` unreachable | Ensure server running & no reverse proxy blocking POST. |

Log Inspection: run with `LOG_LEVEL=DEBUG` (if you wrap logging) or modify `logging.basicConfig` in `main.py`.

---

## 13. Extensibility Roadmap

| Idea | Description |
|------|-------------|
| Auth Layer | Add user auth & per-user session isolation. |
| Persistent Storage | Store chat history + transcripts in a database (PostgreSQL / Redis). |
| Multi-Voice Support | Expose Murf voice selection in UI config. |
| Focus / A11y | Add focus trap & keyboard shortcuts (Esc to close modal, Space to toggle recording). |
| Offline Mode | Local fallback STT/LLM models (e.g. Whisper + local LLM) when cloud keys absent. |
| Analytics | Aggregate anonymized session metrics (duration, commands) with opt-in. |
| Caching Layer | Cache weather/search responses per TTL to reduce API usage. |

---

## 14. Development Notes

* Restart required only when changing environment variables (unless using runtime key endpoint in dev).
* Avoid printing raw binary/audio to logs. Keep logs small for performance.
* WebSocket error handling returns simple status updates to client; extend with structured codes if building a richer UX.

---

## 15. Contributing

1. Fork / branch `feature/<name>`
2. Add or update tests (future test harness recommended)
3. Ensure no secrets in commits (`git diff` scan before push)
4. Submit PR with architectural rationale in description

---

## 16. License

Project currently distributed without an explicit OSS license declaration. Add a LICENSE file (e.g., MIT or Apache-2.0) if you intend to open-source. Until then, all rights reserved by the author.

---

## 17. Disclaimer

This project streams audio & interacts with third-party APIs. Ensure compliance with each provider’s Terms of Service and local privacy regulations. Rotate any keys that have been committed to version control.

---

## 18. Quick Start (TL;DR)

```bash
pip install -r requirements.txt
cp .env.example .env   # (create and populate with valid keys)
uvicorn main:app --reload
# Open browser → http://localhost:8000 → Config → Enter keys → Save → Record
```

---

For production guidance (auth, rate limiting, secrets management) see Section 11.

Feel free to adapt and extend! PRs welcome once a contribution workflow & license are defined.

