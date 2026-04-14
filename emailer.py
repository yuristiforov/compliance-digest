"""
emailer.py — Digest assembly and email delivery.

Builds a self-contained HTML email from enriched articles grouped by topic
and sends it via Gmail SMTP using an App Password.

Environment variables required (loaded by the caller via python-dotenv):
    GMAIL_USER         — Gmail address used as the SMTP login and From address.
    GMAIL_APP_PASSWORD — 16-character Gmail App Password (spaces are stripped).
"""

import logging
import os
import smtplib
from collections import defaultdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

logger = logging.getLogger(__name__)

_FALLBACK_TOPIC = "Прочее"
_ACCENT = "#1a73e8"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _group_by_topic(articles: list[dict]) -> dict[str, list[dict]]:
    """Group articles by topic_ru, alphabetically sorted, Прочее last.

    Args:
        articles: Flat list of enriched article dicts (must have topic_ru).

    Returns:
        OrderedDict-style plain dict: topic → article list, alphabetical,
        with "Прочее" always at the end.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for article in articles:
        topic = (article.get("topic_ru") or _FALLBACK_TOPIC).strip()
        grouped[topic].append(article)

    def sort_key(topic: str) -> tuple:
        return (1 if topic == _FALLBACK_TOPIC else 0, topic)

    return {topic: grouped[topic] for topic in sorted(grouped, key=sort_key)}


def build_html(articles_by_topic: dict[str, list[dict]], date_str: str) -> str:
    """Build a complete, self-contained HTML digest email.

    Args:
        articles_by_topic: Ordered dict from _group_by_topic().
        date_str: Human-readable date string for the header (e.g. "10 March 2026").

    Returns:
        Full HTML document as a string, ready to be used as an email body.
    """
    total = sum(len(v) for v in articles_by_topic.values())
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Collect per-source counts across all articles.
    source_counts: dict[str, int] = defaultdict(int)
    for arts in articles_by_topic.values():
        for a in arts:
            source_counts[a.get("source_name", "Unknown")] += 1

    # --- Table of contents ---
    toc_items = "\n".join(
        f'        <li><a href="#{_topic_anchor(t)}" style="color:{_ACCENT};text-decoration:none;">'
        f'{escape(t)} <span style="color:#888;font-size:13px;">({len(arts)})</span></a></li>'
        for t, arts in articles_by_topic.items()
    )

    # --- Topic sections ---
    sections: list[str] = []
    topics = list(articles_by_topic.items())
    for section_idx, (topic, arts) in enumerate(topics):
        anchor = _topic_anchor(topic)
        cards = "\n".join(_render_card(a) for a in arts)
        hr = '<hr style="border:none;border-top:1px solid #e0e0e0;margin:32px 0;">' \
             if section_idx < len(topics) - 1 else ""
        sections.append(f"""
    <section id="{anchor}">
      <h2 style="color:{_ACCENT};font-size:20px;margin:24px 0 16px;
                 padding-bottom:6px;border-bottom:2px solid {_ACCENT};">
        {escape(topic)}
        <span style="font-size:14px;font-weight:normal;color:#888;margin-left:8px;">
          ({len(arts)})
        </span>
      </h2>
      {cards}
    </section>
    {hr}""")

    # --- Footer: source breakdown ---
    source_rows = "\n".join(
        f'      <tr><td style="padding:2px 16px 2px 0;color:#555;">{escape(src)}</td>'
        f'<td style="color:#333;font-weight:600;">{cnt}</td></tr>'
        for src, cnt in sorted(source_counts.items())
    )

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Compliance Digest — {escape(date_str)}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,Helvetica,sans-serif;
             color:#202124;font-size:15px;line-height:1.6;">

  <div style="max-width:720px;margin:0 auto;background:#fff;
              box-shadow:0 1px 4px rgba(0,0,0,.12);">

    <!-- HEADER -->
    <div style="background:{_ACCENT};padding:28px 32px 24px;">
      <h1 style="margin:0;color:#fff;font-size:26px;font-weight:700;letter-spacing:.3px;">
        Compliance Digest
      </h1>
      <p style="margin:6px 0 0;color:rgba(255,255,255,.85);font-size:14px;">
        {escape(date_str)} &nbsp;·&nbsp; {total} материалов
      </p>
    </div>

    <!-- TABLE OF CONTENTS -->
    <div style="padding:24px 32px 8px;background:#f8f9fa;
                border-bottom:1px solid #e0e0e0;">
      <p style="margin:0 0 10px;font-size:13px;font-weight:700;
                text-transform:uppercase;letter-spacing:.6px;color:#666;">
        Темы
      </p>
      <ul style="margin:0;padding:0 0 0 18px;list-style:disc;">
{toc_items}
      </ul>
    </div>

    <!-- SECTIONS -->
    <div style="padding:8px 32px 24px;">
{"".join(sections)}
    </div>

    <!-- FOOTER -->
    <div style="background:#f8f9fa;border-top:1px solid #e0e0e0;
                padding:20px 32px;font-size:13px;color:#666;">
      <p style="margin:0 0 10px;">
        <strong>Сформировано:</strong> {escape(generated_at)} &nbsp;·&nbsp;
        <strong>Статей:</strong> {total}
      </p>
      <table style="border-collapse:collapse;">
        <thead>
          <tr>
            <th style="text-align:left;padding:2px 16px 4px 0;
                       font-size:12px;text-transform:uppercase;
                       letter-spacing:.4px;color:#888;">Источник</th>
            <th style="text-align:left;padding:2px 0 4px;
                       font-size:12px;text-transform:uppercase;
                       letter-spacing:.4px;color:#888;">Статей</th>
          </tr>
        </thead>
        <tbody>
{source_rows}
        </tbody>
      </table>
    </div>

  </div>
</body>
</html>"""

    return html


def send_digest(html: str, email_config: dict) -> None:
    """Send the HTML digest via Gmail SMTP.

    Reads GMAIL_USER and GMAIL_APP_PASSWORD from the environment.
    Falls back to email_config values if the env vars are absent.

    Args:
        html: Complete HTML string produced by build_html().
        email_config: The 'email' section from config.yaml.
    """
    gmail_user = os.environ.get("GMAIL_USER") or email_config.get("smtp_user", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD") or email_config.get("smtp_password", "")
    # Gmail App Passwords are shown with spaces; strip them.
    app_password = app_password.replace(" ", "")

    to_field = email_config["to"]
    recipients = to_field if isinstance(to_field, list) else [to_field]
    from_name = email_config.get("from_name", "Compliance Digest Bot")
    smtp_server = email_config.get("smtp_server", "smtp.gmail.com")
    smtp_port = int(email_config.get("smtp_port", 587))

    date_str = datetime.now().strftime("%d %B %Y")
    subject = email_config.get("subject_template", "Compliance Digest — {date}").format(
        date=date_str
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{gmail_user}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_user, app_password)
            server.sendmail(gmail_user, recipients, msg.as_bytes())
        logger.info("Digest sent to %s (subject: %s)", ", ".join(recipients), subject)
    except Exception as exc:
        logger.error("Failed to send digest to %s: %s", ", ".join(recipients), exc)
        raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _topic_anchor(topic: str) -> str:
    """Convert a topic label to a safe HTML anchor id."""
    safe = topic.lower()
    for ch in " /&,":
        safe = safe.replace(ch, "-")
    # Keep only alphanumeric, hyphens, Cyrillic — strip the rest.
    safe = "".join(c for c in safe if c.isalnum() or c == "-")
    return safe.strip("-") or "section"


def _render_card(article: dict) -> str:
    """Render a single article as an HTML card block.

    Args:
        article: Enriched article dict with keys: title, url, source_name,
                 published_at, summary_ru.

    Returns:
        HTML string for the card.
    """
    title = escape(article.get("title") or "Без названия")
    url = escape(article.get("url") or "#")
    source = escape(article.get("source_name") or "")
    published = _format_date(article.get("published_at") or "")
    summary = escape(article.get("summary_ru") or "")

    meta_parts = []
    if source:
        meta_parts.append(source)
    if published:
        meta_parts.append(published)
    meta_line = " &nbsp;·&nbsp; ".join(meta_parts)

    return f"""
      <div style="background:#fff;border:1px solid #e8eaed;border-radius:6px;
                  padding:16px 18px;margin-bottom:12px;">
        <p style="margin:0 0 4px;">
          <a href="{url}" target="_blank" rel="noopener"
             style="color:{_ACCENT};font-weight:700;font-size:15px;
                    text-decoration:none;">
            {title}
          </a>
        </p>
        <p style="margin:0 0 8px;font-size:12px;color:#888;">{meta_line}</p>
        {f'<p style="margin:0 0 10px;font-size:14px;color:#444;">{summary}</p>' if summary else ""}
        <a href="{url}" target="_blank" rel="noopener"
           style="font-size:12px;color:{_ACCENT};text-decoration:none;">
          → Читать оригинал
        </a>
      </div>"""


def _format_date(date_str: str) -> str:
    """Parse an ISO-8601 date string and return a short human-readable form.

    Falls back to the raw string if parsing fails.
    """
    if not date_str:
        return ""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
    ):
        try:
            dt = datetime.strptime(date_str, fmt)
            return _strftime_no_pad(dt, "%d %b %Y")
        except (ValueError, AttributeError):
            pass
    # fromisoformat covers most remaining cases
    try:
        dt = datetime.fromisoformat(date_str)
        return _strftime_no_pad(dt, "%d %b %Y")
    except (ValueError, TypeError):
        return date_str[:10]  # return the date portion as-is


def _strftime_no_pad(dt: datetime, fmt: str) -> str:
    """Format a datetime without zero-padding the day (cross-platform).

    %-d works on Linux/macOS but not on Windows; this works everywhere.
    """
    result = dt.strftime(fmt)
    # Strip a leading zero from the day component only.
    if result and result[0] == "0":
        result = result[1:]
    return result
