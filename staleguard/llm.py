"""LLM access through the Bifrost gateway.

Every LLM call StaleGuard makes goes through Bifrost's OpenAI-compatible
endpoint. That gives us, for free:
  * provider fallbacks (`fallbacks` field) - an outage at one provider doesn't
    break verification;
  * per-tenant budgets / rate limits via virtual keys (`x-bf-vk` header);
  * observability: Bifrost reports which provider actually served the call
    (`extra_fields.routing_info`), which we surface in the UI trace.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

import httpx

from .config import Settings
from .models import CallTrace


class LLMError(RuntimeError):
    pass


def parse_json_block(text: str) -> Any:
    """Parse JSON from a model reply, tolerating ```json fences and chatter."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fall back to the first {...} or [...] block
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"model did not return valid JSON: {text[:200]!r}")


class BifrostClient:
    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None):
        self.s = settings
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.s.bifrost_virtual_key:
            h["x-bf-vk"] = self.s.bifrost_virtual_key
        return h

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str,
        purpose: str,
        trace: list[CallTrace],
        json_mode: bool = True,
        temperature: float = 0.0,
        max_tokens: int = 1500,
    ) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "fallbacks": [f for f in self.s.fallbacks if f != model],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        url = self.s.bifrost_url.rstrip("/") + "/v1/chat/completions"
        t0 = time.perf_counter()
        entry = CallTrace(kind="llm", purpose=purpose, target=model)
        try:
            r = await self._client.post(url, json=body, headers=self._headers())
            if r.status_code >= 400 and json_mode:
                # some providers reject response_format; retry once without it
                body.pop("response_format", None)
                r = await self._client.post(url, json=body, headers=self._headers())
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            entry.error = str(e)[:200]
            entry.latency_ms = int((time.perf_counter() - t0) * 1000)
            trace.append(entry)
            raise LLMError(f"Bifrost call failed ({purpose}): {e}") from e

        entry.latency_ms = int((time.perf_counter() - t0) * 1000)
        entry.tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
        extra = data.get("extra_fields") or {}
        routing = extra.get("routing_info") or {}
        # routing_info names the provider/model that actually answered (it differs from the request on fallback)
        provider = routing.get("provider") or extra.get("provider") or ""
        served_model = routing.get("model") or data.get("model") or extra.get("model_requested") or model
        entry.served_by = f"{provider}/{served_model}".strip("/") if provider else served_model
        entry.fallback_used = bool(routing.get("is_fallback"))
        entry.cached = bool((extra.get("cache_debug") or {}).get("cache_hit"))
        trace.append(entry)

        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"unexpected Bifrost response shape: {str(data)[:200]}") from e

    async def chat_json(self, messages: list[dict], **kw) -> Any:
        return parse_json_block(await self.chat(messages, json_mode=True, **kw))

    async def aclose(self) -> None:
        await self._client.aclose()
