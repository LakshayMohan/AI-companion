"""Configuration & environment variable loading.

Central place to read environment variables and expose runtime mutability for
keys updated via the /config/keys endpoint. Avoid scattering os.getenv calls
throughout the codebase.
"""
from __future__ import annotations
import os
from typing import Optional

# IMPORTANT: We intentionally do NOT load values from a .env file now.
# The application relies exclusively on runtime-provided keys (via /config/keys).
MURF_API_KEY: Optional[str] = None
ASSEMBLYAI_API_KEY: Optional[str] = None
GEMINI_API_KEY: Optional[str] = None

# Optional services
OPENCAGE_API_KEY: Optional[str] = None
TRAVILY_API_KEY: Optional[str] = None
SPOTIFY_CLIENT_ID: Optional[str] = None
SPOTIFY_CLIENT_SECRET: Optional[str] = None

# Murf context configuration
MURF_CONTEXT_ID = "murf_context_global_1"
MURF_WS_URL = "wss://api.murf.ai/v1/speech/stream-input"

# Mutable runtime update (used by /config/keys)

def update_runtime_keys(murf: str, assembly: str, gemini: str):
    """Update in-memory API keys. Called when user saves keys via the UI.

    NOTE: This does not persist to disk; use environment variables or a secure
    secret manager in production.
    """
    global MURF_API_KEY, ASSEMBLYAI_API_KEY, GEMINI_API_KEY
    MURF_API_KEY = murf
    ASSEMBLYAI_API_KEY = assembly
    GEMINI_API_KEY = gemini
    return {
        "murf": bool(MURF_API_KEY),
        "assemblyai": bool(ASSEMBLYAI_API_KEY),
        "gemini": bool(GEMINI_API_KEY),
    }
