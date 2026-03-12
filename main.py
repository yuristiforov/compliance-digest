"""
main.py — Production entry point for the Compliance Digest pipeline.

Pipeline:
    1. Load config + .env
    2. Init DB
    3. Collect new articles
    4. Exit early if nothing new
    5. Enrich via LLM
    6. Group by topic
    7. Build HTML
    8. Save HTML archive
    9. Send email
    10. Mark URLs as seen (only after successful send)
    11. Log run summary

Usage:
    python main.py
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

from db import init_db, mark_seen
from collector import fetch_all_sources
from processor import process_articles
from emailer import _group_by_topic, build_html, send_digest

_LOG_PATH = Path("data/digest.log")


def _setup_logging() -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_PATH, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run() -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=== Compliance Digest run started ===")

    # ── 1. Load config ──────────────────────────────────────────────────────
    config = load_config()
    db_path = config["database"]["path"]
    email_config = config["email"]
    llm_config = {
        **config["llm"],
        "max_articles_per_batch": config["digest"]["max_articles_per_batch"],
    }
    lookback_hours = config["digest"].get("lookback_hours", 24)

    # ── 2. Init DB ──────────────────────────────────────────────────────────
    logger.info("Initialising database at: %s", db_path)
    init_db(db_path)

    # ── 3. Collect ──────────────────────────────────────────────────────────
    logger.info("Collecting articles (lookback_hours=%d)…", lookback_hours)
    articles = fetch_all_sources(config["sources"], db_path, lookback_hours=lookback_hours)
    logger.info("Collected %d new article(s).", len(articles))

    # ── 4. Exit early if nothing new ────────────────────────────────────────
    if not articles:
        logger.info("No new articles today — exiting.")
        return

    # ── 5. Enrich via LLM ──────────────────────────────────────────────────
    logger.info("Enriching %d article(s) via LLM…", len(articles))
    enriched = process_articles(articles, llm_config)

    # ── 6. Group by topic ───────────────────────────────────────────────────
    articles_by_topic = _group_by_topic(enriched)
    topic_summary = ", ".join(
        f"{t} ({len(a)})" for t, a in articles_by_topic.items()
    )
    logger.info("Topics: %s", topic_summary)

    # ── 7. Build HTML ───────────────────────────────────────────────────────
    _d = datetime.now()
    date_str = _d.strftime("%d %B %Y").lstrip("0")
    html = build_html(articles_by_topic, date_str)
    logger.info("HTML digest built (%d bytes).", len(html))

    # ── 8. Save HTML archive ────────────────────────────────────────────────
    archive_name = f"digest_{_d.strftime('%Y-%m-%d')}.html"
    archive_path = Path("data") / archive_name
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(html, encoding="utf-8")
    logger.info("HTML archive saved → %s", archive_path.resolve())

    # ── 9. Send email ────────────────────────────────────────────────────────
    logger.info("Sending digest to %s…", email_config["to"])
    send_digest(html, email_config)
    logger.info("Email sent successfully.")

    # ── 10. Mark URLs as seen (only after successful send) ──────────────────
    mark_seen(db_path, enriched)
    logger.info("Marked %d URL(s) as seen.", len(enriched))

    # ── 11. Log run summary ──────────────────────────────────────────────────
    total_input = sum(a.get("_usage", {}).get("input_tokens", 0) for a in enriched)
    total_output = sum(a.get("_usage", {}).get("output_tokens", 0) for a in enriched)
    logger.info(
        "Run complete — articles: %d, topics: %d, LLM tokens: %d in / %d out.",
        len(enriched),
        len(articles_by_topic),
        total_input,
        total_output,
    )
    logger.info("=== Compliance Digest run finished ===")


if __name__ == "__main__":
    run()
