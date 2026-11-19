"""Command line: `python -m staleguard.cli answer.txt` or pipe text on stdin."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .models import CheckRequest, Verdict
from .pipeline import StaleGuard

_ICON = {Verdict.CURRENT: "✅", Verdict.OUTDATED: "❌", Verdict.PARTIALLY: "🟡",
         Verdict.UNVERIFIED: "❔", Verdict.SKIPPED: "·"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="staleguard", description="Find stale claims in an LLM answer.")
    p.add_argument("file", nargs="?", help="file with the answer (default: stdin)")
    p.add_argument("-q", "--question", default="")
    p.add_argument("-k", "--knowledge-date", default=None, help="YYYY-MM the answer's knowledge is from")
    p.add_argument("--json", action="store_true", help="print the full JSON report")
    p.add_argument("--fail-under", type=int, default=None,
                   help="exit 1 if overall freshness is below this (for CI / eval pipelines)")
    a = p.parse_args(argv)
    text = open(a.file).read() if a.file else sys.stdin.read()

    async def run():
        g = StaleGuard()
        try:
            return await g.check(CheckRequest(answer=text, question=a.question, knowledge_date=a.knowledge_date))
        finally:
            await g.aclose()

    rep = asyncio.run(run())
    if a.json:
        print(json.dumps(rep.model_dump(mode="json"), indent=2))
    else:
        print(f"\nStaleGuard [{rep.mode}]  overall freshness {rep.overall_freshness}/100 - {rep.grade}\n")
        for r in rep.claims:
            print(f" {_ICON[r.verdict]} {r.freshness:>3}  [{r.claim.category.value}] {r.claim.text}")
            if r.verdict in (Verdict.OUTDATED, Verdict.PARTIALLY):
                print(f"        -> {r.explanation}")
                for e in r.evidence:
                    if e.stance == "contradicts":
                        print(f"           {e.url} ({e.published_date or 'undated'})")
        if rep.refreshed_answer and rep.refreshed_answer != rep.answer:
            print("\nRefreshed answer:\n" + rep.refreshed_answer)
        s = rep.stats
        print(f"\n{s['claims']} claims · {s['searches']} searches · {s['llm_calls']} LLM calls via Bifrost "
              f"· {s['fallbacks_used']} fallbacks · {s['elapsed_ms']} ms")
    if a.fail_under is not None and rep.overall_freshness < a.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
