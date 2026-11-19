"""The StaleGuard pipeline: extract -> re-verify -> score -> rewrite.

    answer ──► claim extraction (LLM via Bifrost, cheap model; heuristic fallback)
           ──► per time-sensitive claim, in parallel:
                 Tavily live search ──► adjudication (LLM via Bifrost, strong model)
           ──► freshness score per claim + answer-level score
           ──► refreshed answer with corrections + citations
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timezone
from typing import Optional

from . import heuristics, prompts, scoring
from .config import Settings, settings as default_settings
from .llm import BifrostClient, LLMError
from .models import (CallTrace, Category, CheckReport, CheckRequest, Claim, ClaimResult, Evidence,
                     ScoreBreakdown, Verdict)
from .offline import SnapshotSearch
from .search import TavilyClient


class StaleGuard:
    def __init__(self, settings: Optional[Settings] = None, llm=None, search=None):
        self.s = settings or default_settings
        self.mode = self.s.resolved_mode()
        if self.mode == "live":
            self.llm = llm or BifrostClient(self.s)
            self.search = search or TavilyClient(self.s)
        else:
            self.llm = llm  # None -> heuristics only
            self.search = search or SnapshotSearch()

    # ------------------------------------------------------------ extract --
    async def extract(self, req: CheckRequest, trace: list[CallTrace]) -> list[Claim]:
        fallback = heuristics.heuristic_claims(req.answer, req.max_claims)
        if not self.llm:
            return fallback
        risky = [c.text for c in fallback if heuristics.risk_score(c.text) >= 0.5]
        try:
            data = await self.llm.chat_json(
                [
                    {"role": "system", "content": prompts.EXTRACT_SYSTEM.replace("{max_claims}", str(req.max_claims))},
                    {"role": "user", "content": prompts.EXTRACT_USER.format(
                        question=req.question or "-", risky=json.dumps(risky), answer=req.answer)},
                ],
                model=self.s.extract_model, purpose="extract_claims", trace=trace,
            )
            raw = data.get("claims", []) if isinstance(data, dict) else data
            claims = []
            for i, c in enumerate(raw[: req.max_claims]):
                try:
                    cat = Category(str(c.get("category", "stable")).lower())
                except ValueError:
                    cat = heuristics.classify(c.get("text", ""))
                text = c.get("text") or c.get("span") or ""
                if not text:
                    continue
                claims.append(Claim(
                    id=f"c{i + 1}", text=text, span=c.get("span") or text, category=cat,
                    subject=c.get("subject", ""), attribute=c.get("attribute", ""), value=str(c.get("value", "")),
                    time_sensitive=bool(c.get("time_sensitive", cat != Category.STABLE)) and cat != Category.STABLE,
                    search_query=c.get("search_query") or heuristics.build_query(text, cat),
                ))
            return claims or fallback
        except LLMError:
            return fallback

    # ------------------------------------------------------------- verify --
    async def _judge_llm(self, claim: Claim, evidence: list[Evidence], trace: list[CallTrace]):
        listing = "\n".join(
            f"[{i + 1}] {e.title} ({e.published_date or 'undated'}) {e.url}\n{e.snippet[:700]}"
            for i, e in enumerate(evidence))
        data = await self.llm.chat_json(
            [
                {"role": "system", "content": prompts.JUDGE_SYSTEM.format(today=date.today().isoformat())},
                {"role": "user", "content": prompts.JUDGE_USER.format(
                    claim=claim.text, category=claim.category.value, subject=claim.subject or "-",
                    attribute=claim.attribute or "-", value=claim.value or "-", results=listing)},
            ],
            model=self.s.judge_model, purpose=f"judge:{claim.id}", trace=trace,
        )
        try:
            verdict = Verdict(str(data.get("verdict", "unverified")).lower())
        except ValueError:
            verdict = Verdict.UNVERIFIED
        if verdict == Verdict.SKIPPED:
            verdict = Verdict.UNVERIFIED
        for k, v in (data.get("stances") or {}).items():
            try:
                evidence[int(k) - 1].stance = v if v in ("supports", "contradicts", "neutral") else "neutral"
            except (ValueError, IndexError):
                pass
        conf = float(data.get("confidence", 0.5) or 0.5)
        return verdict, str(data.get("current_value", "") or ""), conf, str(data.get("explanation", ""))

    async def verify(self, claim: Claim, knowledge_date: str, trace: list[CallTrace], sem: asyncio.Semaphore) -> ClaimResult:
        if not claim.time_sensitive or claim.category == Category.STABLE:
            fresh, br = scoring.score_claim(Verdict.SKIPPED, claim.category, [], 1.0, knowledge_date)
            return ClaimResult(claim=claim, verdict=Verdict.SKIPPED, freshness=fresh, breakdown=br,
                               explanation="Timeless fact - not re-verified.")
        async with sem:
            raw = await self.search.search(claim.search_query or claim.text, claim.category, trace)
            evidence = [Evidence(title=r.get("title", ""), url=r.get("url", ""),
                                 snippet=(r.get("content") or "")[:1200],
                                 published_date=r.get("published_date"), relevance=float(r.get("score") or 0))
                        for r in raw if r.get("url")]
            verdict = None
            if self.llm and evidence:
                try:
                    verdict, current, conf, expl = await self._judge_llm(claim, evidence, trace)
                except LLMError:
                    verdict = None
            if verdict is None:
                verdict, current, conf, expl = heuristics.heuristic_judge(claim, evidence)
        fresh, br = scoring.score_claim(verdict, claim.category, evidence, conf, knowledge_date)
        return ClaimResult(claim=claim, verdict=verdict, freshness=fresh, breakdown=br,
                           explanation=expl, current_value=current, evidence=evidence)

    # ------------------------------------------------------------ rewrite --
    async def rewrite(self, req: CheckRequest, results: list[ClaimResult], trace: list[CallTrace]) -> str:
        bad = [r for r in results if r.verdict in (Verdict.OUTDATED, Verdict.PARTIALLY)]
        if not bad:
            return req.answer
        sources: list[str] = []

        def cite(r: ClaimResult) -> str:
            ev = next((e for e in r.evidence if e.stance == "contradicts"), r.evidence[0] if r.evidence else None)
            if not ev:
                return ""
            if ev.url not in sources:
                sources.append(ev.url)
            return f" [{sources.index(ev.url) + 1}]"

        if self.llm:
            findings = "\n".join(
                f"- Claim: {r.claim.text} -> {r.verdict.value}; current value: {r.current_value or 'n/a'}; "
                f"{r.explanation}{cite(r)}" for r in results if r.verdict != Verdict.SKIPPED)
            try:
                data = await self.llm.chat_json(
                    [{"role": "system", "content": prompts.REWRITE_SYSTEM},
                     {"role": "user", "content": prompts.REWRITE_USER.format(
                         answer=req.answer, findings=findings,
                         sources="\n".join(f"[{i + 1}] {u}" for i, u in enumerate(sources)))}],
                    model=self.s.extract_model, purpose="rewrite", trace=trace, max_tokens=2000)
                text = data.get("answer", "") if isinstance(data, dict) else ""
                if text:
                    return text + ("\n\nSources:\n" + "\n".join(f"[{i + 1}] {u}" for i, u in enumerate(sources)))
            except LLMError:
                pass
        # deterministic rewrite: annotate stale spans in place
        text = req.answer
        for r in bad:
            span = r.claim.span or r.claim.text
            ref = cite(r).strip(" []")
            label = "outdated" if r.verdict == Verdict.OUTDATED else "partly outdated"
            if r.current_value and len(r.current_value) < 40:
                tag = f" [⚠ {label}: now {r.current_value}" + (f", see [{ref}]]" if ref else "]")
            else:
                tag = f" [⚠ {label}" + (f", see [{ref}]]" if ref else "]")
            if span in text:
                text = text.replace(span, span.rstrip(".") + tag + ("." if span.endswith(".") else ""), 1)
        if sources:
            text += "\n\nSources:\n" + "\n".join(f"[{i + 1}] {u}" for i, u in enumerate(sources))
        return text

    # ---------------------------------------------------------------- run --
    async def check(self, req: CheckRequest) -> CheckReport:
        t0 = time.perf_counter()
        trace: list[CallTrace] = []
        kd = req.knowledge_date or self.s.default_knowledge_date
        claims = await self.extract(req, trace)
        sem = asyncio.Semaphore(self.s.concurrency)
        results = list(await asyncio.gather(*(self.verify(c, kd, trace, sem) for c in claims)))
        refreshed = await self.rewrite(req, results, trace) if req.rewrite else ""
        score = scoring.overall(results)
        counts = {v.value: sum(r.verdict == v for r in results) for v in Verdict}
        return CheckReport(
            question=req.question, answer=req.answer, overall_freshness=score, grade=scoring.grade(score),
            claims=results, refreshed_answer=refreshed,
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            mode=self.mode, trace=trace,
            stats={
                "claims": len(results), **counts,
                "llm_calls": sum(t.kind == "llm" for t in trace),
                "searches": sum(t.kind == "search" for t in trace),
                "fallbacks_used": sum(t.fallback_used for t in trace),
                "tokens": sum(t.tokens for t in trace),
                "knowledge_date": kd,
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            },
        )

    async def aclose(self):
        for c in (self.llm, self.search):
            if c is not None and hasattr(c, "aclose"):
                await c.aclose()
