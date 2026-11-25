"""Live web re-verification through Tavily."""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Optional

import httpx

from .config import Settings
from .models import CallTrace, Category

# Categories that change fast get a recency-restricted search; slower ones
# search the past couple of years so authoritative docs pages still appear.
_RECENCY_WINDOW_DAYS = {
    Category.PRICING: 365,
    Category.API_VERSION: 365,
    Category.COMPANY_INFO: 365,
    Category.PRODUCT: 365,
    Category.STATISTIC: 730,
    Category.REGULATION: 730,
}


SOCIAL_DOMAINS = ["instagram.com", "facebook.com", "tiktok.com", "pinterest.com", "quora.com", "threads.net"]


class TavilyClient:
    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None):
        self.s = settings
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout)
        self._cache: dict[tuple, list[dict]] = {}

    async def search(
        self,
        query: str,
        category: Category,
        trace: list[CallTrace],
        include_domains: Optional[list[str]] = None,
    ) -> list[dict]:
        key = (query.lower().strip(), category, tuple(include_domains or ()))
        if key in self._cache:
            trace.append(CallTrace(kind="search", purpose=category.value, target=query, cached=True))
            return self._cache[key]

        body: dict = {
            "query": query[:400],
            "search_depth": self.s.search_depth,
            "max_results": self.s.results_per_claim,
            "include_published_date": True,
            "chunks_per_source": 3,
            "topic": "news" if category == Category.COMPANY_INFO else "general",
        }
        window = _RECENCY_WINDOW_DAYS.get(category)
        if window:
            body["start_date"] = (date.today() - timedelta(days=window)).isoformat()
        if include_domains:
            body["include_domains"] = include_domains
            body["include_domains_mode"] = "prefer"
        # social posts and Q&A threads are opinions, not evidence
        body["exclude_domains"] = SOCIAL_DOMAINS

        t0 = time.perf_counter()
        entry = CallTrace(kind="search", purpose=category.value, target=query, served_by="tavily")
        try:
            r = await self._client.post(
                self.s.tavily_url.rstrip("/") + "/search",
                json=body,
                headers={"Authorization": f"Bearer {self.s.tavily_api_key}"},
            )
            if r.status_code == 400 and "start_date" in body:
                body.pop("start_date")  # some query shapes reject date filters
                r = await self._client.post(
                    self.s.tavily_url.rstrip("/") + "/search",
                    json=body,
                    headers={"Authorization": f"Bearer {self.s.tavily_api_key}"},
                )
            r.raise_for_status()
            results = r.json().get("results", [])
        except (httpx.HTTPError, ValueError) as e:
            entry.error = str(e)[:200]
            results = []
        entry.latency_ms = int((time.perf_counter() - t0) * 1000)
        trace.append(entry)
        if results:
            self._cache[key] = results
        return results

    async def aclose(self) -> None:
        await self._client.aclose()
