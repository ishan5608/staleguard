"""Deterministic, model-free building blocks.

Used three ways:
  1. offline mode (no API keys) - the whole pipeline runs on these;
  2. a safety net when an LLM call fails even after Bifrost fallbacks;
  3. a pre-pass that tells the LLM extractor which sentences look risky.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from .models import Category, Claim, Evidence, Verdict

_PATTERNS: list[tuple[Category, re.Pattern]] = [
    (Category.PRICING, re.compile(
        r"([$€£₹]\s?\d|\d+\s?(usd|dollars|eur|inr)\b|\bper (month|year|seat|user|token|1m|million|request)\b"
        r"|\bpric(e|es|ing)\b|\bcosts?\b|\bfree tier\b|\bsubscription\b|/mo\b|/month\b)", re.I)),
    (Category.API_VERSION, re.compile(
        r"(\bv\d+(\.\d+)*\b|\bversion\s+\d|\b(latest|current|newest|stable)\s+(release|version|major)\b"
        r"|\b(lts|sdk|api|deprecated|end[- ]of[- ]life|eol)\b|\b(python|node(\.js)?|react|java|go|rust|django|"
        r"next\.js|typescript|kubernetes|ios|android)\s+\d+(\.\d+)*)", re.I)),
    (Category.REGULATION, re.compile(
        r"\b(act|regulation|law|gdpr|ccpa|dpdp|hipaa|directive|bill|compliance|mandat\w*|regulator\w*|"
        r"legislation|ban(ned)?|tax rate|statute|enforce\w*|negotiat\w*|adopted)\b", re.I)),
    (Category.COMPANY_INFO, re.compile(
        r"\b(ceo|cto|cfo|founder|co-founder|chair\w*|president|headquarter\w*|employees|headcount|acquir\w*|"
        r"valuation|valued at|raised|funding|series [a-f]|revenue|market cap|ipo|layoffs?|merged)\b", re.I)),
    (Category.STATISTIC, re.compile(
        r"(\d+(\.\d+)?\s?%|\b\d[\d,.]*\s?(million|billion|crore|lakh)\b|\bmonthly active users\b|\bmarket share\b)", re.I)),
    (Category.PRODUCT, re.compile(
        r"\b(launch\w*|releas\w*|supports?|available|feature|roadmap|beta|preview|discontinu\w*|"
        r"(is|are) (the )?(latest|newest|most recent)|renamed|rebrand\w*|branded|called)\b", re.I)),
]

_TEMPORAL = re.compile(r"\b(currently|now|today|latest|newest|recent(ly)?|as of|this year|still|current)\b", re.I)

_MONEY = re.compile(r"[$€£₹]\s?\d[\d,]*(\.\d+)?(\s?(k|m|b|million|billion))?", re.I)
_VERSION = re.compile(r"\b(?:v)?(\d+)(?:\.(\d+))?(?:\.(\d+))?\b")
_PCT = re.compile(r"\d+(\.\d+)?\s?%")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    # split on sentence enders and on list bullets
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\s+[-*•]\s+|\s+\d+\)\s+", text)
    return [p.strip(" -*•") for p in parts if len(p.strip()) > 12]


_HISTORICAL = re.compile(r"\b(was|were) (originally |first )?(created|invented|founded|born|designed|first released)\b", re.I)


def classify(sentence: str) -> Category:
    if _HISTORICAL.search(sentence) and not _TEMPORAL.search(sentence):
        return Category.STABLE
    scores: Counter = Counter()
    for cat, pat in _PATTERNS:
        hits = len(pat.findall(sentence))
        if hits:
            scores[cat] += hits
    if not scores:
        # "X is still Y" / "currently" - time-bound even without domain vocabulary
        return Category.PRODUCT if _TEMPORAL.search(sentence) else Category.STABLE
    # ties broken by list order (pricing > version > regulation > company > stat > product)
    order = [c for c, _ in _PATTERNS]
    return max(scores, key=lambda c: (scores[c], -order.index(c)))


def risk_score(sentence: str) -> float:
    """0..1 - how likely the sentence carries time-sensitive information."""
    cat = classify(sentence)
    base = {Category.STABLE: 0.05, Category.PRODUCT: 0.45, Category.STATISTIC: 0.5,
            Category.REGULATION: 0.6, Category.COMPANY_INFO: 0.7, Category.API_VERSION: 0.75,
            Category.PRICING: 0.85}[cat]
    if _TEMPORAL.search(sentence):
        base = min(1.0, base + 0.15)
    return round(base, 2)


def key_value(sentence: str, category: Category) -> str:
    if category == Category.PRICING:
        m = _MONEY.search(sentence)
        if m:
            return m.group(0).replace(" ", "")
    if category == Category.API_VERSION:
        m = re.search(r"\b(?:v|version\s+)?(\d+(?:\.\d+){0,2})\b(?!\s?%)", sentence, re.I)
        if m and not _YEAR.fullmatch(m.group(1)):
            return m.group(1)
    if category == Category.STATISTIC:
        m = _PCT.search(sentence)
        if m:
            return m.group(0)
    if category == Category.COMPANY_INFO:
        m = re.search(r"\b(?:is|was|are)\s+((?:[A-Z][a-z]+\s?){2,3})", sentence)
        if m:
            return m.group(1).strip()
        m = re.match(r"\s*((?:[A-Z][a-z]+\s){1,2}[A-Z][a-z]+)\s+(?:is|was|remains)\b", sentence)
        if m:
            return m.group(1).strip()
    m = _MONEY.search(sentence) or _PCT.search(sentence)
    return m.group(0) if m else ""


def _subject(sentence: str) -> str:
    caps = re.findall(r"\b([A-Z][\w.+#-]*(?:\s+[A-Z][\w.+#-]*)*)", sentence)
    skip = {"the", "a", "an", "it", "this", "as", "in", "for", "if", "on", "at", "to", "use", "note", "with",
            "when", "while", "since", "most", "many", "all", "each", "you", "your", "we", "our", "they"}
    caps = [c for c in caps if c.split()[0].lower() not in skip]
    return caps[0] if caps else " ".join(sentence.split()[:4])


def heuristic_claims(answer: str, max_claims: int = 8) -> list[Claim]:
    claims: list[Claim] = []
    for i, sent in enumerate(split_sentences(answer)):
        cat = classify(sent)
        claims.append(Claim(
            id=f"c{i + 1}",
            text=sent,
            span=sent,
            category=cat,
            subject=_subject(sent),
            value=key_value(sent, cat),
            time_sensitive=cat != Category.STABLE,
            search_query=build_query(sent, cat),
        ))
    # keep the riskiest ones, preserve answer order
    ranked = sorted(claims, key=lambda c: -risk_score(c.text))
    keep = {c.id for c in ranked[:max_claims]}
    return [c for c in claims if c.id in keep]


def build_query(sentence: str, category: Category) -> str:
    q = re.sub(r"\b(currently|now|today|as of \w+ \d{4}|as of \d{4}|still)\b", "", sentence, flags=re.I)
    q = re.sub(r"[$€£₹]?\d[\d,.]*\s?%?", "", q) if category in (Category.PRICING, Category.STATISTIC) else q
    q = re.sub(r"\s+", " ", q).strip(" .")
    suffix = {
        Category.PRICING: "current pricing",
        Category.API_VERSION: "latest version release",
        Category.REGULATION: "current status",
        Category.COMPANY_INFO: "latest news",
        Category.STATISTIC: "latest figures",
        Category.PRODUCT: "latest",
    }.get(category, "")
    return f"{q} {suffix}".strip()[:300]


# ---------------------------------------------------------------- judging --

def _versions(text: str) -> list[tuple[int, ...]]:
    out = []
    for m in _VERSION.finditer(text):
        nums = tuple(int(g) for g in m.groups() if g is not None)
        if len(nums) == 1 and (nums[0] >= 1900 or nums[0] == 0):
            continue  # years and zeros aren't versions
        out.append(nums)
    return out


def _norm_money(s: str) -> str:
    s = re.sub(r"[\s,]", "", s.lower())
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _keywords(claim: Claim) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9.+#-]{2,}", (claim.subject + " " + claim.text).lower())
    stop = {"the", "and", "for", "with", "that", "this", "are", "was", "has", "have", "its", "per", "from",
            "latest", "current", "currently", "version", "release", "stable", "which", "now"}
    return {w for w in words if w not in stop}


def stance_of(claim: Claim, ev_text: str) -> tuple[str, str]:
    """Return (stance, competing_value) of one evidence snippet toward a claim."""
    low = ev_text.lower()
    subj = [w for w in re.findall(r"[a-z0-9.+#-]{2,}", claim.subject.lower())]
    subject_hit = bool(subj) and all(re.search(r"\b" + re.escape(w) + r"\b", low) for w in subj)
    kw = _keywords(claim)
    overlap = sum(1 for w in kw if w in low)
    if not subject_hit and kw and overlap / len(kw) < 0.25:
        return "neutral", ""

    if claim.category == Category.API_VERSION and claim.value:
        claimed = _versions(claim.value)
        anchor = re.search(r"([A-Za-z][\w.+#-]*)\s+v?" + re.escape(claim.value) + r"\b", claim.text)
        if anchor:
            pat = re.escape(anchor.group(1)) + r"\s+v?(\d+(?:\.\d+){0,2})\b"
            found = [tuple(int(x) for x in m.group(1).split(".")) for m in re.finditer(pat, ev_text, re.I)]
        else:
            found = _versions(ev_text)
        if claimed and found:
            c = claimed[0]
            same_major = [v for v in found if v[0] == c[0]]
            newer = [v for v in found if v[: len(c)] > c and (len(c) > 1 and v[0] == c[0] or v[0] > c[0])]
            if newer and re.search(r"\b(latest|new|released|now|stable|current|introduc|end[- ]of[- ]life|eol)", low):
                best = max(newer)
                return "contradicts", ".".join(map(str, best[: max(len(c), 2) if best[0] == c[0] else len(c)]))
            if same_major and not newer:
                return "supports", ""

    if claim.category == Category.PRICING and claim.value:
        claimed = _norm_money(claim.value)
        found = [_norm_money(m.group(0)) for m in _MONEY.finditer(ev_text)]
        if claimed in found:
            return "supports", ""
        if found:
            return "contradicts", _MONEY.search(ev_text).group(0)

    if claim.value and claim.value.lower() in low:
        return "supports", ""

    neg = re.search(r"\b(no longer|discontinued|deprecated|replaced|end[- ]of[- ]life|entered into force|"
                    r"was adopted|has been adopted|stepped down|resigned|succeeded by|was passed|has been passed|"
                    r"received assent|was enacted|has been enacted|signed into law|renamed|rebranded)\b", low)
    claim_low = claim.text.lower()
    if neg and not re.search(re.escape(neg.group(0)), claim_low):
        if re.search(r"\b(not|still|yet|being negotiated|is the active|is the current)\b", claim_low):
            sent = next((x for x in re.split(r"(?<=[.!?])\s+", ev_text) if neg.group(0) in x.lower()), "")
            return "contradicts", sent.strip()[:400]
    return "neutral", ""


def heuristic_judge(claim: Claim, evidence: list[Evidence]) -> tuple[Verdict, str, float, str]:
    """Return (verdict, current_value, confidence, explanation)."""
    if not evidence:
        return Verdict.UNVERIFIED, "", 0.3, "No live sources found for this claim."
    tally: Counter = Counter()
    values: Counter = Counter()
    for ev in evidence:
        stance, val = stance_of(claim, f"{ev.title}. {ev.snippet}")
        ev.stance = stance
        tally[stance] += 1
        if val:
            values[val] += 1
    sup, con = tally["supports"], tally["contradicts"]
    current = values.most_common(1)[0][0] if values else ""
    if con and con >= sup:
        conf = min(0.9, 0.5 + 0.15 * con)
        msg = f"{con} source(s) contradict this claim"
        if current and len(current) <= 40:
            msg += f"; they point to {current}."
        elif current:
            msg += "; see the up-to-date statement above."
        else:
            msg += "."
        return Verdict.OUTDATED, current, conf, msg
    if sup and con:
        return Verdict.PARTIALLY, current, 0.55, f"Mixed evidence: {sup} supporting vs {con} contradicting."
    if sup:
        return Verdict.CURRENT, "", min(0.9, 0.5 + 0.15 * sup), f"{sup} recent source(s) confirm this claim."
    return Verdict.UNVERIFIED, "", 0.35, "Sources found, but none clearly confirm or refute the claim."


def best_published(evidence: list[Evidence]) -> Optional[str]:
    dates = sorted((e.published_date for e in evidence if e.published_date), reverse=True)
    return dates[0] if dates else None
