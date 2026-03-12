"""
collector.py — RSS feed fetching and article collection.

Fetches articles from configured RSS sources (and scrape-based sources)
and normalizes them into a consistent dict format for downstream processing.

Methods supported per source config:
  rss    — fetch via requests + feedparser (browser UA avoids most blocks)
  scrape — custom page scraper; dispatch by source['scraper'] value:
             paypers — extracts from The Paypers Nuxt SSR JSON payload
             iapp    — filters IAPP sitemap.xml by lastmod, fetches og:meta
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup

from db import is_seen

logger = logging.getLogger(__name__)

# Single shared session with browser UA — prevents blocks on most feeds.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": BROWSER_UA})

# Maximum articles fetched per IAPP sitemap scan (parallel page requests).
_IAPP_MAX_ARTICLES = 30


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_all_sources(
    sources: list[dict],
    db_path: str,
    lookback_hours: int = 24,
) -> list[dict]:
    """Fetch new, unseen articles from all enabled sources.

    Routes each source to the appropriate fetcher based on its 'method'
    field, then applies age and deduplication filters.

    Args:
        sources: List of source config dicts from config.yaml.
        db_path: Path to the SQLite database used for deduplication.
        lookback_hours: Reject articles older than this many hours.

    Returns:
        List of new article dicts with keys: title, url, snippet,
        source_name, published_at.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    articles: list[dict] = []

    for source in sources:
        if not source.get("enabled", True):
            logger.debug("Skipping disabled source: %s", source.get("name"))
            continue

        name = source.get("name", "<unknown>")
        method = source.get("method", "rss")
        scraper = source.get("scraper", "")

        try:
            if method == "rss":
                entries = _fetch_rss(source)
            elif method == "scrape":
                if scraper == "paypers":
                    entries = _fetch_scrape_paypers(source)
                elif scraper == "iapp":
                    # IAPP scraper pre-filters by cutoff and db to avoid
                    # expensive page fetches for already-seen articles.
                    entries = _fetch_scrape_iapp(source, cutoff, db_path)
                else:
                    logger.warning(
                        "Unknown scraper '%s' for source '%s'; skipping.", scraper, name
                    )
                    continue
            else:
                logger.warning(
                    "Unknown method '%s' for source '%s'; skipping.", method, name
                )
                continue
        except Exception as exc:
            logger.warning("Unhandled error fetching source '%s': %s", name, exc)
            continue

        logger.info("Source '%s': fetched %d raw entries", name, len(entries))

        for article in entries:
            if not article.get("url"):
                logger.debug("Skipping entry with no URL from '%s'", name)
                continue

            # Age filter (skip if article is older than the cutoff).
            published = _parse_date(article.get("published_at", ""))
            if published and published < cutoff:
                logger.debug(
                    "Skipping old article '%s' (published %s)",
                    article.get("title", "")[:60],
                    article.get("published_at"),
                )
                continue

            # Deduplication filter.
            if is_seen(db_path, article["url"]):
                logger.debug("Skipping already-seen URL: %s", article["url"])
                continue

            articles.append(article)

    logger.info("Total new articles collected: %d", len(articles))
    return articles


# ---------------------------------------------------------------------------
# RSS fetcher
# ---------------------------------------------------------------------------

def _fetch_rss(source: dict) -> list[dict]:
    """Fetch an RSS feed via requests (browser UA) + feedparser.

    Using requests instead of feedparser's built-in urllib lets us send a
    browser User-Agent header, which bypasses blocks on most feeds.

    On any network or parse error the exception is caught, a warning is
    logged, and an empty list is returned so the pipeline continues.

    Args:
        source: Source config dict with at least 'name' and 'url'.

    Returns:
        List of normalized article dicts, or [] on failure.
    """
    name = source["name"]
    url = source["url"]
    try:
        resp = _SESSION.get(url, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            logger.warning(
                "Feed '%s' returned a bozo error: %s", name, feed.bozo_exception
            )
            return []
        return [_normalize_entry(e, name) for e in feed.entries]
    except Exception as exc:
        logger.warning("Failed to fetch RSS '%s' (%s): %s", name, url, exc)
        return []


# ---------------------------------------------------------------------------
# Scraper: The Paypers (Nuxt SSR payload)
# ---------------------------------------------------------------------------

def _fetch_scrape_paypers(source: dict) -> list[dict]:
    """Extract articles from The Paypers Nuxt SSR JSON payload.

    The Paypers renders via Nuxt (Vue SSR). The article data for all
    content-overview sections is embedded as a serialized JSON array in
    an inline <script> tag. Each article includes title, slug, domain
    slug (used to build the URL), published_at, and an HTML abstract.

    Args:
        source: Source config dict with 'name' and 'url'.

    Returns:
        List of normalized article dicts, or [] on failure.
    """
    name = source["name"]
    url = source["url"]
    try:
        resp = _SESSION.get(url, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")

        # Locate the inline script that contains the Nuxt SSR state.
        # It is the largest script tag and contains 'content-overview'.
        nuxt_script = None
        for sc in soup.find_all("script"):
            txt = sc.string or ""
            if "content-overview" in txt and len(txt) > 10_000:
                nuxt_script = txt
                break

        if not nuxt_script:
            logger.warning("Paypers: could not find Nuxt SSR payload in %s", url)
            return []

        # The payload is a valid JSON array where integer values are
        # indices that reference other items in the same array.
        data: list = json.loads(nuxt_script)

        def resolve(node, depth: int = 0):
            """Recursively resolve index references into their actual values."""
            if depth > 25:
                return node
            if isinstance(node, int) and 0 <= node < len(data):
                return resolve(data[node], depth + 1)
            if isinstance(node, dict):
                return {k: resolve(v, depth + 1) for k, v in node.items()}
            if isinstance(node, list):
                return [resolve(i, depth + 1) for i in node]
            return node

        # data[3] is the payload key→index map.
        payload_map: dict = data[3]
        overview_keys = [
            k for k in payload_map
            if "content-overview" in k and "PUBLICATION" in k
        ]

        seen_ids: set[str] = set()
        articles: list[dict] = []

        for key in overview_keys:
            idx = payload_map[key]
            try:
                resolved = resolve(data[idx])
                content_items = resolved.get("content_items", [])
                if not isinstance(content_items, list):
                    continue
                for item in content_items:
                    if not isinstance(item, dict):
                        continue
                    art_id = item.get("id", "")
                    if art_id in seen_ids:
                        continue
                    seen_ids.add(art_id)

                    title = (item.get("title") or "").strip()
                    slug = item.get("slug") or ""
                    domain_info = item.get("domain") or {}
                    domain_slug = (
                        domain_info.get("slug", "news")
                        if isinstance(domain_info, dict)
                        else "news"
                    )
                    article_url = (
                        f"https://www.thepaypers.com/{domain_slug}/{slug}"
                        if slug else ""
                    )
                    published_at = item.get("published_at") or ""
                    snippet = _extract_text(item.get("abstract") or "", max_words=150)

                    if not title or not article_url:
                        continue

                    articles.append({
                        "title": title,
                        "url": article_url,
                        "snippet": snippet,
                        "source_name": name,
                        "published_at": published_at,
                    })
            except Exception as exc:
                logger.debug(
                    "Paypers: error resolving overview section '%s': %s", key, exc
                )

        return articles

    except Exception as exc:
        logger.warning("Failed to scrape Paypers (%s): %s", url, exc)
        return []


# ---------------------------------------------------------------------------
# Scraper: IAPP (sitemap.xml + parallel og:meta)
# ---------------------------------------------------------------------------

def _fetch_scrape_iapp(
    source: dict,
    cutoff: datetime,
    db_path: str,
) -> list[dict]:
    """Fetch recent IAPP articles via their sitemap.xml.

    Strategy:
      1. Download the sitemap (9 000+ URLs with <lastmod> timestamps).
      2. Filter to /news/ URLs whose lastmod falls within the lookback window.
      3. Skip URLs already in the seen_urls table.
      4. Fetch og:title and og:description for the remaining URLs in parallel
         using a thread pool (max 5 workers).

    Args:
        source: Source config dict with 'name' and 'url' (sitemap URL).
        cutoff: Datetime before which articles are considered too old.
        db_path: SQLite database path for pre-filtering seen URLs.

    Returns:
        List of normalized article dicts, or [] on failure.
    """
    name = source["name"]
    sitemap_url = source["url"]
    try:
        resp = _SESSION.get(sitemap_url, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "xml")

        candidates: list[tuple[datetime, str, str]] = []  # (published_dt, published_str, url)
        for url_tag in soup.find_all("url"):
            loc_tag = url_tag.find("loc")
            lastmod_tag = url_tag.find("lastmod")
            if not loc_tag or "/news/" not in loc_tag.text:
                continue
            lastmod_str = lastmod_tag.text.strip() if lastmod_tag else ""
            published_dt = _parse_date(lastmod_str)
            if not published_dt or published_dt < cutoff:
                continue
            article_url = loc_tag.text.strip()
            if is_seen(db_path, article_url):
                continue
            candidates.append((published_dt, lastmod_str, article_url))

        # Keep the N most recent unseen articles.
        candidates.sort(key=lambda x: x[0], reverse=True)
        candidates = candidates[:_IAPP_MAX_ARTICLES]

        if not candidates:
            return []

        logger.info(
            "IAPP: %d unseen candidates in window; fetching og:meta in parallel.",
            len(candidates),
        )

        articles: list[dict] = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(_fetch_iapp_article_meta, url, name, published_str): url
                for _, published_str, url in candidates
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    articles.append(result)

        return articles

    except Exception as exc:
        logger.warning("Failed to scrape IAPP sitemap (%s): %s", sitemap_url, exc)
        return []


def _fetch_iapp_article_meta(
    url: str, source_name: str, published_at: str
) -> dict | None:
    """Fetch one IAPP article page and extract og:title / og:description.

    Falls back to deriving the title from the URL slug if og:title is missing.

    Args:
        url: Full article URL.
        source_name: Name of the source (for the returned dict).
        published_at: ISO-8601 string from the sitemap lastmod field.

    Returns:
        Normalized article dict, or None if the request fails.
    """
    try:
        resp = _SESSION.get(url, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")

        og_title = soup.find("meta", property="og:title")
        og_desc = soup.find("meta", property="og:description")

        title = (og_title.get("content", "") if og_title else "").strip()
        # Strip the " | IAPP" suffix that appears on every page title.
        title = re.sub(r"\s*\|\s*IAPP\s*$", "", title).strip()

        if not title:
            # Derive a readable title from the URL slug.
            slug = url.rstrip("/").split("/")[-1]
            title = slug.replace("-", " ").title()

        snippet = (og_desc.get("content", "") if og_desc else "").strip()

        return {
            "title": title,
            "url": url,
            "snippet": snippet,
            "source_name": source_name,
            "published_at": published_at,
        }
    except Exception as exc:
        logger.debug("IAPP: failed to fetch article %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalize_entry(entry, source_name: str) -> dict:
    """Convert a feedparser entry into a normalized article dict.

    Gracefully handles missing fields by falling back to empty strings.

    Args:
        entry: A feedparser entry object.
        source_name: Name of the source this entry came from.

    Returns:
        Article dict with keys: title, url, snippet, source_name,
        published_at.
    """
    title = getattr(entry, "title", "") or ""
    url = getattr(entry, "link", "") or ""

    # published_parsed is a time.struct_time in UTC when available.
    published_at = ""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            published_at = dt.isoformat()
        except Exception:
            pass
    if not published_at:
        published_at = getattr(entry, "published", "") or ""

    # Build snippet from summary or content, strip HTML, truncate to 150 words.
    raw_text = ""
    if hasattr(entry, "summary") and entry.summary:
        raw_text = entry.summary
    elif hasattr(entry, "content") and entry.content:
        raw_text = entry.content[0].get("value", "")

    snippet = _extract_text(raw_text, max_words=150)

    return {
        "title": title.strip(),
        "url": url.strip(),
        "snippet": snippet,
        "source_name": source_name,
        "published_at": published_at,
    }


def _extract_text(html: str, max_words: int = 150) -> str:
    """Strip HTML tags and return up to max_words words of plain text.

    Args:
        html: Raw HTML or plain text string.
        max_words: Maximum number of words to return.

    Returns:
        Plain text truncated to max_words words.
    """
    if not html:
        return ""
    try:
        text = BeautifulSoup(html, "lxml").get_text(separator=" ")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    return " ".join(words[:max_words])


def _parse_date(date_str: str) -> datetime | None:
    """Attempt to parse an ISO-8601 date string into an aware datetime.

    Args:
        date_str: Date string, ideally in ISO-8601 format.

    Returns:
        Timezone-aware datetime, or None if parsing fails.
    """
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
