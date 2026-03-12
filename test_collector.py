"""
test_collector.py — Manual smoke test for db.py and collector.py.

Loads config.yaml, initialises the database, fetches articles from all
enabled RSS/scrape sources with a 72-hour lookback window, and prints a
per-source article count summary plus the first 3 articles.

Does NOT call the LLM or send any email.

Usage:
    python test_collector.py
"""

import logging
import sys
from collections import Counter

# Ensure UTF-8 output on Windows (avoids cp1251 encode errors in the console).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml

from db import init_db
from collector import fetch_all_sources

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)


def main() -> None:
    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    db_path = config["database"]["path"]
    sources = config["sources"]

    enabled = [s for s in sources if s.get("enabled", True)]
    disabled = [s for s in sources if not s.get("enabled", True)]

    print(f"\nInitialising database at: {db_path}")
    init_db(db_path)

    print(f"Fetching from {len(enabled)} enabled sources (lookback_hours=72) …")
    if disabled:
        print(f"Skipped (disabled): {', '.join(s['name'] for s in disabled)}")
    print()

    articles = fetch_all_sources(sources, db_path, lookback_hours=72)

    # --- Per-source counts ---
    counts: Counter = Counter(a["source_name"] for a in articles)
    print(f"\n{'='*60}")
    print(f"Articles found: {len(articles)}")
    print(f"{'='*60}")
    print("\nPer-source breakdown:")
    for source in enabled:
        name = source["name"]
        n = counts.get(name, 0)
        method = source.get("method", "rss")
        scraper = source.get("scraper", "")
        tag = f"[{method}/{scraper}]" if scraper else f"[{method}]"
        status = "✓" if n > 0 else "✗"
        print(f"  {status}  {name:<22s} {tag:<18s} {n:>3d} articles")

    # --- First 3 articles ---
    print(f"\n{'─'*60}")
    print("First 3 articles:")
    print(f"{'─'*60}")
    for i, article in enumerate(articles[:3], start=1):
        print(f"\n[{i}] {article['title']}")
        print(f"    Source : {article['source_name']}")
        print(f"    URL    : {article['url']}")
        print(f"    Date   : {article['published_at']}")
        if article.get("snippet"):
            preview = article["snippet"][:120].replace("\n", " ")
            print(f"    Snippet: {preview}…")

    if not articles:
        print("\nNo new articles found.")
        print("Try increasing lookback_hours or check that enabled feeds are reachable.")


if __name__ == "__main__":
    main()
