"""Web search using Tavily API."""
from __future__ import annotations
from typing import Optional
import httpx
from . import config

async def tavily_search(query: str) -> Optional[str]:
    api_key = config.TRAVILY_API_KEY
    if not api_key:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "search_depth": "basic"},
        )
    if r.status_code != 200:
        return None
    data = r.json()
    answer = data.get("answer")
    return answer

__all__ = ["tavily_search"]
