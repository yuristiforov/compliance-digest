"""
test_emailer.py — Full end-to-end pipeline smoke test.

Collect → Process (all articles) → Build HTML → Save preview → Send email.

Usage:
    python test_emailer.py
"""

import logging
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

from db import init_db
from collector import fetch_all_sources
from processor import process_articles
from emailer import _group_by_topic, build_html, send_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_PREVIEW_PATH = Path("data/test_digest.html")


def main() -> None:
    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    db_path = config["database"]["path"]
    email_config = config["email"]
    llm_config = {
        **config["llm"],
        "max_articles_per_batch": config["digest"]["max_articles_per_batch"],
    }

    # ── 1. Init DB ──────────────────────────────────────────────────────────
    print(f"\nInitialising database at: {db_path}")
    init_db(db_path)

    # ── 2. Collect ──────────────────────────────────────────────────────────
    print("Collecting articles (lookback_hours=72) …\n")
    articles = fetch_all_sources(config["sources"], db_path, lookback_hours=72)
    print(f"\nCollected: {len(articles)} articles\n")

    if not articles:
        print("No articles found — nothing to do.")
        return

    # ── 3. Process (all articles) ───────────────────────────────────────────
    print(f"{'='*60}")
    print(f"Sending {len(articles)} articles to LLM for enrichment …")
    print(f"{'='*60}\n")
    enriched = process_articles(articles, llm_config)

    # ── 4. Group by topic ───────────────────────────────────────────────────
    articles_by_topic = _group_by_topic(enriched)

    # ── 5. Print breakdown ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Topic breakdown  ({len(enriched)} articles total)")
    print(f"{'='*60}")
    for topic, arts in articles_by_topic.items():
        print(f"  {topic:<35s} {len(arts):>3d}")

    source_counts = Counter(a.get("source_name", "") for a in enriched)
    print(f"\n{'─'*60}")
    print("Per-source counts:")
    for src, n in sorted(source_counts.items()):
        print(f"  {src:<30s} {n:>3d}")

    # ── 6. Build HTML ───────────────────────────────────────────────────────
    _d = datetime.now()
    date_str = _d.strftime("%d %B %Y").lstrip("0")
    html = build_html(articles_by_topic, date_str)

    # ── 7. Save preview ─────────────────────────────────────────────────────
    _PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PREVIEW_PATH.write_text(html, encoding="utf-8")
    print(f"\n{'─'*60}")
    print(f"HTML preview saved → {_PREVIEW_PATH.resolve()}")
    print(f"HTML size: {len(html):,} bytes  ({len(html)//1024} KB)")

    # ── 8. Send email ────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"Sending digest to {email_config['to']} …")
    try:
        send_digest(html, email_config)
        print("✓ Email sent successfully.")
    except Exception as exc:
        print(f"✗ Email failed: {exc}")

    print()


if __name__ == "__main__":
    main()
