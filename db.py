"""
db.py — SQLite helpers for tracking seen articles and report summaries.

Provides functions to initialize the database and manage:
  - seen_urls:       lightweight dedup index for daily digest
  - articles:        enriched article store for weekly analysis
  - report_summaries: LLM outputs from weekly/monthly/quarterly/annual reports
"""

import sqlite3
from pathlib import Path


def init_db(db_path: str) -> None:
    """Initialize the SQLite database and create tables if they don't exist.

    Creates the parent directory if it does not exist, then creates:
      - seen_urls:       lightweight dedup index (url, source_name, seen_at)
      - articles:        enriched article store for analytics (url, title,
                         source_name, topic_ru, summary_ru, seen_at)
      - report_summaries: hierarchical LLM outputs keyed by period type/label

    Args:
        db_path: Path to the SQLite database file.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_urls (
                url         TEXT PRIMARY KEY,
                source_name TEXT,
                seen_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                url         TEXT PRIMARY KEY,
                title       TEXT,
                source_name TEXT,
                topic_ru    TEXT,
                summary_ru  TEXT,
                seen_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS report_summaries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                period_type  TEXT NOT NULL,
                period_label TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (period_type, period_label)
            )
        """)
        conn.commit()
    migrate_db(db_path)


def migrate_db(db_path: str) -> None:
    """Apply one-time migrations to an existing database.

    Idempotent — safe to call on every startup.

    Migrations applied:
      1. Add UNIQUE index on report_summaries(period_type, period_label) if
         the table was created before the constraint existed.
         Duplicate rows are pruned first, keeping only the highest-id entry
         per (period_type, period_label).
    """
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_report_period'"
        ).fetchone()
        if not exists:
            # Remove duplicates, keeping the latest row per period
            conn.execute("""
                DELETE FROM report_summaries
                WHERE id NOT IN (
                    SELECT MAX(id)
                    FROM report_summaries
                    GROUP BY period_type, period_label
                )
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_report_period
                ON report_summaries (period_type, period_label)
            """)
            conn.commit()


def is_seen(db_path: str, url: str) -> bool:
    """Check whether an article URL has already been processed.

    Args:
        db_path: Path to the SQLite database file.
        url: The article URL to check.

    Returns:
        True if the URL has been seen before, False otherwise.
    """
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_urls WHERE url = ? LIMIT 1", (url,)
        ).fetchone()
    return row is not None


def mark_seen(db_path: str, articles: list[dict]) -> None:
    """Mark a list of articles as seen so they are not processed again.

    Writes to both tables:
      - seen_urls: url + source_name (lightweight dedup index)
      - articles:  full enriched record including title, topic_ru, summary_ru

    Uses INSERT OR IGNORE so duplicate URLs are silently skipped in both tables.

    Args:
        db_path: Path to the SQLite database file.
        articles: List of article dicts, each with at least 'url' and
                  'source_name' keys. After LLM processing they also carry
                  'title', 'topic_ru', and 'summary_ru'.
    """
    seen_rows = [(a["url"], a.get("source_name", "")) for a in articles]
    article_rows = [
        (
            a["url"],
            a.get("title", ""),
            a.get("source_name", ""),
            a.get("topic_ru", ""),
            a.get("summary_ru", ""),
        )
        for a in articles
    ]
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO seen_urls (url, source_name) VALUES (?, ?)",
            seen_rows,
        )
        conn.executemany(
            """INSERT OR IGNORE INTO articles
               (url, title, source_name, topic_ru, summary_ru)
               VALUES (?, ?, ?, ?, ?)""",
            article_rows,
        )
        conn.commit()


def get_articles_last_7_days(db_path: str) -> list[dict]:
    """Retrieve all articles seen in the last 7 days from the articles table.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of article dicts with keys: url, title, source_name,
        topic_ru, summary_ru, seen_at. Ordered most-recent first.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT url, title, source_name, topic_ru, summary_ru, seen_at
            FROM articles
            WHERE seen_at >= datetime('now', '-7 days')
            ORDER BY seen_at DESC
        """).fetchall()
    return [dict(row) for row in rows]


def save_report_summary(
    db_path: str,
    period_type: str,
    period_label: str,
    summary_text: str,
) -> int:
    """Insert a completed LLM report into report_summaries.

    Args:
        db_path:      Path to the SQLite database file.
        period_type:  One of 'weekly', 'monthly', 'quarterly', 'annual'.
        period_label: Human-readable period key, e.g. '2026-W11', '2026-03',
                      '2026-Q1', '2026'.
        summary_text: Full LLM output text to persist.

    Returns:
        The rowid of the newly inserted row.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """INSERT OR REPLACE INTO report_summaries
               (period_type, period_label, summary_text)
               VALUES (?, ?, ?)""",
            (period_type, period_label, summary_text),
        )
        conn.commit()
        return cursor.lastrowid


def get_report_summaries(
    db_path: str,
    period_type: str,
    since_date: str,
) -> list[dict]:
    """Retrieve report summaries of a given type created after since_date.

    Args:
        db_path:     Path to the SQLite database file.
        period_type: One of 'weekly', 'monthly', 'quarterly', 'annual'.
        since_date:  ISO-format datetime string, e.g. '2026-03-01 00:00:00'.
                     Only rows with created_at > this value are returned.

    Returns:
        List of dicts with keys: id, period_type, period_label,
        summary_text, created_at. Ordered by created_at ascending so
        callers receive summaries in chronological order.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, period_type, period_label, summary_text, created_at
               FROM report_summaries
               WHERE period_type = ?
                 AND created_at >= ?
               ORDER BY created_at ASC""",
            (period_type, since_date),
        ).fetchall()
    return [dict(row) for row in rows]
