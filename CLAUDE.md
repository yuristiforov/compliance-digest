# Compliance Digest — Project Briefing

This document is a briefing for Claude Code sessions working on this project.
Read it fully before making any changes.

---

## 1. Project Overview

**Compliance Digest** is a daily automated news digest agent for the compliance,
fintech, and crypto industries. It collects articles from 7 RSS/scrape sources,
classifies and summarises each article in Russian using Claude Haiku, builds a
self-contained HTML email, and delivers it via Gmail SMTP.

- **Single user:** stifor96@gmail.com
- **Production server:** Hetzner VPS — `/opt/compliance-digest`
- **Local dev (Windows):** `C:\Users\stifo\compliance-digest`
- **Schedule:** 06:00 UTC daily (= 08:00 Belgrade time)

---

## 2. Architecture

The pipeline runs sequentially in `main.py`:

```
collector.py  →  processor.py  →  emailer.py
     ↑                                 ↓
   db.py (dedup filter)          db.py (mark_seen)
```

**Stages:**

1. **collect** — `fetch_all_sources()` iterates all enabled sources in `config.yaml`.
   Each source is routed by `method: rss` or `method: scrape`. Articles already in
   the `seen_urls` SQLite table are skipped before any HTTP request is made.
2. **process** — `process_articles()` sends articles to Claude Haiku in batches of
   20 (auto-calculated as `max_tokens // 200` to prevent JSON truncation). Each
   article gets a Russian topic label (`topic_ru`) and a 2-3 sentence Russian
   summary (`summary_ru`).
3. **email** — `build_html()` assembles a self-contained HTML email with a ToC,
   per-topic sections, and article cards. `send_digest()` delivers it via Gmail
   SMTP with STARTTLS.
4. **dedup** — `mark_seen()` writes all sent article URLs to `seen_urls` **only
   after** a successful email send. If the send fails, nothing is marked seen and
   the run will retry on the next cron invocation.

**Collection strategy (cascade):**
- Try RSS first for every source.
- For sources where RSS is unavailable or blocked, use a custom scraper
  (`method: scrape`) dispatched by the `scraper:` field in `config.yaml`.

**LLM — daily digest (`processor.py`):**
- Model: `claude-haiku-4-5-20251001` (fast, cheap; set in `config.yaml`)
- Batch size: `min(max_articles_per_batch, max_tokens_per_call // 200)` → 20 articles/batch at 4096 tokens
- System prompt: returns a JSON array with `id`, `topic_ru`, `summary_ru` per article
- 8 allowed topics (Russian): AML и санкции, Платежи, Крипто и Web3, Forex и CFD, iGaming, Конфиденциальность и данные, RegTech, Прочее

**LLM — analysis reports (`analyzer.py`):**
- **Weekly** → `claude-haiku-4-5-20251001`, `max_tokens=4096` (model from `config.yaml`)
- **Monthly / Quarterly / Annual** → `claude-sonnet-4-6`, `max_tokens=8192`
  - Sonnet is hard-coded in `run_periodic_analysis()` for these tiers — it must not
    be downgraded by a `config.yaml` change. Higher-level reports require richer
    cross-period synthesis that benefits from a more capable model.
  - `max_tokens=8192` accommodates longer structured outputs (up to 8 sections).

**Deduplication:**
- SQLite table `seen_urls` (columns: `url TEXT PRIMARY KEY`, `source_name`, `seen_at`)
- DB path: `./data/digest.db` (relative to working directory)
- `is_seen()` is called per-article during collection to skip already-processed URLs
- `mark_seen()` is called once after successful email delivery; also writes to `articles`

**Intelligence database (`db.py`):**
- `articles` table — enriched article store (url, title, source_name, topic_ru, summary_ru, seen_at). Populated by `mark_seen()` alongside `seen_urls`. Used by `analyzer.py` for weekly analysis.
- `report_summaries` table — hierarchical LLM output store (id, period_type, period_label, summary_text, created_at). Weekly reports save here so monthly reports can aggregate them; monthly feeds quarterly; quarterly feeds annual. Period labels: `YYYY-Www` / `YYYY-MM` / `YYYY-Qn` / `YYYY`.

---

## 3. Sources (current)

| Source | Method | Notes |
|---|---|---|
| **Finextra** | `rss` | URL must be `headlines.aspx` — `headlines.asp` returns a 404 |
| **The Paypers** | `scrape/paypers` | Nuxt SSR SPA. Data embedded as a JSON array in an inline `<script>` tag; integer values are index references into the same array. Custom `resolve()` function dereferences the structure. No RSS available. |
| **CoinDesk** | `rss` | Standard. No quirks. |
| **Finance Magnates** | `rss` | Standard. No quirks. |
| **Gambling Insider** | `rss` | URL must be `/feed` — `/news/feed` returns a 404 |
| **FinTech Global** | `rss` | Standard. No quirks. |
| **IAPP** | `scrape/iapp` | Next.js SPA, no RSS. Strategy: parse `sitemap.xml` (8 000+ URLs with `lastmod`), filter by recency and `is_seen()`, then parallel-fetch `og:title`/`og:description` via `ThreadPoolExecutor(max_workers=5)`. May time out from server — non-fatal, returns empty list and pipeline continues. |
| **ACAMS Today** | **DISABLED** | `acamstoday.org` migrated to `acams.org` (Drupal 11 SPA). All feed endpoints 404 or redirect. Requires headless browser — not implemented. Set `enabled: false` in `config.yaml`. |

---

## 4. File Structure

```
compliance-digest/
├── main.py              # Production entry point; orchestrates the full pipeline
├── collector.py         # Article collection: RSS fetching + custom scrapers
├── processor.py         # LLM enrichment via Anthropic API (topic + summary)
├── emailer.py           # HTML digest builder + Gmail SMTP sender
├── analyzer.py          # Hierarchical analysis: weekly/monthly/quarterly/annual reports
├── db.py                # SQLite helpers: init_db, is_seen, mark_seen, get_articles_last_7_days,
│                        #   save_report_summary, get_report_summaries
├── config.yaml          # All non-secret configuration (sources, email, LLM, DB)
├── .env                 # Secrets — never committed to git
├── .env.example         # Template for .env (no real values)
├── requirements.txt     # Python dependencies
├── .gitignore           # Excludes .env, data/*.db, __pycache__, etc.
├── deploy.sh            # Local script: rsync project files to the server
├── setup.sh             # Server script: install deps, create venv (run once)
├── run.sh               # Cron entrypoint: activate venv + python main.py
├── run_weekly.sh        # Cron: Fridays 07:00 UTC — python analyzer.py weekly
├── run_monthly.sh       # Cron: 1st of month 08:00 UTC — python analyzer.py monthly
├── run_quarterly.sh     # Cron: 1st of Jan/Apr/Jul/Oct 09:00 UTC — python analyzer.py quarterly
├── run_annual.sh        # Cron: 1st Jan 10:00 UTC — python analyzer.py annual
├── test_collector.py    # Smoke test: collect only, print per-source counts
├── test_processor.py    # Smoke test: collect + LLM on first 10 articles
├── test_emailer.py      # Full pipeline test: collect → process → HTML → send
├── test_analyzer.py     # Smoke test: weekly momentum (articles → LLM → HTML → browser)
├── test_analyzer_periodic.py  # Smoke test: monthly report from seeded weekly summaries
└── data/                # Runtime output directory (gitignored)
    ├── digest.db        # SQLite database (seen_urls + articles + report_summaries tables)
    ├── digest.log       # Append-mode run log (all pipeline scripts)
    ├── digest_YYYY-MM-DD.html    # HTML archive of each sent daily digest
    ├── momentum_YYYY-MM-DD.html  # Weekly momentum report archive
    ├── monthly_YYYY-MM-DD.html   # Monthly report archive
    ├── quarterly_YYYY-MM-DD.html # Quarterly report archive
    └── annual_YYYY-MM-DD.html    # Annual report archive
```

---

## 5. Configuration

### `config.yaml`
All non-secret settings live here. Key sections:

- `sources` — list of source configs; each has `name`, `url`, `method`, `enabled`,
  and optionally `scraper`
- `email` — recipient, sender name, SMTP server/port, subject template
- `schedule.time_utc` — informational only; the actual schedule is set in crontab
- `digest.lookback_hours` — articles older than this are skipped (default: 24)
- `digest.max_articles_per_batch` — upper bound for LLM batch size (default: 100;
  effective batch is further capped by `max_tokens // 200`)
- `llm.model` — Anthropic model ID
- `llm.max_tokens_per_call` — max output tokens per LLM call (default: 4096)
- `database.path` — SQLite DB path, relative to working directory

### `.env`
Never committed. Must be created manually on each machine / server.

```
ANTHROPIC_API_KEY=sk-ant-api03-...
GMAIL_USER=stifor96@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

Both `processor.py` and `main.py` call `load_dotenv(override=True)` at import
time. The `override=True` is intentional — see Known Issues.

---

## 6. Deployment

### Server details
- **Host:** Hetzner VPS, `46.225.3.166`
- **Path:** `/opt/compliance-digest`
- **User:** `root`
- **Python:** system Python 3 via virtualenv at `/opt/compliance-digest/venv`

### Other projects on this server — do not touch
```
airbot
compliance-radar
complit-bot
kyb-agent
tgscrapper
```

### Cron jobs
All five jobs append to the same `digest.log`. Add all lines to the server crontab
(`crontab -e` as root on the server):

```
# Daily digest — 06:00 UTC = 08:00 Belgrade
0 6 * * *     /bin/bash /root/compliance-digest/run.sh >> /root/compliance-digest/data/digest.log 2>&1

# Weekly momentum report — Fridays, 07:00 UTC = 09:00 Belgrade
0 7 * * 5     /bin/bash /root/compliance-digest/run_weekly.sh >> /root/compliance-digest/data/digest.log 2>&1

# Monthly report — 1st of each month, 08:00 UTC
0 8 1 * *     /bin/bash /root/compliance-digest/run_monthly.sh >> /root/compliance-digest/data/digest.log 2>&1

# Quarterly report — 1st of Jan/Apr/Jul/Oct, 09:00 UTC
0 9 1 1,4,7,10 * /bin/bash /root/compliance-digest/run_quarterly.sh >> /root/compliance-digest/data/digest.log 2>&1

# Annual report — 1st January, 10:00 UTC
0 10 1 1 *    /bin/bash /root/compliance-digest/run_annual.sh >> /root/compliance-digest/data/digest.log 2>&1
```

Note: paths use `/root/compliance-digest` on the production server.
06:00 UTC = 08:00 Belgrade (CET+1 / CEST+2 depending on DST).

### Deploying changes
There is no daemon to restart. Cron reads `run.sh` → `main.py` fresh on each run.

```bash
# From local machine:
bash deploy.sh root@46.225.3.166

# Then SSH in and verify if needed:
ssh root@46.225.3.166
tail -50 /opt/compliance-digest/data/digest.log
```

`deploy.sh` excludes `.env`, `data/*.db`, `data/*.html`, `data/*.log`, and `.git`
from the rsync — server secrets and runtime data are never overwritten by a deploy.

---

## 7. Git Workflow

Repository: https://github.com/yuristiforov/compliance-digest (public)

### Deploy changes to server:
1. Make changes locally
2. Commit and push:
   ```bash
   git add .
   git commit -m "description"
   git push
   ```
3. Pull on server (via Claude Code server session or SSH):
   ```bash
   cd /root/compliance-digest && git pull
   ```
4. If `.sh` files were modified, fix line endings:
   ```bash
   sed -i 's/\r//' /root/compliance-digest/*.sh
   ```

### Never commit:
- `.env` (secrets)
- `data/*.db`, `data/*.html`, `data/*.log` (runtime data)
- `__pycache__`, `.claude/`

### After pulling on server — no restart needed:
Cron picks up changes automatically on next scheduled run.
To test immediately:
```bash
cd /root/compliance-digest && source venv/bin/activate && python main.py
```

---

## 8. Known Issues & Fixes

**`load_dotenv(override=True)`**
On Windows, environment variables may be pre-set as empty strings at the system
level. Without `override=True`, `python-dotenv` will not overwrite them, causing
`ANTHROPIC_API_KEY` to be empty. Always use `override=True`.

**LLM batch size cap**
The original config sets `max_articles_per_batch: 100`, but 100 articles at
~200 output tokens each requires ~20 000 tokens — far over the 4096 limit.
This causes truncated JSON and all articles falling back to "Прочее".
Fix: `batch_size = min(digest_batch, max(1, max_tokens // 200))` → 20 articles/batch.
Do not remove this cap.

**`strftime` cross-platform day formatting**
`%-d` (day without leading zero) works on Linux/macOS but raises `ValueError` on
Windows. Fix: `dt.strftime("%d %b %Y").lstrip("0")` or the `_strftime_no_pad()`
helper in `emailer.py`. Do not use `%-d` anywhere in this project.

**IAPP sitemap timeouts**
The IAPP scraper fetches `sitemap.xml` (8 000+ entries) then makes parallel HTTP
requests for og:meta. On slower servers this can time out. The exception is caught
and logged as a warning; the pipeline continues with 0 IAPP articles for that run.
This is expected and non-fatal.

**Unicode on Windows console**
Test scripts call `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at
the top to prevent `UnicodeEncodeError` on Windows cp1251 consoles when printing
Russian text or Unicode symbols. Do not remove this line from test scripts.

**CRLF line endings in `run.sh`**
If `run.sh` is edited or created on Windows, it gets CRLF line endings which cause
bash to fail silently on the server (`cd /root/compliance-digest\r` — directory not
found, venv not activated, nothing written to log).
Fix on server: `sed -i 's/\r//' run.sh`.
Prevention: always save shell scripts with LF endings (in VS Code: bottom-right
corner → CRLF → change to LF).

---

## 9. How to Extend

**Add a new RSS source:**
1. Add a block to `config.yaml` under `sources`:
   ```yaml
   - name: "Source Name"
     url: "https://example.com/feed.xml"
     method: rss
     enabled: true
   ```
2. Test with `python test_collector.py`.

**Add a new scraped source:**
1. Add a block to `config.yaml` with `method: scrape` and a unique `scraper:` value.
2. Add a `_fetch_scrape_<name>(source)` function in `collector.py`.
3. Route it in `fetch_all_sources()` inside the `elif method == "scrape":` block.

**Add or rename a topic:**
Edit the topics list in `_SYSTEM_PROMPT` inside `processor.py`. The list is passed
directly to the LLM; keep it as a Python list literal inside the prompt string.
Also update `_group_by_topic()` in `emailer.py` if "Прочее" is renamed.

**Change the digest HTML layout:**
Edit `build_html()` and `_render_card()` in `emailer.py`. All CSS is inline (required
for email clients). Do not introduce external stylesheets or `<link>` tags.

**Change the LLM model:**
Update `llm.model` in `config.yaml`. If switching to a model with different token
limits, also update `llm.max_tokens_per_call`.

**Change the send time:**
Edit the crontab on the server (`crontab -e`). The `schedule.time_utc` field in
`config.yaml` is informational only and does not drive scheduling.

**Run any analysis report manually:**
```bash
# On the server or local machine (with venv active):
python analyzer.py           # weekly (default)
python analyzer.py weekly    # explicit
python analyzer.py monthly   # monthly from weekly summaries in report_summaries
python analyzer.py quarterly # quarterly from monthly summaries
python analyzer.py annual    # annual from quarterly summaries
```
Each level reads from `report_summaries` rather than raw articles. If there are
not enough source summaries (monthly needs ≥3 weekly, quarterly needs ≥2 monthly,
annual needs ≥3 quarterly), the script exits with a warning and does nothing.

**Hierarchical aggregation chain:**
```
raw articles  →  weekly (saves to report_summaries)
weekly sums   →  monthly (saves to report_summaries)
monthly sums  →  quarterly (saves to report_summaries)
quarterly sums → annual (saves to report_summaries)
```
Each level is a single Claude Haiku call with max_tokens=4096.

**Modify analysis prompts or section frameworks:**
Each report level has two constants at the top of `analyzer.py`:
- `_SYS_WEEKLY` / `_SYS_MONTHLY` / `_SYS_QUARTERLY` / `_SYS_ANNUAL` — system prompts
- `_FRAMEWORK_WEEKLY` / `_FRAMEWORK_MONTHLY` / `_FRAMEWORK_QUARTERLY` / `_FRAMEWORK_ANNUAL`
  — section frameworks appended verbatim to the user message

Edit them directly; section headers are plain text rendered in a `<pre>` block.
Do not change the `_PERIOD_CONFIG` accent colours or subject prefixes without also
updating `_build_periodic_html()` expectations.

**Change report HTML colours or titles per level:**
Edit `_PERIOD_CONFIG` at the bottom of `analyzer.py`. Keys per level:
`accent` (hex), `title`, `source_label_ru`, `subject_prefix`, `archive_prefix`.

---

## 10. Testing

All test scripts live in the project root. Run from the project directory with the
virtualenv active.

| Script | What it does |
|---|---|
| `test_collector.py` | Runs collection only. Prints article count per source with ✓/✗ indicators. Fast — no LLM calls, no email. |
| `test_processor.py` | Runs collection then sends the first 10 articles to the LLM. Prints topic, summary, and token cost per article. |
| `test_emailer.py` | Full pipeline: collect (72h lookback) → process all → build HTML → save to `data/test_digest.html` → send email to `stifor96@gmail.com`. |
| `test_analyzer.py` | Calls `get_articles_last_7_days()`, prints count. If ≥10: runs full momentum analysis, saves HTML to `data/test_momentum.html`, opens in browser. |

**On the server:**
```bash
cd /opt/compliance-digest
source venv/bin/activate
python main.py
tail -50 data/digest.log
```

To test without sending email, comment out the `send_digest()` call in `main.py`
temporarily and check `data/digest_YYYY-MM-DD.html` directly.
