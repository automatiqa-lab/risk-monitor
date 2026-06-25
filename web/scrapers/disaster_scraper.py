"""
Disaster & weather scrapers - GDACS RSS, NOAA NHC RSS.
Public feeds, no API keys needed.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta

import feedparser
import httpx

from web.scrapers.base import BaseScraper
from web.database import upsert_article, insert_alert, insert_metric

logger = logging.getLogger("control_tower.scraper.disaster")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class GDACScraper(BaseScraper):
    """Scrape GDACS (Global Disaster Alert and Coordination System) RSS feed."""
    name = "gdacs"
    module = "weather"

    FEED_URL = "https://www.gdacs.org/xml/rss.xml"

    def run(self, db) -> tuple[int, int]:
        articles = 0
        try:
            r = httpx.get(self.FEED_URL, timeout=20, headers={"User-Agent": UA}, follow_redirects=True)
            feed = feedparser.parse(r.text)

            cutoff = datetime.now(timezone.utc) - timedelta(days=14)

            for entry in feed.entries[:30]:
                title = getattr(entry, "title", "")
                link = getattr(entry, "link", "")
                summary = getattr(entry, "summary", "")
                if not title:
                    continue

                # Parse severity from GDACS format
                severity = "info"
                title_lower = title.lower()
                if any(kw in title_lower for kw in ("red", "orange", "category 4", "category 5", "7.", "8.")):
                    severity = "critical"
                elif any(kw in title_lower for kw in ("orange", "category 3", "6.", "flood")):
                    severity = "high"

                # Determine signal
                signal = "shortage" if severity == "critical" else "general" if severity == "high" else ""

                upsert_article(db, title[:200], link, "GDACS", "weather", "", signal,
                              summary[:500], summary, "")
                articles += 1

                if severity in ("critical", "high"):
                    insert_alert(db, title[:120], "weather", severity, "", ["WEATHER", "GDACS"])

            logger.info("GDACS: %d events parsed", articles)

        except Exception as exc:
            logger.warning("GDACS scrape failed: %s", exc)

        return articles, 0


class NOAAHurricaneScraper(BaseScraper):
    """Scrape NOAA National Hurricane Center RSS for active tropical systems."""
    name = "noaa_nhc"
    module = "weather"

    FEEDS = [
        "https://www.nhc.noaa.gov/index-at.xml",   # Atlantic
        "https://www.nhc.noaa.gov/index-ep.xml",   # Eastern Pacific
        "https://www.nhc.noaa.gov/index-cp.xml",   # Central Pacific
    ]

    def run(self, db) -> tuple[int, int]:
        articles = 0
        active_systems = 0

        for feed_url in self.FEEDS:
            try:
                r = httpx.get(feed_url, timeout=20, headers={"User-Agent": UA}, follow_redirects=True)
                feed = feedparser.parse(r.text)

                for entry in feed.entries[:15]:
                    title = getattr(entry, "title", "")
                    link = getattr(entry, "link", "")
                    summary = getattr(entry, "summary", "")
                    if not title:
                        continue

                    # Check if it's an active system
                    severity = "info"
                    if any(kw in title.lower() for kw in ("hurricane", "typhoon", "tropical storm", "warning")):
                        severity = "high"
                        active_systems += 1
                    if any(kw in title.lower() for kw in ("category 4", "category 5", "major hurricane")):
                        severity = "critical"

                    signal = "shortage" if severity == "critical" else "general" if severity == "high" else ""

                    upsert_article(db, title[:200], link, "NOAA NHC", "weather", "", signal,
                                  summary[:500], summary, "")
                    articles += 1

                    if severity in ("critical", "high"):
                        insert_alert(db, title[:120], "weather", severity, "", ["WEATHER", "NHC"])

            except Exception as exc:
                logger.warning("NOAA %s failed: %s", feed_url.split("/")[-1], exc)

        insert_metric(db, "active_tropical_systems", "weather", active_systems, "systems", "Active Tropical Systems", "NOAA NHC")
        return articles, 1


class PanamaCanalScraper(BaseScraper):
    """Scrape Panama Canal Authority news page for transit restrictions."""
    name = "panama_canal"
    module = "congestion"

    def run(self, db) -> tuple[int, int]:
        articles = 0
        try:
            r = httpx.get(
                "https://www.pancanal.com/en/news/",
                timeout=20, headers={"User-Agent": UA}, follow_redirects=True,
            )

            # Extract news items from HTML
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")

            for item in soup.select("article, .news-item, .post")[:10]:
                title_el = item.select_one("h2, h3, .title, a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)[:200]
                link_el = item.select_one("a[href]")
                link = link_el["href"] if link_el else ""
                if link and not link.startswith("http"):
                    link = f"https://www.pancanal.com{link}"

                summary_el = item.select_one("p, .excerpt, .summary")
                summary = summary_el.get_text(strip=True)[:500] if summary_el else ""

                signal = ""
                lower = f"{title} {summary}".lower()
                if any(kw in lower for kw in ("restriction", "draft", "closure", "limit")):
                    signal = "general"
                if any(kw in lower for kw in ("suspend", "halt", "emergency")):
                    signal = "shortage"

                upsert_article(db, title, link, "Panama Canal Authority", "congestion",
                              "central_america", signal, summary, summary, "")
                articles += 1

        except Exception as exc:
            logger.warning("Panama Canal scrape failed: %s", exc)

        return articles, 0


class OFACScraper(BaseScraper):
    """Scrape OFAC recent actions for new sanctions affecting shipping."""
    name = "ofac"
    module = "geopolitical"

    def run(self, db) -> tuple[int, int]:
        articles = 0
        try:
            r = httpx.get(
                "https://ofac.treasury.gov/recent-actions",
                timeout=20, headers={"User-Agent": UA}, follow_redirects=True,
            )

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")

            for item in soup.select(".views-row, .recent-action, article, tr")[:15]:
                text = item.get_text(strip=True)[:300]
                if len(text) < 20:
                    continue

                link_el = item.select_one("a[href]")
                link = ""
                if link_el:
                    link = link_el.get("href", "")
                    if link and not link.startswith("http"):
                        link = f"https://ofac.treasury.gov{link}"

                title = text[:150]

                # Check if shipping-relevant
                lower = text.lower()
                if any(kw in lower for kw in ("vessel", "shipping", "maritime", "port", "iran", "russia", "oil")):
                    signal = "general"
                    upsert_article(db, title, link, "OFAC", "geopolitical", "", signal, text[:500], text, "")
                    insert_alert(db, f"OFAC: {title[:100]}", "geopolitical", "high", "", ["GEOPOLITICAL", "SANCTIONS"])
                    articles += 1

        except Exception as exc:
            logger.warning("OFAC scrape failed: %s", exc)

        return articles, 0
