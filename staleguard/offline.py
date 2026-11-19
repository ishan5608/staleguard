"""Offline mode: run the full pipeline with no API keys.

Search results come from a small snapshot corpus (staleguard/data/snapshot.json)
captured for the demo answers. The snapshot is clearly marked in the UI so it is
never mistaken for a live lookup - switch to live mode (set TAVILY_API_KEY and run
Bifrost) for real-time verification.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import CallTrace, Category

_DATA = Path(__file__).parent / "data" / "snapshot.json"


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9.+#-]{2,}", s.lower())
            if w not in {"the", "and", "for", "is", "of", "to", "in", "a", "latest", "current", "version",
                         "release", "news", "status", "pricing", "figures"}}


class SnapshotSearch:
    def __init__(self, path: Path = _DATA):
        self.docs: list[dict] = json.loads(path.read_text())["results"]

    async def search(self, query: str, category: Category, trace: list[CallTrace], include_domains=None) -> list[dict]:
        q = _tokens(query)
        scored = []
        for d in self.docs:
            dt = _tokens(d["title"] + " " + d["content"] + " " + " ".join(d.get("keywords", [])))
            kw = _tokens(" ".join(d.get("keywords", [])))
            overlap = len(q & dt) / max(1, len(q))
            if q & kw and overlap >= 0.15:
                scored.append((overlap, d))
        scored.sort(key=lambda x: -x[0])
        results = [dict(d, score=round(s, 3)) for s, d in scored[:4]]
        trace.append(CallTrace(kind="search", purpose=category.value, target=query, served_by="offline-snapshot"))
        return results

    async def aclose(self) -> None:
        return None
