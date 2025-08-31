"""Web search using Tavily API.

Provides a resilient async helper that:
 - Returns the explicit `answer` field when present.
 - Otherwise builds a short summary from the first result (content/title/url).
 - Gracefully returns None on network or API errors.
"""
from __future__ import annotations
from typing import Optional, Dict, Any
import httpx
from . import config

async def tavily_search(query: str) -> Optional[str]:
    api_key = config.TRAVILY_API_KEY
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "search_depth": "basic"},
            )
        if r.status_code != 200:
            return None
        data: Dict[str, Any] = r.json() if r.content else {}
        # Prefer direct answer
        if isinstance(data, dict):
            direct = data.get("answer") or data.get("content") or data.get("summary")
            if isinstance(direct, str) and direct.strip():
                return direct.strip()
            # Look into results list
            results = data.get("results")
            if isinstance(results, list) and results:
                top = results[0]
                if isinstance(top, dict):
                    parts = []
                    for key in ("content", "title"):
                        v = top.get(key)
                        if isinstance(v, str) and v.strip():
                            parts.append(v.strip())
                    url = top.get("url") or top.get("source") or top.get("link")
                    if url:
                        parts.append(f"Source: {url}")
                    if parts:
                        return " \n".join(parts)
        return None
    except Exception:
        return None

__all__ = ["tavily_search"]
