"""Freshness scoring.

The score is deliberately explainable - every number in the UI can be traced
back to four components:

  prior_risk       How likely the claim has drifted, *before* looking at any
                   evidence. Exponential decay using a per-category half-life
                   and the age of the answer's knowledge.
  verdict_score    What live sources say (current / partially / outdated).
  evidence_recency How recent the newest relevant source is.
  source_agreement Share of non-neutral sources that agree with the verdict.

freshness = 100 * clamp(
    conf * verdict_term + (1 - conf) * (1 - prior_risk)
)
where verdict_term blends the verdict with recency and agreement. With no
evidence (conf low), the score falls back towards the prior.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from .models import Category, ClaimResult, Evidence, ScoreBreakdown, Verdict

# Days after which there's a 50% chance a fact in this category has changed.
HALF_LIFE_DAYS = {
    Category.PRICING: 120,
    Category.API_VERSION: 180,
    Category.COMPANY_INFO: 240,
    Category.PRODUCT: 240,
    Category.STATISTIC: 365,
    Category.REGULATION: 540,
    Category.STABLE: 36500,
}

_VERDICT_BASE = {
    Verdict.CURRENT: 1.0,
    Verdict.PARTIALLY: 0.5,
    Verdict.OUTDATED: 0.05,
}


def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def prior_risk(category: Category, knowledge_date: Optional[str], today: Optional[date] = None) -> float:
    today = today or date.today()
    kd = parse_date(knowledge_date) or today
    age = max(0, (today - kd).days)
    half = HALF_LIFE_DAYS[category]
    return round(1 - 0.5 ** (age / half), 3)


def recency(evidence: list[Evidence], today: Optional[date] = None) -> float:
    today = today or date.today()
    relevant = [e for e in evidence if e.stance != "neutral"] or evidence
    dates = [d for d in (parse_date(e.published_date) for e in relevant) if d]
    if not dates:
        return 0.5  # undated sources (docs pages) - neutral
    age = max(0, (today - max(dates)).days)
    return round(0.5 ** (age / 180), 3)  # 1.0 this month, 0.5 after ~6 months


def agreement(evidence: list[Evidence], verdict: Verdict) -> float:
    decided = [e for e in evidence if e.stance in ("supports", "contradicts")]
    if not decided:
        return 0.0
    want = "contradicts" if verdict == Verdict.OUTDATED else "supports"
    return round(sum(e.stance == want for e in decided) / len(decided), 3)


def score_claim(
    verdict: Verdict,
    category: Category,
    evidence: list[Evidence],
    confidence: float,
    knowledge_date: Optional[str],
    today: Optional[date] = None,
) -> tuple[int, ScoreBreakdown]:
    pr = 0.0 if category == Category.STABLE else prior_risk(category, knowledge_date, today)
    rec = recency(evidence, today)
    agr = agreement(evidence, verdict)
    conf = max(0.0, min(1.0, confidence))

    if verdict == Verdict.SKIPPED:
        value = 1.0
    elif verdict == Verdict.UNVERIFIED:
        # Can't confirm != wrong: floor at 20, then lean on the age-based prior.
        value = 0.2 + 0.5 * (1 - pr)
    else:
        base = _VERDICT_BASE[verdict]
        if verdict == Verdict.CURRENT:
            # Confirmed by live sources: the age-based prior no longer matters much.
            # Fresh, agreeing, high-confidence evidence -> near 100.
            term = base * (0.7 + 0.15 * rec + 0.15 * agr)
            value = term * (0.6 + 0.4 * conf)
        elif verdict == Verdict.OUTDATED:
            # More agreement on "outdated" -> lower score; low confidence leans on the prior.
            term = base + 0.15 * (1 - agr)
            value = conf * term + (1 - conf) * (1 - pr) * 0.5
        else:  # partially current
            value = base * (0.6 + 0.4 * conf) + (1 - conf) * (1 - pr) * 0.2

    value = max(0.0, min(1.0, value))
    return round(value * 100), ScoreBreakdown(
        prior_risk=pr, verdict_score=_VERDICT_BASE.get(verdict, 0.5 if verdict == Verdict.UNVERIFIED else 1.0),
        evidence_recency=rec, source_agreement=agr, confidence=round(conf, 3),
    )


def overall(results: list[ClaimResult]) -> int:
    """Answer-level score: a mean that is dragged down hard by any stale claim.

    One wrong price makes an answer unsafe to act on, so we blend the mean with
    the minimum instead of reporting a plain average.
    """
    checked = [r for r in results if r.verdict != Verdict.SKIPPED]
    if not checked:
        return 100
    scores = [r.freshness for r in checked]
    mean = sum(scores) / len(scores)
    return round(0.6 * mean + 0.4 * min(scores))


def grade(score: int) -> str:
    if score >= 85:
        return "Fresh"
    if score >= 65:
        return "Mostly fresh"
    if score >= 40:
        return "Needs review"
    return "Stale"
