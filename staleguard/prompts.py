"""Prompt templates. Kept separate so they're easy to read and tune."""

EXTRACT_SYSTEM = """You audit AI-generated answers for information that may have gone stale.
Split the answer into atomic, self-contained factual claims (resolve pronouns so each
claim stands alone). For each claim decide:
- category: one of pricing | api_version | regulation | company_info | statistic | product | stable
  (stable = timeless facts such as definitions, history, maths - these never go stale)
- time_sensitive: true if the fact could plausibly change within ~2 years
- subject: the entity the claim is about (product, company, law, library)
- attribute: what property of the subject is asserted
- value: the asserted value, verbatim if possible (price, version, name, date, status)
- span: the exact substring of the answer this claim comes from (copy it character for character)
- search_query: a concise web search query that would find the CURRENT value of this
  attribute. Do not include the asserted value itself, so the search isn't biased.
Return JSON: {"claims": [{"text":..., "span":..., "category":..., "time_sensitive":...,
"subject":..., "attribute":..., "value":..., "search_query":...}]}
Return at most {max_claims} claims, prioritising time-sensitive ones."""

EXTRACT_USER = """Question (context, may be empty): {question}

Sentences a heuristic pre-pass flagged as risky: {risky}

Answer to audit:
\"\"\"{answer}\"\"\""""

JUDGE_SYSTEM = """You are a meticulous fact-checker. Today is {today}.
You get one claim taken from an AI answer and numbered web search results retrieved just now.
Decide whether the claim is still true TODAY.
- "current": recent sources confirm the claim's value.
- "outdated": sources show the value has changed (newer price, newer version, law passed,
  different person in role, etc.). Give the up-to-date value in current_value.
- "partially_current": the core is right but a detail changed.
- "unverified": the results don't settle it. Never guess - prefer unverified to a hallucinated verdict.
Prefer official/primary sources and the most recent dates. A source only "supports" if it
states the same value; it "contradicts" if it states a different, more recent value.
Return JSON: {{"verdict": ..., "current_value": "...", "confidence": 0-1,
"explanation": "one or two sentences citing sources like [1]",
"stances": {{"1": "supports|contradicts|neutral", ...}}}}"""

JUDGE_USER = """Claim: {claim}
Category: {category}
Subject: {subject} | Attribute: {attribute} | Asserted value: {value}

Search results:
{results}"""

REWRITE_SYSTEM = """Rewrite the answer so that every outdated claim is corrected using the
verified findings. Keep the original structure, tone and length. Keep claims that were
confirmed or unverified, but append " (unverified)" to unverified time-sensitive claims.
Cite corrected facts inline as [n] using the source numbers given. Return JSON:
{"answer": "..."}"""

REWRITE_USER = """Original answer:
\"\"\"{answer}\"\"\"

Verified findings:
{findings}

Sources:
{sources}"""
