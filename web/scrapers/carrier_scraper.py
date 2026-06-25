"""
Carrier website scraper - scrapes actual shipping line news/advisory pages
using the existing Playwright infrastructure from agent/crawler.py.
Stores articles tagged with carrier source for the carrier detail views.
"""
from __future__ import annotations

import sys, os, logging, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from web.scrapers.base import BaseScraper
from web.database import upsert_article, insert_alert
from web.scrapers.news_scraper import _tag_regions

logger = logging.getLogger("control_tower.scraper.carriers")

# Carrier configs with search selectors (from agent/crawler.py patterns)
CARRIERS = {
    "msc": {
        "name": "MSC",
        "news_url": "https://www.msc.com/en/news",
        "selectors": {"links": "a[href*='/news/']", "title": "h1", "body": "article, .article-body, .news-content, main"},
    },
    "maersk": {
        "name": "Maersk",
        "news_url": "https://www.maersk.com/news",
        "selectors": {"links": "a[href*='/news/']", "title": "h1", "body": "[data-testid='article-body'], article, .article-body"},
    },
    "cma_cgm": {
        "name": "CMA CGM",
        "news_url": "https://www.cma-cgm.com/news",
        "selectors": {"links": "a[href*='/news/']", "title": "h1, .news-title", "body": ".news-content, article"},
    },
    "hapag_lloyd": {
        "name": "Hapag-Lloyd",
        "news_url": "https://www.hapag-lloyd.com/en/news-insights/news.html",
        "selectors": {"links": "a[href*='/news/']", "title": "h1", "body": ".article__body, .news__body, article"},
    },
    "one_line": {
        "name": "ONE",
        "news_url": "https://www.one-line.com/en/news",
        "selectors": {"links": "a[href*='/news/']", "title": "h1, .news-title", "body": ".news-detail, article"},
    },
    "cosco": {
        "name": "COSCO Shipping",
        "news_url": "https://lines.coscoshipping.com/home/Notices",
        "selectors": {"links": "a[href*='Notice'], a[href*='news']", "title": "h1, .notice-title", "body": ".notice-content, .content"},
    },
    "evergreen": {
        "name": "Evergreen Line",
        "news_url": "https://www.evergreen-line.com/static/jsp/news_list.jsp",
        "selectors": {"links": "a[href*='news']", "title": "h1, .title", "body": ".news-detail, .content"},
    },
    "zim": {
        "name": "ZIM",
        "news_url": "https://www.zim.com/news",
        "selectors": {"links": "a[href*='/news/']", "title": "h1", "body": ".post-content, article, .content"},
    },
}


async def _scrape_carrier(carrier_key, cfg):
    """Scrape a single carrier news page, return list of (title, url, text) tuples."""
    from playwright.async_api import async_playwright
    from urllib.parse import urljoin
    import re

    results = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = await ctx.new_page()

            try:
                await page.goto(cfg["news_url"], timeout=25000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
            except Exception as exc:
                logger.warning("%s page load failed: %s", cfg["name"], exc)
                await browser.close()
                return results

            # Collect article links
            sel = cfg["selectors"]
            link_els = await page.query_selector_all(sel["links"])
            hrefs = []
            seen = set()
            for el in link_els:
                href = await el.get_attribute("href")
                if href:
                    full = urljoin(cfg["news_url"], href)
                    if full not in seen:
                        seen.add(full)
                        hrefs.append(full)

            # Also try to extract titles directly from the listing page
            for el in link_els[:15]:
                try:
                    title = (await el.inner_text()).strip()
                    href = await el.get_attribute("href")
                    if title and len(title) > 15 and href:
                        full = urljoin(cfg["news_url"], href)
                        # Clean Google-style suffix
                        clean_title = re.sub(r'\s*[-–-]\s*[^-–-]+$', '', title).strip() if ' - ' in title else title
                        results.append((clean_title[:200], full, ""))
                except:
                    pass

            # Visit first few article pages for full text
            if len(results) < 5 and hrefs:
                article_page = await ctx.new_page()
                for href in hrefs[:8]:
                    try:
                        await article_page.goto(href, timeout=15000, wait_until="domcontentloaded")
                        title_el = await article_page.query_selector(sel["title"])
                        title = ""
                        if title_el:
                            title = (await title_el.inner_text()).strip()[:200]
                        body_el = await article_page.query_selector(sel["body"])
                        body = ""
                        if body_el:
                            body = re.sub(r'\s+', ' ', (await body_el.inner_text()).strip())[:500]
                        if title:
                            results.append((title, href, body))
                        await asyncio.sleep(1.5)
                    except:
                        continue

            await browser.close()
    except Exception as exc:
        logger.warning("%s scraper error: %s", cfg["name"], exc)

    return results


class CarrierWebsiteScraper(BaseScraper):
    """Scrape all carrier news/advisory pages via Playwright."""
    name = "carrier_websites"
    module = "freight"

    def run(self, db) -> tuple[int, int]:
        total = 0

        for carrier_key, cfg in CARRIERS.items():
            try:
                results = asyncio.get_event_loop().run_until_complete(
                    _scrape_carrier(carrier_key, cfg)
                )
            except RuntimeError:
                # No event loop - create one
                loop = asyncio.new_event_loop()
                results = loop.run_until_complete(_scrape_carrier(carrier_key, cfg))
                loop.close()

            carrier_name = cfg["name"]
            count = 0
            for title, url, body in results:
                if not title or len(title) < 15:
                    continue

                text_lower = f"{title} {body}".lower()
                signal = ""
                if any(kw in text_lower for kw in ("suspend", "halt", "shortage", "force majeure", "emergency", "crisis")):
                    signal = "shortage"
                elif any(kw in text_lower for kw in ("surcharge", "advisory", "update", "efs", "war risk", "reroute")):
                    signal = "general"

                regions = _tag_regions(text_lower)

                upsert_article(db, title, url, carrier_name, "freight", regions, signal,
                              body[:300] if body else "", body or "", "")
                count += 1

                if signal == "shortage":
                    insert_alert(db, f"{carrier_name}: {title[:100]}", "freight", "critical",
                                regions, ["FREIGHT", carrier_name.upper()])

            total += count
            logger.info("  %s: %d articles scraped", carrier_name, count)

        return total, 0
