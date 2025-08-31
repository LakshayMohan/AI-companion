<div align="center">
# AI Voice Companion
</div>
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
│  + UI)     │                  │ (AudioStreamer)│      │  (STT)       │
└────┬───────┘                  └──────┬───────┘         └─────────────┘
	 │ LLM stream (JSON events)        │ final transcript
	 ▼                                 ▼
┌────────────┐   prompt/history  ┌─────────────┐  weather/search  ┌──────────┐
│  Chat UI   │◀──────────────────│ Gemini API  │◀────────────────▶│ Tavily   │
└────────────┘                    └─────────────┘                  └──────────┘
	▲                                      │
	│ audio (proxy /tts/fetch/{id})        │
	│                                      ▼
┌────────┐     WS (upstream)       ┌────────────┐   ┌─────────────┐
│ Player │◀────────────────────────│  Murf TTS  │◀──│ app_core/tts │
└────────┘                         └────────────┘   └─────────────┘
	▲                                     ▲
	│ mood detection                      │ playlist search
	│                                     │
	└────────────── Spotify API ◀─────────┘

Where the server responsibilities are split across modules:
- `main.py` — app routes, WebSocket wiring and app startup
- `app_core/history.py` — session chat memory and helpers
- `app_core/config.py` — runtime key management and static defaults
- `app_core/intents.py` — lightweight intent detection (weather/search)
- `app_core/weather.py` — OpenCage + Open-Meteo helpers
- `app_core/search.py` — Tavily search wrapper
- `app_core/spotify.py` — Spotify token, mood detection, playlist/track helpers
- AudioStreamer (class in `main.py`) — handles AssemblyAI streaming, buffers transcriptions and triggers LLM/TTS flows

This modular layout keeps business logic (search/weather/spotify) separated from routing and streaming glue, making the code easier to test and evolve.
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
├── main.py                    # FastAPI app (routes, WebSocket endpoint, startup)
├── app_core/                  # Modular business logic and helpers
│   ├── __init__.py
│   ├── config.py              # runtime key toggles & static defaults
│   ├── history.py             # in-memory chat history helpers
│   ├── intents.py             # simple intent detectors (weather, search)
│   ├── weather.py             # OpenCage + Open-Meteo helpers
│   ├── search.py              # Tavily search helper
│   └── spotify.py             # Spotify token & playlist helpers
├── static/
│   ├── index.html             # UI markup + config modal
│   ├── script.js              # Client logic (audio, websocket, chat, config)
│   ├── styles.css             # Visual design, responsive + modal styling
│   └── recorderWorklet.js     # AudioWorklet processor (capturing PCM frames)
├── requirements.txt           # Python dependencies
└── README.md                  # This documentation
```

---

## 5. Runtime Keys (Environment File Disabled)

All API keys must be supplied **at runtime** via the UI Config modal or a direct POST to `/config/keys`.

Why:
* Prevent accidental use of committed secrets.
* Make it explicit which keys are active (only those you intentionally inject this session).
* Easier key rotation during development without restarts.

Runtime POST body (all required):
```json
{ "murf": "<MURF_API_KEY>", "assemblyai": "<ASSEMBLYAI_API_KEY>", "gemini": "<GEMINI_API_KEY>" }
```
Optional (enter through UI for extra features):
* `OPENCAGE_API_KEY` – weather geocoding
* `TRAVILY_API_KEY` – web search
* `CLIENT_ID` / `CLIENT_SECRET` – Spotify playlists

Other (static) configuration still uses module defaults (e.g. `MURF_CONTEXT_ID`). If you need to customize those, edit `app_core/config.py` or extend the runtime endpoint.

> IMPORTANT: Since `.env` loading is disabled, adding or editing a `.env` file has **no effect** unless you revert the change in `app_core/config.py`.

Security tip: When you finish a session, refresh without re‑injecting keys to clear them from memory.

---

## 6. Setup & Run

### 6.1 Install Dependencies
```bash
pip install -r requirements.txt
```

### 6.2 Launch Dev Server
```bash
uvicorn main:app --reload
```

### 6.3 Access UI & Inject Keys
Visit: [https://ai-companion-ihj1.onrender.com] or [http://127.0.0.1:8000/] (on local machine)

1. Open the Config (gear) modal.
2. Enter Murf, AssemblyAI, and Gemini keys (required). Save.
3. (Optional) Enter weather/search/Spotify keys and save again.
4. Press Record.

Headless / programmatic injection example (PowerShell): when working on local machine
```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/config/keys -Body (@{murf='YOUR_MURF';assemblyai='YOUR_AAI';gemini='YOUR_GEMINI'} | ConvertTo-Json) -ContentType 'application/json'
```

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
| Secrets in Repo | (If committed) must be rotated. | Remove secrets from commits, rotate exposed keys. `.env` now ignored. |
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

---
## 🏆 30-Day Challenge Journey

<details>
<summary><strong>🎯 Click to see complete development timeline</strong></summary>

### **Days 1-10: Foundation**

- ✅ **Day 1**: Basic FastAPI web app with dummy response simulation
- ✅ **Day 2**: REST API endpoint for text-to-speech with secure .env configuration
- ✅ **Day 3**: Frontend UI development with JavaScript fetch API integration
- ✅ **Day 4**: Client-side "Echo Bot" using MediaRecorder API
- ✅ **Day 5**: Client-to-server audio uploading implementation
- ✅ **Day 6**: AssemblyAI speech-to-text integration with glassmorphism UI
- ✅ **Day 7**: Echo Bot v2 with voice selection feature
- ✅ **Day 8**: Google Gemini LLM integration for intelligent responses
- ✅ **Day 9**: Complete end-to-end conversational pipeline
- ✅ **Day 10**: Chat history and auto-record feature implementation

### **Days 11-20: Enhancement**

- ✅ **Day 11**: Full-stack error handling with fallback audio
- ✅ **Day 12**: UI refinement with conversation logs
- ✅ **Day 13**: Professional README.md documentation
- ✅ **Day 14**: Service-oriented architecture refactoring
- ✅ **Day 15**: WebSocket real-time communication channel
- ✅ **Day 16**: Real-time audio streaming implementation
- ✅ **Day 17**: Streaming transcription with Web Audio API
- ✅ **Day 18**: Intelligent turn detection with AssemblyAI
- ✅ **Day 19**: Streaming LLM responses integration
- ✅ **Day 20**: Murf AI WebSocket streaming for TTS

### **Days 21-30: Advanced Features**

- ✅ **Day 21**: Base64 audio streaming to client over WebSocket
- ✅ **Day 22**: Complete streaming pipeline with Web Audio API playback
- ✅ **Day 23**: Seamless end-to-end conversation flow with context
- ✅ **Day 24**: VoiceIQ personality development with system prompting
- ✅ **Day 25**: Advanced conversation features and error handling
- ✅ **Day 26**: Web search integration with Tavily API
- ✅ **Day 27**: Dynamic configuration management system
- ✅ **Day 28**: Cloud deployment on Render.com with HTTP streaming
- ✅ **Day 29**: Comprehensive documentation and feature updates
- 🎯 **Day 30**: Final showcase and project completion

</details>

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

MIT License

Copyright (c) 2025 Lakshay Mohan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## 17. Disclaimer

This project streams audio & interacts with third-party APIs. Ensure compliance with each provider’s Terms of Service and local privacy regulations. Rotate any keys that have been committed to version control.

---

## 18. Quick Start (TL;DR)

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# Browser: (https://ai-companion-ihj1.onrender.com) → Config → Enter keys → Save → Record
```
---

## 19. Acknowledgments

- **[Murf AI](https://murf.ai/)** - For the incredible voice synthesis technology and the 30-day challenge
- **[AssemblyAI](https://www.assemblyai.com/)** - For robust speech recognition capabilities
- **[Google](https://ai.google.dev/)** - For the powerful Gemini language model
- **[Tavily](https://tavily.com/)** - For real-time web search integration
- **[Render.com](https://render.com/)** - For reliable cloud hosting

---

https://ai-companion-ihj1.onrender.com
For production guidance (auth, rate limiting, secrets management) see Section 11.

Feel free to adapt and extend! PRs welcome once a contribution workflow & license are defined.

