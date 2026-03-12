"""
test_analyzer.py — Smoke test for the weekly momentum analysis module.

Usage:
    python test_analyzer.py

What it does:
    1. Calls get_articles_last_7_days() and prints the article count.
    2. If count >= 10:
         - Runs the full momentum analysis (LLM call).
         - Saves HTML to data/test_momentum.html.
         - Opens the file in the default browser.
    3. If count < 10:
         - Prints a hint to run main.py a few times first.
"""

import sys
import webbrowser
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

from db import get_articles_last_7_days, init_db
from analyzer import build_momentum_prompt, _call_llm_raw, _build_momentum_html, _SYS_WEEKLY

from datetime import datetime, timedelta


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    config = load_config()
    db_path = config["database"]["path"]
    model = config["llm"].get("model", "claude-haiku-4-5-20251001")

    # Ensure articles table exists.
    init_db(db_path)

    # ── Step 1: count articles ───────────────────────────────────────────────
    articles = get_articles_last_7_days(db_path)
    count = len(articles)
    print(f"\n{'='*60}")
    print(f"  Articles in the last 7 days: {count}")
    print(f"{'='*60}\n")

    if count == 0:
        print("Need more data — run main.py a few times first.")
        print("(The articles table is populated after each successful digest send.)")
        return

    if count < 10:
        print(f"Need more data — run main.py a few times first.")
        print(f"(Have {count} article(s), need at least 10 for a meaningful analysis.)")
        return

    # ── Step 2: run full analysis ─────────────────────────────────────────────
    print(f"Running momentum analysis on {count} article(s) using {model}…\n")
    user_prompt = build_momentum_prompt(articles)
    analysis_text, usage = _call_llm_raw(_SYS_WEEKLY, user_prompt, model)

    print("─" * 60)
    print("LLM RESPONSE:")
    print("─" * 60)
    print(analysis_text)
    print("─" * 60)
    print(f"\nToken usage — input: {usage['input_tokens']:,}  output: {usage['output_tokens']:,}")

    # ── Step 3: build and save HTML ───────────────────────────────────────────
    now = datetime.now()
    date_to = now.strftime("%d.%m.%Y")
    date_from = (now - timedelta(days=7)).strftime("%d.%m.%Y")

    html = _build_momentum_html(analysis_text, date_from, date_to, count)

    out_path = Path("data/test_momentum.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    abs_path = out_path.resolve()
    print(f"\nHTML saved → {abs_path}")

    # ── Step 4: open in browser ───────────────────────────────────────────────
    print("Opening in browser…")
    webbrowser.open(abs_path.as_uri())

    print("\nDone. ✓")


if __name__ == "__main__":
    main()
