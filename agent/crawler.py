"""
Web Crawler - scrapes news/updates pages from major shipping line websites.

Uses Playwright (async) for JavaScript-rendered carrier sites.
Each carrier has a custom extraction strategy since page structures differ.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from urllib.parse import urljoin

from agent.rss_aggregator import Article

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-carrier CSS selectors (best-effort; update if sites change layout)
# ---------------------------------------------------------------------------
_CARRIER_SELECTORS: dict[str, dict] = {
    "msc": {
        "article_links": "a[href*='/news/']",
        "title": "h1",
        "date": "time, .date, [class*='date']",
        "body": "article, .article-body, .news-content, main",
    },
    "hapag_lloyd": {
        "article_links": "a[href*='/news/']",
        "title": "h1",
        "date": "time, .publication-date, [class*='date']",
        "body": ".article__body, .news__body, article",
    },
    "cma_cgm": {
        "article_links": "a[href*='/news/']",
        "title": "h1, .news-title",
        "date": "time, .date, .published",
        "body": ".news-content, article, .content-area",
    },
    "maersk": {
        "article_links": "a[href*='/news/']",
        "title": "h1",
        "date": "time, [data-testid='date'], .date",
        "body": "[data-testid='article-body'], article, .article-body",
    },
    "evergreen": {
        "article_links": "a[href*='news']",
        "title": "h1, .title",
        "date": ".date, time",
        "body": ".news-detail, .content",
    },
    "cosco": {
        "article_links": "a[href*='Notice'], a[href*='news']",
        "title": "h1, .notice-title",
        "date": ".date, .pub-date, time",
        "body": ".notice-content, .content",
    },
    "one_line": {
        "article_links": "a[href*='/news/']",
        "title": "h1, .news-title",
        "date": "time, .date, .published-date",
        "body": ".news-detail, .article-content, article",
    },
    "yang_ming": {
        "article_links": "a[href*='news'], a[href*='News']",
        "title": "h1, .title",
        "date": ".date, time",
        "body": ".content, .news-content",
    },
    "hmm": {
        "article_links": "a[href*='news'], a[href*='Notice']",
        "title": "h1, .title",
        "date": ".date, time, .reg-date",
        "body": ".content, .view-content, article",
    },
    "zim": {
        "article_links": "a[href*='/news/']",
        "title": "h1",
        "date": "time, .date, .post-date",
        "body": ".post-content, article, .content",
    },
    "wan_hai": {
        "article_links": "a[href*='news'], a[href*='bulletin']",
        "title": "h1, .title",
        "date": ".date, time",
        "body": ".content, article",
    },
    "pil": {
        "article_links": "a[href*='news']",
        "title": "h1, .entry-title",
        "date": "time, .date, .entry-date",
        "body": ".entry-content, article, .content",
    },
    "messina": {
        "article_links": "a[href*='/news/']",
        "title": "h1",
        "date": "time, .date",
        "body": "article, .content, .news-body",
    },
    "safmarine": {
        "article_links": "a[href*='/news/']",
        "title": "h1",
        "date": "time, .date",
        "body": "article, .article-body, .content",
    },
}

_DEFAULT_SELECTORS = {
    "article_links": "a[href*='/news/'], a[href*='/press/'], a[href*='/article/']",
    "title": "h1",
    "date": "time, .date, .published",
    "body": "article, main, .content",
}


def _strip_html(html: str) -> str:
    """Very lightweight HTML tag stripper."""
    return re.sub(r"<[^>]+>", " ", html).strip()


def _clean_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


async def _extract_article_text(page, selectors: dict) -> tuple[str, str]:
    """
    Given a Playwright page already navigated to an article, extract
    (title, body_text) using provided CSS selectors.
    """
    title = ""
    body = ""
    try:
        title_el = await page.query_selector(selectors["title"])
        if title_el:
            title = _clean_whitespace(await title_el.inner_text())
    except Exception:
        pass

    try:
        body_el = await page.query_selector(selectors["body"])
        if body_el:
            body = _clean_whitespace(await body_el.inner_text())
    except Exception:
        pass

    return title, body


async def _parse_article_date(page, selectors: dict) -> Optional[datetime]:
    """Try to extract a publication date from the article page."""
    try:
        date_el = await page.query_selector(selectors["date"])
        if date_el:
            # Try datetime attribute first
            dt_attr = await date_el.get_attribute("datetime")
            if dt_attr:
                from dateutil import parser as dateparser
                return dateparser.parse(dt_attr).astimezone(timezone.utc)
            text = _clean_whitespace(await date_el.inner_text())
            if text:
                from dateutil import parser as dateparser
                return dateparser.parse(text, fuzzy=True).astimezone(timezone.utc)
    except Exception:
        pass
    return None


async def scrape_carrier_news(
    carrier_key: str,
    carrier_config: dict,
    lookback_days: int = 7,
) -> List[Article]:
    """
    Scrape news articles from a single shipping line website using Playwright.

    Args:
        carrier_key: Key from sources.yaml (e.g. 'msc', 'hapag_lloyd').
        carrier_config: Dict with 'name' and 'news_url' for this carrier.
        lookback_days: Only include articles within this window.

    Returns:
        List of Article objects scraped from the carrier site.
    """
    from playwright.async_api import async_playwright

    carrier_name: str = carrier_config.get("name", carrier_key)
    news_url: str = carrier_config.get("news_url", "")
    if not news_url:
        logger.warning("No news_url for carrier '%s', skipping", carrier_key)
        return []

    selectors = _CARRIER_SELECTORS.get(carrier_key, _DEFAULT_SELECTORS)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    articles: List[Article] = []

    logger.info("Scraping %s (%s)…", carrier_name, news_url)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        try:
            await page.goto(news_url, timeout=30_000, wait_until="networkidle")
        except Exception as exc:
            logger.warning("Failed to load %s: %s", news_url, exc)
            await browser.close()
            return []

        # Collect article links from the news index page
        link_elements = await page.query_selector_all(selectors["article_links"])
        hrefs: List[str] = []
        seen: set[str] = set()
        for el in link_elements:
            href = await el.get_attribute("href")
            if href:
                full_url = urljoin(news_url, href)
                if full_url not in seen:
                    seen.add(full_url)
                    hrefs.append(full_url)

        logger.debug("  %s: found %d candidate article links", carrier_name, len(hrefs))

        # Visit each article page (limit to first 20 to keep runtime reasonable)
        article_page = await context.new_page()
        for href in hrefs[:20]:
            try:
                await article_page.goto(href, timeout=20_000, wait_until="domcontentloaded")
                title, body = await _extract_article_text(article_page, selectors)
                pub_date = await _parse_article_date(article_page, selectors)

                if not title:
                    continue  # Can't use an article without a title

                if pub_date is None:
                    pub_date = datetime.now(timezone.utc)

                if pub_date < cutoff:
                    continue  # Too old

                articles.append(
                    Article(
                        title=title,
                        url=href,
                        source=carrier_name,
                        published_date=pub_date,
                        raw_text=body,
                    )
                )

                # Polite crawl delay
                import asyncio
                await asyncio.sleep(2)

            except Exception as exc:
                logger.debug("  Skipping %s: %s", href, exc)
                continue

        await browser.close()

    articles.sort(key=lambda a: a.published_date, reverse=True)
    logger.info("  %s: %d articles scraped", carrier_name, len(articles))
    return articles


async def scrape_all_carriers(
    sources_config: dict,
    lookback_days: int = 7,
) -> List[Article]:
    """
    Scrape all configured shipping line sites sequentially.

    Args:
        sources_config: Parsed content of config/sources.yaml.
        lookback_days: News freshness window.

    Returns:
        Combined list of Article objects from all carrier sites.
    """
    shipping_lines: dict = sources_config.get("shipping_lines", {})
    all_articles: List[Article] = []

    for carrier_key, carrier_cfg in shipping_lines.items():
        if carrier_cfg.get("type") != "scrape":
            continue
        try:
            articles = await scrape_carrier_news(
                carrier_key=carrier_key,
                carrier_config=carrier_cfg,
                lookback_days=lookback_days,
            )
            all_articles.extend(articles)
        except Exception as exc:
            logger.error("Error scraping carrier '%s': %s", carrier_key, exc)

    logger.info("Carrier scraping complete: %d articles total", len(all_articles))
    return all_articles
