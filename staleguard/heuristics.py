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

_MONEY = re.compile(r"[$€£₹]\s?\d[\d,]*(\.\d+)?(\s?(k|m|b|million|billion)\b)?", re.I)
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


_SUBJ_SKIP = {"the", "a", "an", "it", "this", "as", "in", "for", "if", "on", "at", "to", "use", "note", "with",
              "when", "while", "since", "most", "many", "all", "each", "you", "your", "we", "our", "they", "by", "from"}
_SUBJ_PREP = {"for", "in", "on", "at", "to", "with", "by", "from", "if", "when", "while", "since", "as", "use", "note"}
_SUBJ_VERB = re.compile(r"^(?:\s+v?\d[\d.]*)?(?:'s)?\s+(?:is|are|was|were|costs?|has|have|remains|became|launched|"
                        r"supports|offers|charges|includes|runs|requires)\b", re.I)


def _subject(sentence: str) -> str:
    """Best guess at what a sentence is about.

    Prefers the capitalised phrase that is the grammatical subject ("the EU AI Act *is*", "GitHub Copilot
    Individual *costs*", "React 18 *is*"); otherwise the first named thing that isn't introduced by a
    preposition ("*For* AI pair programming" is not the subject).
    """
    cands = []
    for m in re.finditer(r"\b([A-Z][\w.+#-]*(?:'s)?(?:\s+[A-Z][\w.+#-]*)*)", sentence):
        words = [re.sub(r"'s$", "", w) for w in m.group(1).split()]
        lead = words[0].lower()
        while words and words[0].lower() in _SUBJ_SKIP:
            words.pop(0)
        if not words:
            continue
        phrase = " ".join(words)
        cands.append((phrase, bool(_SUBJ_VERB.match(sentence[m.end():])), lead in _SUBJ_PREP and len(m.group(1).split()) > len(words)))
    for phrase, is_subject, _ in cands:
        if is_subject:
            return phrase
    for phrase, _, after_prep in cands:
        if not after_prep:
            return phrase
    return cands[0][0] if cands else " ".join(sentence.split()[:4])


_LABEL = re.compile(r"^\s*(?:[-*•]\s*)?([A-Z][\w .+&/-]{0,40}?)\s*(\([^)]*\))?\s*:\s+")
_GENERIC_LABELS = {"overall trend", "note", "summary", "overall", "tip", "flagship models", "models", "pricing",
                   "in short", "bottom line", "tl;dr", "example"}


_SECTION_WORDS = {
    "languages", "language", "frameworks", "framework", "subscriptions", "subscription", "regulation", "regulations",
    "pricing", "prices", "plans", "tools", "tooling", "models", "overview", "summary", "background", "notes",
    "conclusion", "introduction", "setup", "installation", "requirements", "features", "products", "companies",
    "news", "laws", "legal", "compliance", "security", "hardware", "software", "apps", "services", "frontend",
    "backend", "databases", "cloud", "recommendations", "tips", "options", "comparison", "details", "key", "facts",
    "and", "&", "the", "of", "other", "general", "misc", "resources", "updates", "policy", "policies", "people",
    "leadership", "runtime", "runtimes", "libraries", "stack", "infrastructure", "ai", "web",
}


def _heading(line: str) -> Optional[str]:
    """Classify a line as a section heading.

    Returns None for ordinary lines. For a heading (short, no sentence punctuation) returns the context
    it sets for the lines under it: a named product or company ("OpenAI (ChatGPT)", "## Google") is
    kept; a generic section title ("Subscriptions", "Languages and frameworks") returns "" so it
    resets the context instead of polluting every search query below it.
    """
    t = line.strip().strip("#*_ ").rstrip(":").strip()
    if not t or len(t.split()) > 6 or re.search(r"[.!?]$", t) or _MONEY.search(t):
        return None
    words = [w for w in re.findall(r"[A-Za-z][\w.+&-]*", t)]
    if words and all(w.lower() in _SECTION_WORDS for w in words):
        return ""
    return re.sub(r"[()]", " ", t).strip()


_META = re.compile(r"^\s*(here'?s|here is|here are|below (is|are)|in summary|to summarize|overall,|hope this|"
                   r"i hope|let me know|sure[,!]|great question|feel free|note that this|keep in mind that "
                   r"(prices|details) (may|can))", re.I)


def _words(t: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9.+#-]*", t.lower())


def heuristic_claims(answer: str, max_claims: int = 8) -> list[Claim]:
    claims: list[Claim] = []
    context = ""
    for line in answer.splitlines():
        if not line.strip():
            continue
        h = _heading(line)
        if h is not None:
            context = re.sub(r"\s+", " ", h)
            continue
        label, line_ctx = "", context
        m = _LABEL.match(line)
        if m:
            lab = m.group(1).strip()
            if lab.lower() in {"overall trend", "overall", "summary", "in short", "bottom line", "tl;dr", "note"}:
                line_ctx = ""                                   # wrap-up lines aren't about the last section
            elif lab.lower() not in _GENERIC_LABELS:
                label = lab
        for sent in split_sentences(line):
            if sent not in answer or _META.match(sent):   # exact spans only; skip "Here's a quick guide..."
                continue
            cat = classify(sent)
            if label:
                lw = set(_words(label))
                subject = " ".join([w for w in line_ctx.split() if w.lower() not in lw] + [label])
            elif line_ctx and not set(_words(line_ctx)) & set(_words(sent)):
                subject = f"{line_ctx} {_subject(sent)}"
            else:
                subject = _subject(sent)
            claims.append(Claim(
                id=f"c{len(claims) + 1}",
                text=sent,
                span=sent,
                category=cat,
                subject=subject,
                attribute="tier" if label else "",
                value=key_value(sent, cat),
                time_sensitive=cat != Category.STABLE,
                search_query=build_query(sent, cat, subject if (line_ctx or label) else ""),
            ))
    # keep the riskiest ones, preserve answer order
    ranked = sorted(claims, key=lambda c: -risk_score(c.text))
    keep = {c.id for c in ranked[:max_claims]}
    return [c for c in claims if c.id in keep]


_PRICE_FULL = re.compile(r"[$€£₹]\s?\d[\d,.]*(\s?(k|m|b|million|billion))?(\s?(/|per\s)\s?(month|mo|year|yr|user|seat))?", re.I)


def build_query(sentence: str, category: Category, context: str = "") -> str:
    q = re.sub(r"\b(currently|now|today|as of \w+ \d{4}|as of \d{4}|still)\b", "", sentence, flags=re.I)
    if category in (Category.PRICING, Category.STATISTIC):
        # don't put the asserted value in the query - it biases results toward confirming it
        q = _PRICE_FULL.sub("", q)
        q = re.sub(r"\d+(\.\d+)?\s?%", "", q)
    q = re.sub(r"\(\s*[/,;]?\s*\)", "", q)            # leftover "()" / "(/month)"
    q = re.sub(r"\s+", " ", q).strip(" .:")
    if context:
        missing = [w for w in context.split() if w.lower() not in q.lower()]
        if missing:
            q = " ".join(missing) + " " + q
        if category == Category.PRICING:
            q = q.split(":")[0] if len(q.split(":")[0].split()) >= 2 else q   # "OpenAI ChatGPT Plus: ..." -> name only
    q = re.sub(r"\s+", " ", q).strip()
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


_NEG = re.compile(r"\b(no longer|discontinued|deprecated|replaced|end[- ]of[- ]life|entered into force|"
                  r"was adopted|has been adopted|stepped down|resigned|succeeded by|was passed|has been passed|"
                  r"received assent|was enacted|has been enacted|signed into law|renamed|rebranded)\b")
_GENERIC_SUBJ = {"the", "and", "of", "a", "an", "its", "it", "this"}


def _clean_statement(sent: str) -> str:
    """Tidy a sentence lifted from a web page: markdown, "X on Instagram:" prefixes, stray quotes."""
    t = re.sub(r"[#*_`>]+", " ", sent)
    t = re.sub(r"^.{0,60}?\b(on|via) (instagram|facebook|x|twitter|tiktok|linkedin|threads|youtube)\s*:\s*", "", t, flags=re.I)
    t = re.sub(r"^[^A-Za-z0-9]+", "", t)
    t = re.sub(r"\s+", " ", t).strip().strip('"\u201c\u201d ')
    return t[0].upper() + t[1:] if t else t


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
            # a page that merely mentions "React 18" doesn't confirm it's still the current version
            if same_major and not newer and re.search(r"\b(latest|current(ly)?|newest|stable|active lts|most recent)\b", low):
                return "supports", ""
            if same_major and not newer:
                return "neutral", ""

    if claim.category == Category.PRICING and claim.value:
        return _price_stance(claim, ev_text)

    if claim.value and claim.value.lower() in low:
        return "supports", ""

    neg = _NEG.search(low)
    claim_low = claim.text.lower()
    if neg and not re.search(re.escape(neg.group(0)), claim_low):
        if re.search(r"\b(not|still|yet|being negotiated|is the active|is the current)\b", claim_low):
            # the status change must be about *this* subject: "The EU AI Act entered into force..." counts,
            # an unrelated "It entered into force on ..." from the same page does not
            key = [w for w in _words(claim.subject) if w not in _GENERIC_SUBJ] or _words(claim.subject)
            for sent in re.split(r"(?<=[.!?])\s+|\n+", ev_text):
                sl = sent.lower()
                if _NEG.search(sl) and key and all(re.search(r"\b" + re.escape(w) + r"\b", sl) for w in key):
                    return "contradicts", _clean_statement(sent)[:400]
    return "neutral", ""


_TIERS = "free|go|plus|pro|ultra|premium|advanced|max|team|business|enterprise|basic|standard|starter|individual|family|duo"
_GENERIC = {"the", "and", "plan", "plans", "tier", "tiers", "pricing", "price", "subscription", "monthly",
            "app", "ai", "one"}


def _price_stance(claim: Claim, ev_text: str) -> tuple[str, str]:
    """Compare prices only where the evidence talks about the same product and billing period.

    A page listing many products (or an unrelated one) shouldn't count as contradicting a price just
    because it contains some other dollar amount.
    """
    low = ev_text.lower()
    subj = [w for w in _words(claim.subject) if len(w) >= 2]
    if not subj:
        return "neutral", ""
    tier = subj[-1]                                       # "plus" in "openai chatgpt plus"
    brand = [w for w in subj[:-1] if w not in _GENERIC] or subj
    # "chatgpt plus" / "ai pro": the tier together with the word before it, so ChatGPT Pro and
    # Google AI Pro prices are never mixed up on comparison pages
    tier_re = (re.escape(subj[-2]) + r"\s+" if len(subj) >= 2 else "") + re.escape(tier)
    if not any(re.search(r"\b" + re.escape(b) + r"\b", low) for b in brand):
        return "neutral", ""                              # page isn't about this company/product
    monthly = bool(re.search(r"/\s?mo|month", claim.text, re.I))
    claimed = _norm_money(claim.value)
    near: list[str] = []
    for m in _MONEY.finditer(ev_text):
        before = low[max(0, m.start() - 90): m.start()]
        after = low[m.end(): m.end() + 22]
        # the product name must come *before* the price ("Google AI Ultra costs $X"), and no other
        # tier may sit in between ("AI Ultra ... AI Pro is $19.99" is Pro's price, not Ultra's)
        hits = list(re.finditer(r"\b" + tier_re + r"\b", before))
        if not hits and len(subj) >= 2 and any(re.search(r"\b" + re.escape(b) + r"\b", before) for b in brand):
            hits = list(re.finditer(r"\b" + re.escape(tier) + r"\b", before))   # "Netflix ... Standard plan: $X"
        if not hits:
            continue
        # the word right after the name is still part of it ("Copilot Pro", "Netflix Standard")
        between = re.sub(r"\([^)]*\)", " ", before[hits[-1].end():])          # "(formerly X Individual)"
        between = re.sub(r"^\s*[\w+-]+", "", between, count=1)
        if re.search(r"\b(" + _TIERS + r")\b", between.replace(tier, " ")):
            continue
        # a variant of the plan ("Standard with ads", "student", "annual") is a different price
        variant = re.search(r"\b(with ads|ad-supported|ads|student|annual|yearly|education|nonprofit)\b", between)
        if variant and variant.group(1) not in claim.text.lower():
            continue
        if monthly and re.search(r"(/\s?(yr|year)|per year|a year|annual|yearly)", after):
            continue                                      # annual price vs monthly claim
        near.append(_norm_money(m.group(0)))
    if not near:
        return "neutral", ""
    if claimed in near:
        return "supports", ""
    best = Counter(near).most_common(1)[0][0]
    return "contradicts", best if best.startswith(("$", "€", "£", "₹")) else "$" + best


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
