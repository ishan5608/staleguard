"""HTTP API + web UI.

Endpoints
  GET  /                      web UI
  GET  /api/health            mode, gateway reachability
  GET  /api/demo              the bundled demo answer
  POST /api/check             audit an answer -> CheckReport
  POST /v1/chat/completions   OpenAI-compatible *guarded* completion: forwards the
                              request to Bifrost, audits the reply, and returns the
                              normal completion plus a `staleguard` report. Point any
                              OpenAI SDK at StaleGuard to add freshness checks with a
                              one-line base_url change.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .config import settings
from .models import CheckReport, CheckRequest
from .pipeline import StaleGuard

ROOT = Path(__file__).parent
guard = StaleGuard(settings)


@asynccontextmanager
async def lifespan(_app):
    yield
    await guard.aclose()


app = FastAPI(title="StaleGuard", version="0.1.0", lifespan=lifespan,
              description="Detect and fix stale claims in LLM answers using Tavily + Bifrost.")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
async def health():
    gateway = "not used (offline mode)"
    if guard.mode == "live":
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(settings.bifrost_url.rstrip("/") + "/health")
                gateway = "up" if r.status_code < 500 else f"status {r.status_code}"
        except httpx.HTTPError:
            gateway = "unreachable"
    return {"mode": guard.mode, "bifrost": gateway, "bifrost_url": settings.bifrost_url,
            "extract_model": settings.extract_model, "judge_model": settings.judge_model,
            "fallbacks": settings.fallbacks, "tavily": bool(settings.tavily_api_key)}


@app.get("/api/demo")
async def demo():
    return {"question": "What stack should I use for a new web project in 2026?",
            "answer": (ROOT.parent / "examples" / "demo_answer.txt").read_text().strip()
            if (ROOT.parent / "examples" / "demo_answer.txt").exists() else ""}


@app.post("/api/check", response_model=CheckReport)
async def check(req: CheckRequest):
    if not req.answer.strip():
        raise HTTPException(400, "answer is empty")
    return await guard.check(req)


@app.post("/v1/chat/completions")
async def guarded_completion(request: Request):
    if guard.mode != "live":
        raise HTTPException(503, "Guarded completions need live mode (set TAVILY_API_KEY and run Bifrost).")
    body: dict[str, Any] = await request.json()
    opts = body.pop("staleguard", {}) or {}
    if body.get("stream"):
        raise HTTPException(400, "streaming is not supported by the guard endpoint")
    headers = {"Content-Type": "application/json"}
    for h in ("x-bf-vk", "authorization"):
        if h in request.headers:
            headers[h] = request.headers[h]
    async with httpx.AsyncClient(timeout=settings.request_timeout) as c:
        r = await c.post(settings.bifrost_url.rstrip("/") + "/v1/chat/completions", json=body, headers=headers)
    if r.status_code >= 400:
        return JSONResponse(r.json() if "json" in r.headers.get("content-type", "") else {"error": r.text},
                            status_code=r.status_code)
    data = r.json()
    answer = data["choices"][0]["message"].get("content") or ""
    question = next((m.get("content", "") for m in reversed(body.get("messages", []))
                     if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
    report = await guard.check(CheckRequest(answer=answer, question=question,
                                            knowledge_date=opts.get("knowledge_date"),
                                            rewrite=bool(opts.get("rewrite", True))))
    if opts.get("replace_with_refreshed") and report.refreshed_answer:
        data["choices"][0]["message"]["content"] = report.refreshed_answer
    data["staleguard"] = report.model_dump(mode="json", exclude={"answer"})
    return JSONResponse(data, headers={"X-StaleGuard-Freshness": str(report.overall_freshness),
                                       "X-StaleGuard-Grade": report.grade})

