"""
analyzer.py — Hierarchical regulatory intelligence analysis for Compliance Digest.

Supports four report levels built from bottom up:
  weekly     — raw articles → momentum analysis
  monthly    — weekly summaries → monthly trends
  quarterly  — monthly summaries → strategic shifts
  annual     — quarterly summaries → paradigm-level review

Each higher-level report is built exclusively from summaries of the previous
level; raw articles are never re-read beyond the weekly stage.

Usage:
    python analyzer.py [weekly|monthly|quarterly|annual]
    (default: weekly)

Environment variables required (loaded via python-dotenv):
    ANTHROPIC_API_KEY  — Anthropic API key.
    GMAIL_USER         — Gmail address used as the SMTP login and From address.
    GMAIL_APP_PASSWORD — 16-character Gmail App Password (spaces are stripped).
"""

import logging
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import anthropic
import markdown as _markdown
import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

from db import (
    get_articles_last_7_days,
    get_report_summaries,
    init_db,
    save_report_summary,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# System prompts — one per report level
# ===========================================================================

_SYS_WEEKLY = """\
You are a Regulatory Intelligence Analyst. You will receive a batch of \
compliance and fintech news headlines with summaries from the past 7 days. \
Your task is to identify patterns, rising trends, and weak signals — not to \
analyze individual articles, but to see the shape of the week. Respond in Russian.\
"""

_SYS_MONTHLY = """\
You are a Senior Regulatory Intelligence Analyst. You will receive summaries \
of weekly compliance and fintech news reports for the past month. Your task is \
to identify what changed over the month, which trends are consolidating, and \
what the operational implications are. Respond in Russian.\
"""

_SYS_QUARTERLY = """\
You are a Chief Regulatory Strategist. You will receive monthly intelligence \
reports for an entire quarter. Your task is to identify structural shifts, \
regulatory momentum at a jurisdictional level, and strategic implications for \
a high-risk fintech operating across multiple jurisdictions. Respond in Russian.\
"""

_SYS_ANNUAL = """\
You are a Global Regulatory Affairs Director preparing an annual briefing for \
the board of a high-risk fintech company. You will receive quarterly intelligence \
reports for the full year. Your task is to identify what fundamentally changed in \
the global regulatory environment, what new paradigms emerged, and what the \
strategic positioning implications are for the next 1-3 years. Respond in Russian.\
"""


# ===========================================================================
# Analysis frameworks — appended verbatim to each user message
# ===========================================================================

_FRAMEWORK_WEEKLY = """\
Analyze the above articles and produce a structured report with these sections:

1. КАРТА MOMENTUM
For each topic that appeared this week:
- Топик / Кол-во статей / Динамика (Растёт / Стабильно / Снижается) / Сила сигнала (Сильный / Слабый)
- Одна строка: что движется в этой теме

2. РАДАР ЮРИСДИКЦИЙ
Which regulators or jurisdictions appeared most frequently or unexpectedly this week? \
Flag anything new, surprising, or disproportionately active.

3. СЛАБЫЕ СИГНАЛЫ
2-4 themes that appeared only 1-2 times but may indicate an emerging trend. \
Mark each: "(низкая уверенность)"

4. СКВОЗНЫЕ ТРЕДЫ
Are there 2-3 separate stories that are actually connected (different jurisdictions \
moving on the same issue)? Describe the thread and its significance.

5. EXECUTIVE BRIEF
3-5 sentences for a Chief Compliance Officer with 60 seconds. \
What actually mattered this week and why?

6. ВОПРОСЫ НЕДЕЛИ
3 specific questions a compliance team should be discussing this week to \
stress-test current assumptions.

Constraints:
- Do not summarize individual articles — synthesize across all of them.
- Flag all uncertain claims with "(низкая уверенность)"
- If the week was genuinely quiet, say so explicitly rather than manufacturing urgency.\
"""

_FRAMEWORK_MONTHLY = """\
Produce a Monthly Regulatory Intelligence Report with these sections:

1. ГЛАВНЫЕ СОБЫТИЯ МЕСЯЦА
Top 5 most significant regulatory developments this month. For each: what happened, \
which jurisdictions, what is the direct operational implication.

2. ТРЕНДЫ МЕСЯЦА
Which topics accelerated, stabilized, or faded compared to previous weeks? \
Show the direction of travel, not just the current state.

3. ЮРИСДИКЦИОННЫЙ ПРОФИЛЬ
Which regulators were most active this month? Any surprising entrants or notable silences?

4. ОПЕРАЦИОННЫЕ ПРИОРИТЕТЫ
Top 3 compliance actions a high-risk fintech should be reviewing based on this \
month's news. Be specific.

5. ПРОГНОЗ НА СЛЕДУЮЩИЙ МЕСЯЦ
Based on current signals, what regulatory developments are most likely in the next \
30 days? Mark all items "(низкая уверенность)" — these are signals, not certainties.

6. EXECUTIVE SUMMARY
5 sentences maximum. Written for a board-level report.

Constraints: Synthesize across weeks, do not repeat individual stories. \
Flag uncertain claims. If a month was quiet, say so.\
"""

_FRAMEWORK_QUARTERLY = """\
Produce a Quarterly Regulatory Strategy Report with these sections:

1. СТРУКТУРНЫЕ СДВИГИ КВАРТАЛА
What fundamentally changed in the regulatory landscape this quarter? \
Distinguish between noise (enforcement actions) and signal (new frameworks, paradigm shifts).

2. РЕГУЛЯТОРНЫЙ MOMENTUM ПО ЮРИСДИКЦИЯМ
For each major jurisdiction active this quarter: direction of travel \
(tightening / loosening / restructuring), key developments, outlook for next quarter.

3. МЕЖЮРИСДИКЦИОННЫЕ ПАТТЕРНЫ
Where are multiple regulators moving in the same direction? \
What global coordination is visible (FATF, IOSCO, FSB-driven)?

4. СТРАТЕГИЧЕСКИЕ РИСКИ
Top 3 risks that a high-risk fintech board should be aware of entering next quarter. \
For each: probability direction (increasing/stable/decreasing), potential impact, \
suggested strategic response.

5. ВОЗМОЖНОСТИ
Are there regulatory developments that create competitive advantages (new licensing \
regimes, clarifying guidance, reduced uncertainty in key markets)?

6. ПРОГНОЗ НА СЛЕДУЮЩИЙ КВАРТАЛ
Key regulatory events expected next quarter (consultations closing, legislation \
scheduled, enforcement cycles). Mark all "(низкая уверенность)".

7. BOARD SUMMARY
7 sentences maximum. Strategic framing only — no operational detail.

Constraints: Think in quarters, not weeks. Avoid repeating monthly details. \
Elevate to strategic implications only.\
"""

_FRAMEWORK_ANNUAL = """\
Produce an Annual Regulatory Intelligence Report with these sections:

1. ГОД В ТРЁХ ПРЕДЛОЖЕНИЯХ
The single most important paragraph of this report. \
What was the defining regulatory story of the year?

2. СМЕНА ПАРАДИГМ
What regulatory frameworks, assumptions, or equilibria that existed at the start \
of the year no longer exist at the end? What replaced them?

3. ЮРИСДИКЦИОННАЯ КАРТА ГОДА
Winners (jurisdictions that became more attractive for high-risk fintech), \
Losers (jurisdictions that tightened significantly), \
Wildcards (jurisdictions with unpredictable trajectory).

4. ТЕМЫ КОТОРЫЕ РОДИЛИСЬ В ЭТОМ ГОДУ
New regulatory topics that did not exist or were marginal at the start of the year \
and are now mainstream.

5. ТЕМЫ КОТОРЫЕ УМЕРЛИ ИЛИ УГАСЛИ
Topics that dominated previous years but lost momentum.

6. СТРАТЕГИЧЕСКИЕ ИМПЛИКАЦИИ — 1 ГОД
Top 3 priorities for the compliance function in the next 12 months based on \
this year's trajectory.

7. СТРАТЕГИЧЕСКИЕ ИМПЛИКАЦИИ — 3 ГОДА
What structural regulatory trends, if they continue, will most reshape the \
high-risk fintech landscape by 2028-2029?

8. BOARD BRIEFING
One page maximum (10 sentences). Written for non-technical board members. \
No jargon. Strategic framing only.

Constraints: Think in years, not quarters. Only structural, permanent, or \
paradigm-level changes belong here. Operational details do not.\
"""


# ===========================================================================
# Per-level configuration (populated after functions are defined)
# ===========================================================================

# Maps period_type → config dict; _PERIOD_CONFIG is finalised at module bottom.
_PERIOD_CONFIG: dict[str, dict] = {}


# ===========================================================================
# Public prompt builders
# ===========================================================================

def build_momentum_prompt(articles: list[dict]) -> str:
    """Construct the weekly user message from raw article dicts.

    One line per article:
        [N] Topic: {topic_ru} | Source: {source_name} | Title: {title} | Summary: {summary_ru}
    Followed by _FRAMEWORK_WEEKLY verbatim.

    Args:
        articles: List of enriched article dicts (title, topic_ru,
                  source_name, summary_ru).

    Returns:
        Formatted user message string ready for the API.
    """
    lines: list[str] = []
    for idx, a in enumerate(articles, start=1):
        topic = (a.get("topic_ru") or "Прочее").strip()
        source = (a.get("source_name") or "").strip()
        title = (a.get("title") or "").strip()
        summary = (a.get("summary_ru") or "").strip()
        lines.append(
            f"[{idx}] Topic: {topic} | Source: {source} | Title: {title} | Summary: {summary}"
        )
    return "\n".join(lines) + "\n\n" + _FRAMEWORK_WEEKLY


def build_monthly_prompt(weekly_summaries: list[dict], month_label: str) -> str:
    """Construct the monthly user message from weekly report summaries.

    One block per weekly summary:
        === Week {N} ({period_label}) ===
        {summary_text}
    Followed by _FRAMEWORK_MONTHLY verbatim.

    Args:
        weekly_summaries: List of report_summaries rows (need 'period_label'
                          and 'summary_text' keys), ordered chronologically.
        month_label:      e.g. '2026-03' — used only for context in the prompt.

    Returns:
        Formatted user message string ready for the API.
    """
    blocks: list[str] = []
    for idx, s in enumerate(weekly_summaries, start=1):
        label = s.get("period_label", f"week-{idx}")
        text = (s.get("summary_text") or "").strip()
        blocks.append(f"=== Week {idx} ({label}) ===\n{text}")
    return "\n\n".join(blocks) + "\n\n" + _FRAMEWORK_MONTHLY


def build_quarterly_prompt(monthly_summaries: list[dict], quarter_label: str) -> str:
    """Construct the quarterly user message from monthly report summaries.

    One block per monthly summary:
        === Month {N} ({period_label}) ===
        {summary_text}
    Followed by _FRAMEWORK_QUARTERLY verbatim.

    Args:
        monthly_summaries: List of report_summaries rows ordered chronologically.
        quarter_label:     e.g. '2026-Q1'.

    Returns:
        Formatted user message string ready for the API.
    """
    blocks: list[str] = []
    for idx, s in enumerate(monthly_summaries, start=1):
        label = s.get("period_label", f"month-{idx}")
        text = (s.get("summary_text") or "").strip()
        blocks.append(f"=== Month {idx} ({label}) ===\n{text}")
    return "\n\n".join(blocks) + "\n\n" + _FRAMEWORK_QUARTERLY


def build_annual_prompt(quarterly_summaries: list[dict], year_label: str) -> str:
    """Construct the annual user message from quarterly report summaries.

    One block per quarterly summary:
        === Quarter {N} ({period_label}) ===
        {summary_text}
    Followed by _FRAMEWORK_ANNUAL verbatim.

    Args:
        quarterly_summaries: List of report_summaries rows ordered chronologically.
        year_label:          e.g. '2026'.

    Returns:
        Formatted user message string ready for the API.
    """
    blocks: list[str] = []
    for idx, s in enumerate(quarterly_summaries, start=1):
        label = s.get("period_label", f"quarter-{idx}")
        text = (s.get("summary_text") or "").strip()
        blocks.append(f"=== Quarter {idx} ({label}) ===\n{text}")
    return "\n\n".join(blocks) + "\n\n" + _FRAMEWORK_ANNUAL


# ===========================================================================
# Public runners
# ===========================================================================

def run_weekly_analysis() -> None:
    """Run the full weekly momentum analysis pipeline.

    Steps:
        1. Load config + .env
        2. Init DB and fetch articles from the last 7 days
        3. Bail out if fewer than 10 articles
        4. Call Claude Haiku with the momentum prompt (single call, max_tokens=4096)
        5. Determine date range + ISO week label
        6. Build HTML email
        7. Send via Gmail SMTP
        8. Save LLM output to report_summaries (period_type='weekly')
        9. Save HTML archive to data/momentum_YYYY-MM-DD.html
       10. Log summary
    """
    _setup_logging()
    logger.info("=== Weekly Momentum Analysis started ===")

    # ── 1. Load config ───────────────────────────────────────────────────────
    config = _load_config()
    db_path = config["database"]["path"]
    email_config = config["email"]
    model = config["llm"].get("model", "claude-haiku-4-5-20251001")

    # ── 2. Init DB + fetch articles ──────────────────────────────────────────
    init_db(db_path)
    articles = get_articles_last_7_days(db_path)
    logger.info("Found %d article(s) in the last 7 days.", len(articles))

    # ── 3. Guard: need at least 10 articles ──────────────────────────────────
    if len(articles) < 10:
        logger.warning(
            "Not enough articles for weekly analysis (found %d, need ≥10).",
            len(articles),
        )
        return

    # ── 4. Call LLM ──────────────────────────────────────────────────────────
    logger.info("Calling LLM (%s) for momentum analysis…", model)
    user_prompt = build_momentum_prompt(articles)
    analysis_text, usage = _call_llm_raw(_SYS_WEEKLY, user_prompt, model)
    logger.info(
        "LLM usage — input: %d tokens, output: %d tokens.",
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
    )

    # ── 5. Determine date range + ISO week label ──────────────────────────────
    now = datetime.now()
    date_to = now.strftime("%d.%m.%Y")
    date_from = (now - timedelta(days=7)).strftime("%d.%m.%Y")
    iso = now.isocalendar()
    week_label = f"{iso[0]}-W{iso[1]:02d}"

    # ── 6. Build HTML ─────────────────────────────────────────────────────────
    html = _build_momentum_html(analysis_text, date_from, date_to, len(articles))
    logger.info("Momentum HTML built (%d bytes).", len(html))

    # ── 7. Send email ─────────────────────────────────────────────────────────
    subject = f"Momentum Report — {date_from} – {date_to}"
    _send_email(html, subject, email_config)
    logger.info("Momentum email sent: %s", subject)

    # ── 8. Save to report_summaries ───────────────────────────────────────────
    save_report_summary(db_path, "weekly", week_label, analysis_text)
    logger.info("Weekly summary saved to report_summaries (label: %s).", week_label)

    # ── 9. Save HTML archive ──────────────────────────────────────────────────
    archive_path = Path("data") / f"momentum_{now.strftime('%Y-%m-%d')}.html"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(html, encoding="utf-8")
    logger.info("HTML archive saved → %s", archive_path.resolve())

    logger.info(
        "=== Weekly Momentum Analysis complete — %d articles, "
        "%d in / %d out tokens ===",
        len(articles),
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
    )


def run_periodic_analysis(period_type: str) -> None:
    """Unified runner for monthly, quarterly, and annual reports.

    Each report is built entirely from LLM summaries of the previous level
    stored in report_summaries — never from raw articles.

    Minimum source summaries required:
        monthly:   ≥3 weekly summaries
        quarterly: ≥2 monthly summaries
        annual:    ≥3 quarterly summaries

    Args:
        period_type: One of 'monthly', 'quarterly', 'annual'.
    """
    if period_type not in _PERIOD_CONFIG:
        logger.error(
            "Unknown period_type '%s'. Valid values: monthly, quarterly, annual.",
            period_type,
        )
        return

    _setup_logging()
    logger.info("=== %s Analysis started ===", period_type.capitalize())

    # ── 1. Load config ────────────────────────────────────────────────────────
    config = _load_config()
    db_path = config["database"]["path"]
    email_config = config["email"]
    cfg = _PERIOD_CONFIG[period_type]

    # Model selection: weekly uses Haiku (cheap, fast); all higher-level reports
    # use Sonnet for richer synthesis.  The haiku model ID falls back to config
    # so it stays in one place, but the Sonnet upgrade is intentionally hard-coded
    # here — it must not be downgraded by a config change.
    _SONNET = "claude-sonnet-4-6"
    _PERIOD_MODELS: dict[str, tuple[str, int]] = {
        # period_type → (model_id, max_tokens)
        "monthly":   (_SONNET, 8192),
        "quarterly": (_SONNET, 8192),
        "annual":    (_SONNET, 8192),
    }
    haiku_model = config["llm"].get("model", "claude-haiku-4-5-20251001")
    model, max_tokens = _PERIOD_MODELS.get(period_type, (haiku_model, 4096))

    init_db(db_path)

    # ── 2. Calculate period bounds ────────────────────────────────────────────
    now = datetime.now()
    since_date, period_label = _calculate_period(period_type, now)
    since_str = since_date.strftime("%Y-%m-%d %H:%M:%S")

    # ── 3. Fetch source summaries ─────────────────────────────────────────────
    source_type = cfg["source_type"]
    summaries = get_report_summaries(db_path, source_type, since_str)
    logger.info(
        "Found %d %s summary(ies) since %s.",
        len(summaries), source_type, since_date.strftime("%Y-%m-%d"),
    )

    # ── 4. Guard: minimum source count ───────────────────────────────────────
    min_count = cfg["min_summaries"]
    if len(summaries) < min_count:
        logger.warning(
            "Not enough %s summaries for %s analysis (found %d, need ≥%d).",
            source_type, period_type, len(summaries), min_count,
        )
        return

    # ── 5. Call LLM ───────────────────────────────────────────────────────────
    logger.info(
        "Calling LLM (%s) for %s analysis (%d source summaries)…",
        model, period_type, len(summaries),
    )
    user_prompt = cfg["build_fn"](summaries, period_label)
    system_prompt = cfg["system_prompt"]
    analysis_text, usage = _call_llm_raw(system_prompt, user_prompt, model, max_tokens)
    logger.info(
        "LLM usage — input: %d tokens, output: %d tokens.",
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
    )

    # ── 6. Save to report_summaries ───────────────────────────────────────────
    save_report_summary(db_path, period_type, period_label, analysis_text)
    logger.info(
        "%s summary saved to report_summaries (label: %s).",
        period_type.capitalize(), period_label,
    )

    # ── 7. Build HTML ─────────────────────────────────────────────────────────
    html = _build_periodic_html(analysis_text, period_type, period_label, len(summaries))
    logger.info("%s HTML built (%d bytes).", period_type.capitalize(), len(html))

    # ── 8. Send email ─────────────────────────────────────────────────────────
    subject = f"{cfg['subject_prefix']} — {period_label}"
    _send_email(html, subject, email_config)
    logger.info("Email sent: %s", subject)

    # ── 9. Save HTML archive ──────────────────────────────────────────────────
    archive_name = f"{cfg['archive_prefix']}_{now.strftime('%Y-%m-%d')}.html"
    archive_path = Path("data") / archive_name
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(html, encoding="utf-8")
    logger.info("HTML archive saved → %s", archive_path.resolve())

    logger.info(
        "=== %s Analysis complete — %d source summaries, %d in / %d out tokens ===",
        period_type.capitalize(),
        len(summaries),
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
    )


# ===========================================================================
# Internal: LLM
# ===========================================================================

def _call_llm_raw(
    system: str, user: str, model: str, max_tokens: int = 4096
) -> tuple[str, dict]:
    """Generic single API call.

    Args:
        system:     System prompt string.
        user:       User message string.
        model:      Anthropic model ID.
        max_tokens: Maximum output tokens (default 4096; use 8192 for Sonnet
                    calls where reports are longer and richer).

    Returns:
        Tuple of (response_text, usage_dict).
        usage_dict has keys 'input_tokens' and 'output_tokens'.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = message.content[0].text
    usage = {
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }
    return text, usage


# ===========================================================================
# Internal: markdown → HTML
# ===========================================================================

def _md_to_html(text: str) -> str:
    return _markdown.markdown(text, extensions=["tables", "nl2br"])


_MD_STYLES = """\
<style>
.md-body h1,.md-body h2{color:#0f6aad;margin-top:24px;margin-bottom:8px;}
.md-body h2{font-size:17px;border-bottom:1px solid #e0e0e0;padding-bottom:4px;}
.md-body h3{font-size:15px;color:#333;margin-top:16px;}
.md-body table{border-collapse:collapse;width:100%;margin:12px 0;}
.md-body th{background:#0f6aad;color:#fff;padding:8px 12px;text-align:left;}
.md-body td{padding:7px 12px;border-bottom:1px solid #eee;}
.md-body tr:nth-child(even) td{background:#f8f9fa;}
.md-body strong{color:#111;}
.md-body blockquote{border-left:3px solid #0f6aad;margin:12px 0;padding:8px 16px;
  background:#f0f7ff;color:#555;}
.md-body hr{border:none;border-top:1px solid #e0e0e0;margin:20px 0;}
.md-body ul,.md-body ol{padding-left:20px;}
.md-body li{margin-bottom:4px;}
</style>"""


# ===========================================================================
# Internal: HTML builders
# ===========================================================================

def _build_momentum_html(
    analysis_text: str,
    date_from: str,
    date_to: str,
    article_count: int,
) -> str:
    """Build self-contained HTML for the weekly momentum report.

    Args:
        analysis_text: Raw LLM output.
        date_from:     Start of the analysis window (DD.MM.YYYY).
        date_to:       End of the analysis window (DD.MM.YYYY).
        article_count: Number of raw articles analysed.

    Returns:
        Full HTML document string.
    """
    accent = "#0f6aad"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title_text = f"Momentum Report — неделя {date_from} – {date_to}"

    converted_html = _md_to_html(analysis_text)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(title_text)}</title>
  {_MD_STYLES}
</head>
<body style="margin:0;padding:0;background:#f0f4f8;
             font-family:Arial,Helvetica,sans-serif;color:#202124;
             font-size:15px;line-height:1.6;">
  <div style="max-width:740px;margin:0 auto;background:#fff;
              box-shadow:0 1px 4px rgba(0,0,0,.12);">
    <div style="background:{accent};padding:28px 32px 22px;">
      <h1 style="margin:0;color:#fff;font-size:24px;font-weight:700;">
        Momentum Report
      </h1>
      <p style="margin:6px 0 0;color:rgba(255,255,255,.85);font-size:14px;">
        неделя {escape(date_from)} – {escape(date_to)}
        &nbsp;·&nbsp; {article_count} статей проанализировано
      </p>
    </div>
    <div style="padding:28px 32px 32px;">
      <div style="background:#f8f9fa;border-left:4px solid {accent};
                  border-radius:0 6px 6px 0;padding:20px 24px;">
        <div class="md-body" style="font-size:14px;line-height:1.75;color:#303030;">
          {converted_html}
        </div>
      </div>
    </div>
    <div style="background:#f8f9fa;border-top:1px solid #e0e0e0;
                padding:16px 32px;font-size:12px;color:#888;">
      <p style="margin:0;">
        Сформировано: {escape(generated_at)}
        &nbsp;·&nbsp; Статей в выборке: {article_count}
      </p>
    </div>
  </div>
</body>
</html>"""


def _build_periodic_html(
    analysis_text: str,
    period_type: str,
    period_label: str,
    source_count: int,
) -> str:
    """Build self-contained HTML for monthly, quarterly, or annual reports.

    Uses a distinct accent colour per level so reports are visually
    distinguishable at a glance.

    Args:
        analysis_text: Raw LLM output.
        period_type:   'monthly', 'quarterly', or 'annual'.
        period_label:  e.g. '2026-03', '2026-Q1', '2026'.
        source_count:  Number of lower-level summaries used as input.

    Returns:
        Full HTML document string.
    """
    cfg = _PERIOD_CONFIG[period_type]
    accent = cfg["accent"]
    title = cfg["title"]
    source_label_ru = cfg["source_label_ru"]
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    converted_html = _md_to_html(analysis_text)
    accent_override = f"<style>.md-body h1,.md-body h2{{color:{accent};}} .md-body th{{background:{accent};}}</style>"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(title)} — {escape(period_label)}</title>
  {_MD_STYLES}
  {accent_override}
</head>
<body style="margin:0;padding:0;background:#f0f4f8;
             font-family:Arial,Helvetica,sans-serif;color:#202124;
             font-size:15px;line-height:1.6;">
  <div style="max-width:740px;margin:0 auto;background:#fff;
              box-shadow:0 1px 4px rgba(0,0,0,.12);">
    <div style="background:{accent};padding:28px 32px 22px;">
      <h1 style="margin:0;color:#fff;font-size:24px;font-weight:700;">
        {escape(title)}
      </h1>
      <p style="margin:6px 0 0;color:rgba(255,255,255,.85);font-size:14px;">
        {escape(period_label)}
        &nbsp;·&nbsp; {source_count} {escape(source_label_ru)}
      </p>
    </div>
    <div style="padding:28px 32px 32px;">
      <div style="background:#f8f9fa;border-left:4px solid {accent};
                  border-radius:0 6px 6px 0;padding:20px 24px;">
        <div class="md-body" style="font-size:14px;line-height:1.75;color:#303030;">
          {converted_html}
        </div>
      </div>
    </div>
    <div style="background:#f8f9fa;border-top:1px solid #e0e0e0;
                padding:16px 32px;font-size:12px;color:#888;">
      <p style="margin:0;">
        Сформировано: {escape(generated_at)}
        &nbsp;·&nbsp; Использовано отчётов: {source_count}
      </p>
    </div>
  </div>
</body>
</html>"""


# ===========================================================================
# Internal: period calculation
# ===========================================================================

def _calculate_period(period_type: str, now: datetime) -> tuple[datetime, str]:
    """Return (since_date, period_label) for the current calendar period.

    since_date is the first moment of the current period (month / quarter /
    year) at midnight, so get_report_summaries() returns everything created
    within the current period.

    Args:
        period_type: 'monthly', 'quarterly', or 'annual'.
        now:         Reference datetime (typically datetime.now()).

    Returns:
        Tuple of (since_date as datetime, period_label as str).
    """
    if period_type == "monthly":
        since_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_label = now.strftime("%Y-%m")

    elif period_type == "quarterly":
        q = (now.month - 1) // 3 + 1
        quarter_start_month = (q - 1) * 3 + 1
        since_date = now.replace(
            month=quarter_start_month, day=1,
            hour=0, minute=0, second=0, microsecond=0,
        )
        period_label = f"{now.year}-Q{q}"

    else:  # annual
        since_date = now.replace(
            month=1, day=1,
            hour=0, minute=0, second=0, microsecond=0,
        )
        period_label = str(now.year)

    return since_date, period_label


# ===========================================================================
# Internal: email sender
# ===========================================================================

def _send_email(html: str, subject: str, email_config: dict) -> None:
    """Send an HTML report via Gmail SMTP.

    Args:
        html:         Complete HTML string to send.
        subject:      Email subject line.
        email_config: The 'email' section from config.yaml.
    """
    gmail_user = os.environ.get("GMAIL_USER") or email_config.get("smtp_user", "")
    app_password = (
        os.environ.get("GMAIL_APP_PASSWORD") or email_config.get("smtp_password", "")
    )
    app_password = app_password.replace(" ", "")

    recipient = email_config["to"]
    from_name = email_config.get("from_name", "Compliance Digest Bot")
    smtp_server = email_config.get("smtp_server", "smtp.gmail.com")
    smtp_port = int(email_config.get("smtp_port", 587))

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{gmail_user}>"
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(gmail_user, app_password)
        server.sendmail(gmail_user, [recipient], msg.as_bytes())


# ===========================================================================
# Internal: config + logging
# ===========================================================================

def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _setup_logging() -> None:
    # Guard: only configure if no handlers have been added yet (idempotent).
    if logging.getLogger().handlers:
        return
    log_path = Path("data/digest.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_path, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


# ===========================================================================
# Period configuration — defined here so it can reference the prompt builders
# ===========================================================================

_PERIOD_CONFIG = {
    "monthly": {
        "source_type":    "weekly",
        "min_summaries":  3,
        "build_fn":       build_monthly_prompt,
        "system_prompt":  _SYS_MONTHLY,
        "subject_prefix": "Monthly Compliance Report",
        "archive_prefix": "monthly",
        "title":          "Monthly Compliance Report",
        "accent":         "#0d7a3e",
        "source_label_ru": "недельных отчётов",
    },
    "quarterly": {
        "source_type":    "monthly",
        "min_summaries":  2,
        "build_fn":       build_quarterly_prompt,
        "system_prompt":  _SYS_QUARTERLY,
        "subject_prefix": "Quarterly Regulatory Report",
        "archive_prefix": "quarterly",
        "title":          "Quarterly Regulatory Report",
        "accent":         "#7c3aed",
        "source_label_ru": "месячных отчётов",
    },
    "annual": {
        "source_type":    "quarterly",
        "min_summaries":  3,
        "build_fn":       build_annual_prompt,
        "system_prompt":  _SYS_ANNUAL,
        "subject_prefix": "Annual Regulatory Intelligence Report",
        "archive_prefix": "annual",
        "title":          "Annual Regulatory Intelligence Report",
        "accent":         "#b91c1c",
        "source_label_ru": "квартальных отчётов",
    },
}


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "weekly"
    if mode == "weekly":
        run_weekly_analysis()
    else:
        run_periodic_analysis(mode)
