"""Typed data models shared across the StaleGuard pipeline."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Category(str, Enum):
    PRICING = "pricing"
    API_VERSION = "api_version"
    REGULATION = "regulation"
    COMPANY_INFO = "company_info"
    STATISTIC = "statistic"
    PRODUCT = "product"
    STABLE = "stable"  # timeless facts, not worth re-verifying


class Verdict(str, Enum):
    CURRENT = "current"            # live sources confirm the claim
    OUTDATED = "outdated"          # newer sources show a different value
    PARTIALLY = "partially_current"  # mostly right, some detail changed
    UNVERIFIED = "unverified"      # nothing conclusive found
    SKIPPED = "skipped"            # stable claim, not checked


class Claim(BaseModel):
    id: str
    text: str = Field(description="The atomic, self-contained claim")
    span: str = Field("", description="Exact substring of the answer the claim came from")
    category: Category
    subject: str = ""            # e.g. "React", "ChatGPT Plus"
    attribute: str = ""          # e.g. "input price per 1M tokens"
    value: str = ""              # e.g. "$5.00"
    time_sensitive: bool = True
    search_query: str = ""


class Evidence(BaseModel):
    title: str
    url: str
    snippet: str
    published_date: Optional[str] = None
    relevance: float = 0.0
    stance: str = "neutral"  # supports | contradicts | neutral


class ScoreBreakdown(BaseModel):
    prior_risk: float          # how likely this category has drifted given its age
    verdict_score: float       # what the live evidence says
    evidence_recency: float    # how fresh the best evidence is (1 = this month)
    source_agreement: float    # fraction of sources that agree with the verdict
    confidence: float          # adjudicator confidence


class ClaimResult(BaseModel):
    claim: Claim
    verdict: Verdict
    freshness: int = Field(ge=0, le=100)
    breakdown: ScoreBreakdown
    explanation: str = ""
    current_value: str = ""     # corrected value if outdated
    evidence: list[Evidence] = []


class CallTrace(BaseModel):
    """One upstream call made by the pipeline (LLM via Bifrost or Tavily search)."""
    kind: str                  # llm | search
    purpose: str
    target: str                # model or query
    served_by: str = ""        # provider/model that actually answered (Bifrost routing info)
    fallback_used: bool = False
    latency_ms: int = 0
    tokens: int = 0
    cached: bool = False
    error: str = ""


class CheckRequest(BaseModel):
    answer: str = Field(description="LLM-generated answer to audit")
    question: str = ""
    knowledge_date: Optional[str] = Field(
        None, description="When the answer's knowledge is from (YYYY-MM or YYYY-MM-DD); "
                          "defaults to the model cutoff if unknown")
    max_claims: int = 12
    rewrite: bool = True


class CheckReport(BaseModel):
    question: str = ""
    answer: str
    overall_freshness: int
    grade: str
    claims: list[ClaimResult]
    refreshed_answer: str = ""
    checked_at: str
    mode: str                   # live | offline
    trace: list[CallTrace] = []
    stats: dict = {}
