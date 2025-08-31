"""Session chat history management."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any

# In-memory session -> list[message]
chat_history: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
MAX_HISTORY_LENGTH = 40  # max messages (user+model) per session

def append_to_history(session_id: str, role: str, text: str):
    """Append a message to session history and trim to MAX_HISTORY_LENGTH.

    Message schema kept compatible with Gemini SDK expectations: {role, parts:[text]}.
    """
    if not session_id:
        return
    entry = {"role": role, "parts": [text], "timestamp": datetime.utcnow().isoformat()}
    chat_history[session_id].append(entry)
    if len(chat_history[session_id]) > MAX_HISTORY_LENGTH:
        chat_history[session_id] = chat_history[session_id][-MAX_HISTORY_LENGTH:]

__all__ = ["append_to_history", "chat_history", "MAX_HISTORY_LENGTH"]
