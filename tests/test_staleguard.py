import re
import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from staleguard import heuristics, scoring
from staleguard.config import Settings
from staleguard.llm import BifrostClient, parse_json_block
from staleguard.models import Category, Claim, CheckRequest, Evidence, Verdict
from staleguard.pipeline import StaleGuard
from staleguard.search import TavilyClient

DEMO = (Path(__file__).parent.parent / "examples" / "demo_answer.txt").read_text()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- heuristics

@pytest.mark.parametrize("text,cat", [
    ("ChatGPT Plus costs $20 per month.", Category.PRICING),
    ("Node.js 18 is the active LTS line.", Category.API_VERSION),
    ("The EU AI Act is still being negotiated.", Category.REGULATION),
    ("Satya Nadella is the CEO of Microsoft.", Category.COMPANY_INFO),
    ("Python was created by Guido van Rossum.", Category.STABLE),
    ("Water boils at 100 degrees Celsius at sea level.", Category.STABLE),
])
def test_classify(text, cat):
    assert heuristics.classify(text) == cat


def test_key_values():
    assert heuristics.key_value("Copilot costs $10 per month", Category.PRICING) == "$10"
    assert heuristics.key_value("Use Python 3.11 today", Category.API_VERSION) == "3.11"


def test_money_normalisation_keeps_magnitude():
    assert heuristics._norm_money("$10") != heuristics._norm_money("$100")
    assert heuristics._norm_money("$10.00") == heuristics._norm_money("$10")


def test_version_contradiction_uses_anchor():
    claim = Claim(id="c1", text="Node.js 18 is the active LTS line.", category=Category.API_VERSION,
                  subject="Node.js", value="18")
    stance, val = heuristics.stance_of(claim, "Node.js 18 reached end-of-life on April 30, 2025. Node.js 24 is Active LTS.")
    assert stance == "contradicts" and val == "24"  # not "30" from the date


def test_price_supported():
    claim = Claim(id="c1", text="Copilot costs $10 per month", category=Category.PRICING, subject="Copilot", value="$10")
    assert heuristics.stance_of(claim, "GitHub Copilot Pro costs $10 per month or $100 per year")[0] == "supports"


# ------------------------------------------------------------------- scoring

def test_prior_risk_grows_with_age():
    today = date(2026, 11, 20)
    young = scoring.prior_risk(Category.PRICING, "2026-10", today)
    old = scoring.prior_risk(Category.PRICING, "2024-01", today)
    assert 0 <= young < old <= 1
    assert scoring.prior_risk(Category.REGULATION, "2025-01", today) < scoring.prior_risk(Category.PRICING, "2025-01", today)


def test_score_ordering():
    ev = [Evidence(title="t", url="u", snippet="s", published_date="2026-11-01", stance="supports")]
    cur, _ = scoring.score_claim(Verdict.CURRENT, Category.PRICING, ev, 0.9, "2024-06", date(2026, 11, 20))
    ev2 = [Evidence(title="t", url="u", snippet="s", published_date="2026-11-01", stance="contradicts")]
    old, _ = scoring.score_claim(Verdict.OUTDATED, Category.PRICING, ev2, 0.9, "2024-06", date(2026, 11, 20))
    unv, _ = scoring.score_claim(Verdict.UNVERIFIED, Category.PRICING, [], 0.3, "2024-06", date(2026, 11, 20))
    assert cur > 85 and old < 15 and old < unv < cur


def test_overall_penalises_single_stale_claim():
    from staleguard.models import ClaimResult, ScoreBreakdown
    br = ScoreBreakdown(prior_risk=0, verdict_score=1, evidence_recency=1, source_agreement=1, confidence=1)
    mk = lambda f, v: ClaimResult(claim=Claim(id="x", text="x", category=Category.PRICING), verdict=v, freshness=f, breakdown=br)
    assert scoring.overall([mk(95, Verdict.CURRENT)] * 4 + [mk(5, Verdict.OUTDATED)]) < 70


# ------------------------------------------------------------ offline pipeline

def test_offline_demo_end_to_end():
    g = StaleGuard(Settings(mode="offline"))
    rep = run(g.check(CheckRequest(answer=DEMO, knowledge_date="2024-06")))
    by = {r.claim.text.split()[0:6].__str__(): r for r in rep.claims}
    verdicts = {r.claim.text[:25]: r.verdict for r in rep.claims}
    assert rep.mode == "offline"
    assert sum(v == Verdict.OUTDATED for v in verdicts.values()) >= 3
    assert any(r.verdict == Verdict.CURRENT for r in rep.claims)
    assert any(r.verdict == Verdict.SKIPPED for r in rep.claims)
    py = next(r for r in rep.claims if "Python 3.11" in r.claim.text)
    assert py.verdict == Verdict.OUTDATED and py.current_value.startswith("3.1")
    assert rep.overall_freshness < 40 and rep.grade == "Stale"
    assert "Python 3.14" in rep.refreshed_answer and "React 19" in rep.refreshed_answer
    assert "Python 3.11" not in rep.refreshed_answer and "Sources:" in rep.refreshed_answer


def test_fresh_answer_scores_high():
    g = StaleGuard(Settings(mode="offline"))
    rep = run(g.check(CheckRequest(answer="GitHub Copilot Pro costs $10 per month. Python was created by Guido van Rossum.",
                                   knowledge_date="2025-10")))
    assert rep.overall_freshness >= 70


# --------------------------------------------- live pipeline (mocked upstreams)

def _bifrost_handler(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"headers": dict(request.headers), "body": body, "url": str(request.url)})
        system = body["messages"][0]["content"]
        if "audit AI-generated answers" in system:
            content = {"claims": [
                {"text": "React 18 is the current major version of React.", "span": "React 18 is the current major version of React.",
                 "category": "api_version", "time_sensitive": True, "subject": "React", "attribute": "latest major version",
                 "value": "18", "search_query": "React latest stable major version"},
                {"text": "Python was created by Guido van Rossum.", "span": "Python was created by Guido van Rossum.",
                 "category": "stable", "time_sensitive": False, "subject": "Python", "attribute": "creator",
                 "value": "Guido van Rossum", "search_query": ""}]}
            extra = {"provider": "openai", "routing_info": {"is_fallback": False}}
        elif "fact-checker" in system:
            content = {"verdict": "outdated", "current_value": "React 19", "confidence": 0.92,
                       "explanation": "React 19 became the stable major in Dec 2024 [1].", "stances": {"1": "contradicts"}}
            # pretend the primary provider was down and Bifrost fell back
            extra = {"provider": "anthropic", "routing_info": {"is_fallback": True, "primary_provider": "openai"}}
        else:
            content = {"answer": "React 19 is the current major version of React [1]. Python was created by Guido van Rossum."}
            extra = {"provider": "openai"}
        return httpx.Response(200, json={
            "model": body["model"].split("/")[-1], "choices": [{"message": {"role": "assistant", "content": json.dumps(content)}}],
            "usage": {"total_tokens": 321}, "extra_fields": extra})
    return handler


def _tavily_handler(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx.Response(200, json={"results": [
            {"title": "React v19", "url": "https://react.dev/blog/2024/12/05/react-19",
             "content": "React v19 is now available on npm.", "published_date": "2024-12-05", "score": 0.9}]})
    return handler


def test_live_pipeline_through_bifrost_and_tavily():
    llm_seen, search_seen = [], []
    s = Settings(mode="live", tavily_api_key="tvly-test", bifrost_url="http://bifrost.test",
                 bifrost_virtual_key="sk-bf-demo", extract_model="openai/gpt-4o-mini", judge_model="openai/gpt-4o",
                 fallbacks=["anthropic/claude-3-5-haiku-latest"])
    llm = BifrostClient(s, httpx.AsyncClient(transport=httpx.MockTransport(_bifrost_handler(llm_seen))))
    search = TavilyClient(s, httpx.AsyncClient(transport=httpx.MockTransport(_tavily_handler(search_seen))))
    g = StaleGuard(s, llm=llm, search=search)
    rep = run(g.check(CheckRequest(answer="React 18 is the current major version of React. Python was created by Guido van Rossum.")))

    assert rep.mode == "live"
    react = next(r for r in rep.claims if "React" in r.claim.text)
    assert react.verdict == Verdict.OUTDATED and react.current_value == "React 19"
    assert react.evidence[0].stance == "contradicts" and react.freshness < 15
    assert next(r for r in rep.claims if "Guido" in r.claim.text).verdict == Verdict.SKIPPED
    assert "React 19" in rep.refreshed_answer

    # every LLM call went to Bifrost's OpenAI-compatible endpoint with fallbacks + virtual key
    assert all(c["url"].endswith("/v1/chat/completions") for c in llm_seen)
    assert all(c["headers"]["x-bf-vk"] == "sk-bf-demo" for c in llm_seen)
    assert all(c["body"]["fallbacks"] == ["anthropic/claude-3-5-haiku-latest"] for c in llm_seen)
    assert {c["body"]["model"] for c in llm_seen} == {"openai/gpt-4o-mini", "openai/gpt-4o"}
    # Bifrost routing info is surfaced in the trace
    judge = next(t for t in rep.trace if t.purpose.startswith("judge"))
    assert judge.fallback_used and judge.served_by.startswith("anthropic/")
    assert rep.stats["fallbacks_used"] == 1 and rep.stats["tokens"] > 0
    # Tavily got a recency-bounded search, bearer auth, and wasn't asked about the stable claim
    assert len(search_seen) == 1
    assert search_seen[0]["headers"]["authorization"] == "Bearer tvly-test"
    assert "start_date" in search_seen[0]["body"] and search_seen[0]["body"]["include_published_date"] is True


def test_llm_failure_falls_back_to_heuristics():
    s = Settings(mode="live", tavily_api_key="tvly-test", bifrost_url="http://bifrost.test")
    dead = BifrostClient(s, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503, text="all providers down"))))
    from staleguard.offline import SnapshotSearch
    g = StaleGuard(s, llm=dead, search=SnapshotSearch())
    rep = run(g.check(CheckRequest(answer=DEMO)))
    assert any(r.verdict == Verdict.OUTDATED for r in rep.claims)  # still useful
    assert any(t.error for t in rep.trace if t.kind == "llm")


def test_parse_json_block():
    assert parse_json_block('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_block('Sure! {"a": [1,2]} hope that helps') == {"a": [1, 2]}


# -------------------------------------------------------------------- server

def test_server_offline():
    from fastapi.testclient import TestClient
    from staleguard import server
    server.guard = StaleGuard(Settings(mode="offline"))
    c = TestClient(server.app)
    assert c.get("/api/health").json()["mode"] == "offline"
    assert "StaleGuard" in c.get("/").text
    demo = c.get("/api/demo").json()
    r = c.post("/api/check", json={"answer": demo["answer"], "knowledge_date": "2024-06"})
    assert r.status_code == 200 and r.json()["grade"] == "Stale"
    assert c.post("/api/check", json={"answer": "  "}).status_code == 400
    assert c.post("/v1/chat/completions", json={"model": "x", "messages": []}).status_code == 503


# ------------------------------------------------------------ streaming + samples

def test_stream_events_order():
    g = StaleGuard(Settings(mode="offline"))

    async def collect():
        return [ev async for ev in g.check_stream(CheckRequest(answer=DEMO, knowledge_date="2024-06"))]

    evs = run(collect())
    types = [e["type"] for e in evs]
    assert types[0] == "stage" and types[-1] == "report"
    assert types.index("claims") < types.index("claim")
    n_claims = len(next(e for e in evs if e["type"] == "claims")["claims"])
    assert types.count("claim") == n_claims
    assert evs[-1]["report"]["grade"] == "Stale"


@pytest.mark.parametrize("sample_id,min_outdated", [("dev-stack", 4), ("subscriptions", 2), ("tech-news", 2)])
def test_bundled_samples(sample_id, min_outdated):
    samples = json.loads((Path(__file__).parent.parent / "examples" / "samples.json").read_text())
    s = next(x for x in samples if x["id"] == sample_id)
    rep = run(StaleGuard(Settings(mode="offline")).check(CheckRequest(answer=s["answer"], knowledge_date=s["knowledge_date"])))
    assert sum(r.verdict == Verdict.OUTDATED for r in rep.claims) >= min_outdated
    assert any(r.verdict == Verdict.CURRENT for r in rep.claims)


def test_server_stream_and_samples():
    from fastapi.testclient import TestClient
    from staleguard import server
    server.guard = StaleGuard(Settings(mode="offline"))
    c = TestClient(server.app)
    assert len(c.get("/api/samples").json()) >= 3
    with c.stream("POST", "/api/check/stream", json={"answer": DEMO}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    assert "event: claims" in body and "event: report" in body
    assert c.post("/api/check/stream", json={"answer": " "}).status_code == 400


# ----------------------------------- regressions found in the first live (Tavily) run

PLANS = (Path(__file__).parent.parent / "examples" / "plans_answer.txt").read_text()


def test_section_context_and_queries():
    claims = heuristics.heuristic_claims(PLANS, 10)
    plus = next(c for c in claims if c.text.startswith("Plus ($20/month)"))
    assert plus.subject == "OpenAI ChatGPT Plus" and plus.value == "$20"
    assert "ChatGPT Plus" in plus.search_query and "$20" not in plus.search_query
    ultra = next(c for c in claims if "Ultra" in c.text)
    assert "Google" in ultra.subject and "OpenAI" not in ultra.subject
    # numbers that aren't prices survive in queries ("o1" must not become "o")
    q = heuristics.build_query("unlimited access to o1 since 2024 for $200/month", Category.PRICING)
    assert "o1" in q and "2024" in q and "$200" not in q
    q = heuristics.build_query("Pro ($200/month): unlimited access to o1", Category.PRICING, "OpenAI ChatGPT Pro")
    assert q.startswith("OpenAI ChatGPT Pro") and "$200" not in q and "()" not in q
    assert all(c.span in PLANS for c in claims)


def test_price_stance_ignores_unrelated_pages():
    plus = Claim(id="x", text="Plus ($20/month): the main paid tier.", category=Category.PRICING,
                 subject="OpenAI ChatGPT Plus", value="$20")
    assert heuristics.stance_of(plus, "GoPro Premium+ costs $24.99 per year.")[0] == "neutral"
    assert heuristics.stance_of(plus, "Genspark pricing: Plus plan $24.99/month.")[0] == "neutral"
    assert heuristics.stance_of(plus, "Claude Pro costs $20/month; Claude Max is $100.")[0] == "neutral"
    assert heuristics.stance_of(plus, "ChatGPT Plus costs $20 per month.")[0] == "supports"
    assert heuristics.stance_of(plus, "OpenAI raised ChatGPT Plus to $22 per month.") == ("contradicts", "$22")
    assert heuristics.stance_of(plus, "ChatGPT Plus: $20/month or $200 per year.")[0] == "supports"


def test_price_stance_keeps_same_named_tiers_apart():
    page = "ChatGPT Plus costs $20/month and ChatGPT Pro $200/month. Google AI Pro is $19.99/month."
    gpro = Claim(id="x", text="Google AI Pro ($19.99/month): replaced Gemini Advanced.", category=Category.PRICING,
                 subject="Gemini Google AI Pro", value="$19.99")
    cpro = Claim(id="y", text="Pro ($200/month): unlimited o1.", category=Category.PRICING,
                 subject="OpenAI ChatGPT Pro", value="$200")
    assert heuristics.stance_of(gpro, page)[0] == "supports"
    assert heuristics.stance_of(cpro, page)[0] == "supports"


def test_money_suffix_needs_word_boundary():
    assert heuristics.key_value("a roughly $20 mid tier", Category.PRICING) == "$20"
    assert heuristics.key_value("raised $6.6 billion", Category.PRICING) == "$6.6 billion".replace(" ", "")


def test_unverifiable_answer_is_not_called_stale():
    g = StaleGuard(Settings(mode="offline"))
    rep = run(g.check(CheckRequest(answer=PLANS)))
    assert not any(r.verdict == Verdict.OUTDATED for r in rep.claims)
    assert any(r.verdict == Verdict.CURRENT for r in rep.claims)      # ChatGPT Plus $20 is in the snapshot
    assert rep.grade == "Partly verified"
    rep = run(g.check(CheckRequest(answer="Acme Widgets Pro costs $49 per month. Acme's CEO is Jane Roe.")))
    assert rep.grade == "Couldn't verify"


def test_price_stance_uses_the_price_after_the_product_name():
    ultra = Claim(id="u", text="Google AI Ultra ($249.99/month): launched May 2025.", category=Category.PRICING,
                  subject="Gemini Google AI Ultra", value="$249.99")
    page = ("For most people, Google AI Pro is the right plan. It costs $19.99 a month in the US. "
            "Google AI Ultra costs $99.99 a month for 5x the Pro limits or $199.99 for 20x.")
    assert heuristics.stance_of(ultra, page) == ("contradicts", "$99.99")
    assert heuristics.stance_of(ultra, "Gemini plans: Google AI Ultra is $249.99/month.")[0] == "supports"


# ------------------------------------------- regressions from the second live run

BRIEF = (Path(__file__).parent.parent / "examples" / "dev_brief_answer.txt").read_text()


def test_generic_headings_do_not_leak_into_claims():
    claims = heuristics.heuristic_claims(BRIEF, 12)
    texts = [c.text for c in claims]
    assert not any(t.startswith("Here's a quick guide") for t in texts)        # filler, not a fact
    assert any("Twitter" in t for t in texts)                                   # all facts fit
    for c in claims:
        assert not re.search(r"\b(Subscriptions|Regulation|Languages)\b", c.subject + " " + c.search_query), c
    netflix = next(c for c in claims if "Netflix" in c.text)
    assert heuristics.stance_of(netflix, "The Netflix Standard plan now costs $17.99 per month in the US.")[0] == "contradicts"


def test_status_change_must_name_the_subject():
    eu = next(c for c in heuristics.heuristic_claims(BRIEF, 12) if "EU AI Act" in c.text)
    assert eu.subject == "EU AI Act"
    unrelated = "Some other regulation was updated. It entered into force on July 27, 2026."
    assert heuristics.stance_of(eu, "The EU AI Act and its rules. " + unrelated)[0] == "neutral"
    good = "The EU Artificial Intelligence Act (AI Act) entered into force on 1 August 2024."
    stance, now = heuristics.stance_of(eu, good)
    assert stance == "contradicts" and "1 August 2024" in now


def test_named_headings_still_set_context():
    assert heuristics._heading("OpenAI (ChatGPT)").startswith("OpenAI")
    assert heuristics._heading("Subscriptions") == ""
    assert heuristics._heading("Languages and frameworks") == ""
    assert heuristics._heading("This is a full sentence.") is None


# -------------------------------------------- regressions from the third live run

def test_possessive_product_names_and_ad_tiers():
    netflix = next(c for c in heuristics.heuristic_claims(BRIEF, 12) if "Netflix" in c.text)
    assert netflix.subject == "Netflix Standard"
    page = ("Netflix plans in the US: Standard with ads costs $8.99 per month. "
            "The Netflix Standard plan costs $17.99 per month, and Premium is $24.99.")
    assert heuristics.stance_of(netflix, page) == ("contradicts", "$17.99")
    assert heuristics.stance_of(netflix, "Netflix pricing. Standard plan: $15.49 per month.")[0] == "supports"


def test_status_statement_is_cleaned():
    tw = next(c for c in heuristics.heuristic_claims(BRIEF, 12) if "Twitter" in c.text)
    stance, now = heuristics.stance_of(tw, 'Scooby on Instagram: "In July 2023, Elon Musk officially rebranded Twitter to X.')
    assert stance == "contradicts" and now.startswith("In July 2023") and "Instagram" not in now
    eu = next(c for c in heuristics.heuristic_claims(BRIEF, 12) if "EU AI Act" in c.text)
    stance, now = heuristics.stance_of(eu, "## Background\n\nThe EU AI Act entered into force on 1 August 2024.")
    assert now.startswith("The EU AI Act entered into force")


def test_social_sites_are_excluded_from_search():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})
    s = Settings(mode="live", tavily_api_key="tvly-test")
    t = TavilyClient(s, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run(t.search("Twitter name", Category.PRODUCT, []))
    assert "instagram.com" in seen[0]["exclude_domains"]


def test_version_mention_is_not_confirmation():
    react = Claim(id="r", text="React 18 is the current major version of React.", category=Category.API_VERSION,
                  subject="React", value="18")
    assert heuristics.stance_of(react, "React 18 introduced automatic batching and concurrent rendering.")[0] == "neutral"
    assert heuristics.stance_of(react, "React 18 is the latest stable release of React.")[0] == "supports"
    assert heuristics.stance_of(react, "React 19 is now the latest stable version.")[0] == "contradicts"
