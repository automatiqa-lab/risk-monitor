"""
Summarizer. Turns raw articles into short summaries and writes the weekly
briefing that opens the report.

The model is whatever you put in settings.yaml under `llm.model`. Calls go
through LiteLLM, so the same code runs against Anthropic, OpenAI, a local
Ollama model, or anything else LiteLLM speaks. Set the matching provider key
in the environment (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...) and you're done.
"""
from __future__ import annotations

import logging
import os
import time
from typing import List

import litellm

from agent.rss_aggregator import Article

logger = logging.getLogger(__name__)

# Sensible default if settings.yaml doesn't say otherwise. Any LiteLLM model
# string works here, e.g. "gpt-4o-mini" or "ollama/llama3.1".
DEFAULT_MODEL = "claude-sonnet-4-6"


def _complete(prompt: str, model: str, max_tokens: int, temperature: float = 0.3) -> str:
    """One chat completion through LiteLLM. Returns the assistant text."""
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
        api_base=os.environ.get("LLM_API_BASE") or None,
    )
    return response.choices[0].message.content.strip()


_ARTICLE_PROMPT = """\
You are a supply chain risk analyst. Read the following article and write \
a concise 2-3 sentence summary suitable for a professional supply chain risk report.

Focus on: (1) what happened or changed, (2) which regions or trade lanes are \
affected, (3) any impact on container availability or shipping costs if relevant.

Article title: {title}
Source: {source}
Published: {date}

Article text:
{text}

Write only the summary paragraph. No bullet points. No markdown. Plain prose."""

_EXECUTIVE_PROMPT = """\
You are a supply chain risk analyst writing the Weekly Briefing section \
of the Operations Risk Monitor report, read by supply chain professionals.

Below are this week's shortage signals and general alerts:

{summaries_block}

Write exactly 3 short paragraphs - no more, no less:

Paragraph 1 - SHORTAGE ALERTS: Summarise the most critical equipment shortages \
and port congestion issues. Name specific ports and regions. Be direct about \
severity and operational impact.

Paragraph 2 - GENERAL ALERTS: Cover the general alerts: schedule disruptions, \
blank sailings, carrier route changes, and any other operational developments \
that affect planning but are not pure shortages.

Paragraph 3 - INDUSTRY OUTLOOK: Close with the broader market sentiment - \
freight rate trends, carrier consolidation news, or strategic shifts that shape \
the short-term outlook for logistics managers.

Plain prose only. No headings. No bullet points. No markdown. \
Each paragraph should be 3-4 sentences. Be specific and actionable."""


def summarize_article(
    article: Article,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 200,
) -> str:
    """
    Summarize one article in 2-3 sentences.

    Uses article.raw_text when present, otherwise the title alone. Writes the
    result to article.summary and returns it.
    """
    text = article.raw_text.strip() if article.raw_text else "(full text not available)"
    # Cap very long bodies to keep token cost down (~2000 chars).
    if len(text) > 2000:
        text = text[:2000] + "…"

    prompt = _ARTICLE_PROMPT.format(
        title=article.title,
        source=article.source,
        date=article.published_date.strftime("%Y-%m-%d"),
        text=text,
    )

    summary = _complete(prompt, model=model, max_tokens=max_tokens)
    article.summary = summary
    return summary


def summarize_all(
    articles: List[Article],
    settings: dict,
) -> List[Article]:
    """
    Summarize every article that doesn't already have a summary.

    Leaves a small gap between calls so we don't hammer the provider. Returns
    the same list with .summary filled in.
    """
    llm_cfg: dict = settings.get("llm", {})
    model: str = llm_cfg.get("model", DEFAULT_MODEL)
    max_tokens: int = llm_cfg.get("summary_max_tokens", 200)
    delay: float = 0.3  # seconds between calls

    to_summarize = [a for a in articles if not a.summary]
    logger.info("Summarizing %d articles with %s…", len(to_summarize), model)

    for i, article in enumerate(to_summarize, 1):
        try:
            summarize_article(article, model=model, max_tokens=max_tokens)
            logger.debug("  [%d/%d] Summarized: %s", i, len(to_summarize), article.title[:60])
        except litellm.RateLimitError:
            logger.warning("Rate limit hit - waiting 60s before retrying…")
            time.sleep(60)
            summarize_article(article, model=model, max_tokens=max_tokens)
        except Exception as exc:
            logger.error("Failed to summarize '%s': %s", article.title[:60], exc)
            article.summary = article.title  # fall back to the headline

        if i < len(to_summarize):
            time.sleep(delay)

    return articles


def generate_executive_summary(
    articles: List[Article],
    model: str = DEFAULT_MODEL,
    max_tokens: int = 600,
) -> str:
    """Write the 3-paragraph Weekly Briefing from the shortage and general signals."""
    if not articles:
        return "No significant ocean freight developments were identified this week."

    lines: List[str] = []

    # Shortage signals
    shortage_items = [a for a in articles if a.container_signal == "shortage"]
    if shortage_items:
        lines.append("SHORTAGE SIGNALS:")
        for a in shortage_items[:8]:
            lines.append(f"- [{a.source}] {a.summary or a.title}")

    # General alerts
    general_items = [a for a in articles if a.container_signal == "general"]
    if general_items:
        lines.append("\nGENERAL ALERTS:")
        for a in general_items[:8]:
            lines.append(f"- [{a.source}] {a.summary or a.title}")

    # Nothing tagged? Fall back to whatever we have.
    if not lines:
        lines.append("THIS WEEK'S NEWS:")
        for a in articles[:10]:
            lines.append(f"- [{a.source}] {a.summary or a.title}")

    summaries_block = "\n".join(lines)
    prompt = _EXECUTIVE_PROMPT.format(summaries_block=summaries_block)

    try:
        return _complete(prompt, model=model, max_tokens=max_tokens)
    except Exception as exc:
        logger.error("Failed to generate executive summary: %s", exc)
        return "This week's ocean freight newsletter covers developments across key trade regions."
