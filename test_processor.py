"""
test_processor.py — Smoke test for processor.py.

Loads config + .env, collects up to 10 fresh articles, calls the LLM to
enrich them, then prints results and a token-cost estimate.

Usage:
    python test_processor.py
"""

import logging
import sys
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml
from dotenv import load_dotenv

from db import init_db
from collector import fetch_all_sources
from processor import process_articles

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)

# Haiku pricing (USD per million tokens), as of early 2025.
# Update if Anthropic changes pricing.
_HAIKU_INPUT_PRICE_PER_M = 0.80
_HAIKU_OUTPUT_PRICE_PER_M = 4.00


def main() -> None:
    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    db_path = config["database"]["path"]
    llm_config = {
        **config["llm"],
        "max_articles_per_batch": config["digest"]["max_articles_per_batch"],
    }

    print(f"\nInitialising database at: {db_path}")
    init_db(db_path)

    print("Collecting articles (lookback_hours=72) …\n")
    all_articles = fetch_all_sources(
        config["sources"], db_path, lookback_hours=72
    )

    sample = all_articles[:10]
    print(f"Collected {len(all_articles)} articles total. Using first {len(sample)} for LLM test.\n")

    if not sample:
        print("No articles found — nothing to process.")
        return

    print(f"{'='*60}")
    print("Calling LLM …")
    print(f"{'='*60}\n")

    enriched = process_articles(sample, llm_config)

    # --- Print results ---
    print(f"\n{'='*60}")
    print("Results")
    print(f"{'='*60}\n")
    for i, art in enumerate(enriched, start=1):
        print(f"[{i}] {art['title']}")
        print(f"    Source  : {art['source_name']}")
        print(f"    Topic   : {art.get('topic_ru', '—')}")
        summary = art.get("summary_ru", "")
        if summary:
            # Wrap long summaries at ~90 chars for readability.
            words = summary.split()
            lines, line = [], []
            for w in words:
                line.append(w)
                if len(" ".join(line)) > 88:
                    lines.append("    " + " ".join(line))
                    line = []
            if line:
                lines.append("    " + " ".join(line))
            print("    Summary :\n" + "\n".join(lines))
        print()

    # --- Token usage & cost estimate ---
    total_input = sum(
        art.get("_usage", {}).get("input_tokens", 0) for art in enriched
    )
    total_output = sum(
        art.get("_usage", {}).get("output_tokens", 0) for art in enriched
    )
    cost_usd = (
        total_input / 1_000_000 * _HAIKU_INPUT_PRICE_PER_M
        + total_output / 1_000_000 * _HAIKU_OUTPUT_PRICE_PER_M
    )

    print(f"{'─'*60}")
    print("Token usage estimate")
    print(f"{'─'*60}")
    print(f"  Input tokens  : {total_input:>8,}")
    print(f"  Output tokens : {total_output:>8,}")
    print(f"  Est. cost     : ${cost_usd:.5f}  "
          f"(Haiku @ ${_HAIKU_INPUT_PRICE_PER_M}/M in, ${_HAIKU_OUTPUT_PRICE_PER_M}/M out)")
    print()


if __name__ == "__main__":
    main()
