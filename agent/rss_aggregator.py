"""
News Fetcher - collects ocean freight news via targeted Google News searches.

Replaces RSS feed management with dynamic Google News queries built from
region and carrier configuration. No external feed URLs to maintain -
queries are derived automatically from config/regions.yaml and
config/sources.yaml.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional
from urllib.parse import quote_plus

import feedparser
import httpx

logger = logging.getLogger(__name__)

# Google News RSS search endpoint
_GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q={query}&hl=en-US&gl=US&ceid=US:en"
)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── Data model ─────────────────────────────────────────────────────────────

@dataclass
class Article:
    """Normalised article record from any source."""
    title: str
    url: str
    source: str
    published_date: datetime
    raw_text: str = ""
    summary: str = ""                   # Filled in by summarizer later
    regions: List[str] = field(default_factory=list)
    container_signal: Optional[str] = None  # "shortage" | "surplus" | "general" | None


# ── Internal helpers ───────────────────────────────────────────────────────

def _parse_entry_date(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
            except Exception:
                pass
    return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_source_name(entry: feedparser.FeedParserDict, fallback: str) -> str:
    """Pull the publisher name out of a Google News RSS entry."""
    if hasattr(entry, "source") and entry.source:
        src_title = getattr(entry.source, "title", "")
        if src_title:
            return src_title
    # Google sometimes appends "- Source Name" to the title
    raw_title = getattr(entry, "title", "") or ""
    if " - " in raw_title:
        return raw_title.rsplit(" - ", 1)[-1].strip()
    return fallback


# ── Query builders ─────────────────────────────────────────────────────────

def _build_region_query(region_cfg: dict) -> str:
    """Build a targeted ocean freight Google News query for a single region."""
    # Use the primary country + top port for a clean, effective query
    countries = region_cfg.get("countries", [])
    ports = region_cfg.get("ports", [])
    keywords = region_cfg.get("keywords", [])

    # Pick the most recognisable location term (first country + first port)
    primary_country = countries[0] if countries else ""
    primary_port = ports[0] if ports else ""

    location_parts = []
    if primary_country:
        location_parts.append(f'"{primary_country}"' if " " in primary_country else primary_country)
    if primary_port and primary_port != primary_country:
        location_parts.append(f'"{primary_port}"' if " " in primary_port else primary_port)
    # Add up to one extra keyword if it's distinctive
    for kw in keywords[:2]:
        kw = kw.strip()
        if len(kw) > 4 and kw not in (primary_country, primary_port):
            location_parts.append(f'"{kw}"' if " " in kw else kw)
            break

    location = " OR ".join(location_parts[:3])
    return f'ocean freight container shipping ({location})'


def _build_carrier_query(carrier_name: str) -> str:
    """Build a Google News query targeting a specific shipping line."""
    short = carrier_name.split(" - ")[0].split("(")[0].strip()
    return f'"{short}" shipping containers freight news'


def _build_container_watch_query() -> str:
    """Build a query targeting container availability signals globally."""
    return (
        '"container shortage" OR "blank sailing" OR '
        '"container rates" OR "equipment availability" OR '
        '"port congestion" ocean freight 2026'
    )


# ── Core fetcher ───────────────────────────────────────────────────────────

def fetch_google_news(
    query: str,
    source_label: str,
    lookback_days: int = 7,
    timeout: int = 20,
    max_results: int = 15,
) -> List[Article]:
    """
    Fetch articles from Google News RSS for a given search query.

    Args:
        query: Search query string (will be URL-encoded + dated).
        source_label: Human label used for logging and fallback source name.
        lookback_days: Appended as `when:Nd` to restrict freshness.
        timeout: HTTP request timeout in seconds.
        max_results: Cap on articles returned per query.

    Returns:
        List of Article objects, newest first.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    dated_query = f"{query} when:{lookback_days}d"
    url = _GOOGLE_NEWS_RSS.format(query=quote_plus(dated_query))

    try:
        r = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        )
        r.raise_for_status()
        feed = feedparser.parse(r.text)
    except Exception as exc:
        logger.warning("Failed to fetch Google News [%s]: %s", source_label, exc)
        return []

    articles: List[Article] = []
    for entry in feed.entries:
        title = _clean(getattr(entry, "title", "") or "")
        link = getattr(entry, "link", "") or ""
        if not title or not link:
            continue

        pub_date = _parse_entry_date(entry)
        if pub_date and pub_date < cutoff:
            continue
        if not pub_date:
            pub_date = datetime.now(timezone.utc)

        # Strip trailing "- Source Name" from Google News titles
        clean_title = title.rsplit(" - ", 1)[0].strip() if " - " in title else title
        raw = _clean(_strip_html(getattr(entry, "summary", "") or ""))
        source_name = _extract_source_name(entry, source_label)

        articles.append(Article(
            title=clean_title,
            url=link,
            source=source_name,
            published_date=pub_date,
            raw_text=raw,
        ))

    articles.sort(key=lambda a: a.published_date, reverse=True)
    results = articles[:max_results]
    logger.info("  Google News [%s]: %d articles", source_label, len(results))
    return results


# ── Main collection entry point ────────────────────────────────────────────

def collect_all_news(
    sources_config: dict,
    regions_config: dict,
    lookback_days: int = 7,
) -> List[Article]:
    """
    Collect ocean freight news from Google News across all configured
    regions, shipping lines, and container availability topics.

    Three search passes:
      1. One targeted query per region (East Africa, Brazil, North Europe…)
      2. One query per shipping line (MSC, Hapag-Lloyd, CMA CGM…)
      3. One global container availability / equipment watch query

    Returns deduplicated list of Articles, newest first.
    """
    all_articles: List[Article] = []
    seen_keys: set[str] = set()   # dedup by normalised title (Google URLs are redirects)

    def _add(articles: List[Article]) -> None:
        for a in articles:
            key = re.sub(r"\s+", " ", a.title.lower())[:80]
            if key not in seen_keys:
                seen_keys.add(key)
                all_articles.append(a)

    # ── Pass 1: Regional ocean freight news ──────────────────────────────
    for region_key, region_cfg in regions_config.get("regions", {}).items():
        display = region_cfg.get("display_name", region_key.replace("_", " ").title())
        logger.info("Querying Google News: %s…", display)
        _add(fetch_google_news(_build_region_query(region_cfg), display, lookback_days))

    # ── Pass 2: Carrier-specific news ────────────────────────────────────
    for carrier_key, carrier_cfg in sources_config.get("shipping_lines", {}).items():
        name = carrier_cfg.get("name", carrier_key)
        logger.info("Querying Google News: %s…", name)
        _add(fetch_google_news(_build_carrier_query(name), name, lookback_days))

    # ── Pass 3: Container availability watch ─────────────────────────────
    logger.info("Querying Google News: Container Availability Watch…")
    _add(fetch_google_news(_build_container_watch_query(), "Container Watch", lookback_days))

    all_articles.sort(key=lambda a: a.published_date, reverse=True)
    logger.info("Google News collection complete: %d unique articles total", len(all_articles))
    return all_articles
