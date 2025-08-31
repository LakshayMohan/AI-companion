"""Intent detection helpers.

Quick pattern-based checks before invoking the LLM for faster responses
(weather, web search, etc.). Keep stateless / pure to simplify testing.
"""
from __future__ import annotations
from typing import Optional
import re

WEATHER_PATTERNS = [
    re.compile(r"\bweather\b", re.I),
    re.compile(r"\btemperature\b", re.I),
]

SEARCH_PATTERNS = [
    re.compile(r"\bsearch\b", re.I),
    re.compile(r"\bfind\b", re.I),
    re.compile(r"\blook up\b", re.I),
]

def detect_weather_intent(text: str) -> bool:
    return any(p.search(text) for p in WEATHER_PATTERNS)

def detect_search_intent(text: str) -> bool:
    return any(p.search(text) for p in SEARCH_PATTERNS)

__all__ = [
    "detect_weather_intent",
    "detect_search_intent",
]
