"""Core application modules for the voice agent.

This package modularizes the previously monolithic main.py for maintainability.
"""
from . import config, intents, spotify, weather, search, history
__all__ = ["config", "intents", "spotify", "weather", "search", "history"]
