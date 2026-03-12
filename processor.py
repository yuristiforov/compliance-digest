"""
processor.py — LLM-based article processing and enrichment.

Uses the Anthropic API (Claude Haiku) to classify each article into a
Russian-language topic and produce a 2-3 sentence Russian summary.

Environment variables required (loaded via python-dotenv):
    ANTHROPIC_API_KEY — Anthropic API key.
"""

import json
import logging
import os
import time

import anthropic
from dotenv import load_dotenv

# override=True ensures .env values win even if the var is already set
# as an empty string in the Windows environment.
load_dotenv(override=True)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a compliance analyst assistant. You will receive a list of articles. \
For each article return a JSON array where each element has exactly these fields:
- "id": the article's id number (integer)
- "topic_ru": topic label in Russian, choose the single best fit from: \
["AML и санкции", "Платежи", "Крипто и Web3", "Forex и CFD", "iGaming", \
"Конфиденциальность и данные", "RegTech", "Прочее"]
- "summary_ru": 2-3 sentence summary in Russian of what the article is about
Return ONLY a valid JSON array. No markdown, no explanation, no preamble.\
"""

_FALLBACK_TOPIC = "Прочее"
_RETRY_DELAY_SECONDS = 5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_articles(articles: list[dict], llm_config: dict) -> list[dict]:
    """Enrich articles with Russian topic labels and summaries via LLM.

    Splits the input into batches sized by digest.max_articles_per_batch,
    calls the LLM once per batch, and merges results back into the original
    list in-place.

    Args:
        articles: List of article dicts (title, url, snippet, source_name,
                  published_at).
        llm_config: The 'llm' section from config.yaml. Keys used:
                    model, max_tokens_per_call, snippet_words.
                    Also reads digest.max_articles_per_batch if present on
                    the dict (caller may pass the merged config).

    Returns:
        The same list, each dict extended with topic_ru (str) and
        summary_ru (str).
    """
    if not articles:
        return articles

    # Cap the LLM batch to what can fit in max_tokens_per_call.
    # Empirically each article consumes ~200 output tokens (topic + summary).
    # This prevents truncated JSON when the digest batch size is large.
    max_tokens: int = llm_config.get("max_tokens_per_call", 4096)
    digest_batch: int = llm_config.get("max_articles_per_batch", 100)
    batch_size: int = min(digest_batch, max(1, max_tokens // 200))
    batches = [
        articles[i: i + batch_size]
        for i in range(0, len(articles), batch_size)
    ]

    logger.info(
        "Processing %d articles in %d batch(es) of up to %d.",
        len(articles), len(batches), batch_size,
    )

    for batch_idx, batch in enumerate(batches, start=1):
        logger.info("Calling LLM for batch %d/%d (%d articles)…",
                    batch_idx, len(batches), len(batch))
        enriched = _call_llm(batch, llm_config)
        # Merge enriched fields back into the original dicts by position.
        for orig, result in zip(batch, enriched):
            orig["topic_ru"] = result.get("topic_ru", _FALLBACK_TOPIC)
            orig["summary_ru"] = result.get("summary_ru", "")

    return articles


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _call_llm(batch: list[dict], llm_config: dict) -> list[dict]:
    """Send one API request for a batch and return the enriched batch.

    Retries once after _RETRY_DELAY_SECONDS on any API error. If both
    attempts fail the batch is returned with fallback values.

    Args:
        batch: Subset of article dicts to process.
        llm_config: LLM config section from config.yaml.

    Returns:
        The batch with topic_ru and summary_ru added to each dict.
    """
    prompt = _build_prompt(batch)
    model = llm_config.get("model", "claude-haiku-4-5-20251001")
    max_tokens = llm_config.get("max_tokens_per_call", 4096)

    for attempt in (1, 2):
        try:
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = message.content[0].text
            logger.info(
                "LLM usage — input: %d tokens, output: %d tokens.",
                message.usage.input_tokens,
                message.usage.output_tokens,
            )
            # Attach usage to the first article dict so callers can aggregate.
            batch[0].setdefault("_usage", {"input_tokens": 0, "output_tokens": 0})
            batch[0]["_usage"]["input_tokens"] += message.usage.input_tokens
            batch[0]["_usage"]["output_tokens"] += message.usage.output_tokens

            return _parse_llm_response(response_text, batch)

        except Exception as exc:
            if attempt == 1:
                logger.warning(
                    "LLM call failed (attempt %d): %s — retrying in %ds…",
                    attempt, exc, _RETRY_DELAY_SECONDS,
                )
                time.sleep(_RETRY_DELAY_SECONDS)
            else:
                logger.warning(
                    "LLM call failed (attempt %d): %s — skipping batch with fallback.",
                    attempt, exc,
                )
                return _apply_fallback(batch)

    return _apply_fallback(batch)  # unreachable, but satisfies type checkers


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(batch: list[dict]) -> str:
    """Construct the user message listing all articles in the batch.

    Each article is one line:
        [N] Source: <source_name> | Title: <title> | Snippet: <snippet>

    Args:
        batch: List of article dicts for this batch.

    Returns:
        Formatted multi-line string to send as the user message.
    """
    lines: list[str] = []
    for idx, article in enumerate(batch, start=1):
        title = (article.get("title") or "").strip()
        source = (article.get("source_name") or "").strip()
        snippet = (article.get("snippet") or "").strip()
        lines.append(f"[{idx}] Source: {source} | Title: {title} | Snippet: {snippet}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_llm_response(response_text: str, batch: list[dict]) -> list[dict]:
    """Parse the LLM JSON response and merge results into the batch.

    Matches results by array position (index 0 → batch[0], etc.).
    Falls back gracefully if the JSON is missing, malformed, or has
    fewer elements than expected.

    Args:
        response_text: Raw text returned by the LLM.
        batch: The original batch of article dicts.

    Returns:
        The batch dicts with topic_ru and summary_ru populated.
    """
    try:
        # Strip accidental markdown fences the model may include despite the prompt.
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]      # drop opening fence line
            cleaned = cleaned.rsplit("```", 1)[0]     # drop closing fence
        cleaned = cleaned.strip()

        parsed: list[dict] = json.loads(cleaned)
        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")

    except Exception as exc:
        logger.warning("Failed to parse LLM response: %s\nRaw: %s", exc, response_text[:300])
        return _apply_fallback(batch)

    for i, article in enumerate(batch):
        if i < len(parsed):
            item = parsed[i]
            if not isinstance(item, dict):
                article["topic_ru"] = _FALLBACK_TOPIC
                article["summary_ru"] = ""
                continue
            topic = str(item.get("topic_ru") or _FALLBACK_TOPIC).strip()
            summary = str(item.get("summary_ru") or "").strip()
            article["topic_ru"] = topic if topic else _FALLBACK_TOPIC
            article["summary_ru"] = summary
        else:
            # LLM returned fewer items than expected.
            logger.debug("LLM response missing entry for article index %d; using fallback.", i)
            article["topic_ru"] = _FALLBACK_TOPIC
            article["summary_ru"] = ""

    return batch


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_fallback(batch: list[dict]) -> list[dict]:
    """Stamp every article in the batch with safe fallback values.

    Args:
        batch: List of article dicts.

    Returns:
        The same batch with topic_ru and summary_ru set to defaults.
    """
    for article in batch:
        article.setdefault("topic_ru", _FALLBACK_TOPIC)
        article.setdefault("summary_ru", "")
    return batch
