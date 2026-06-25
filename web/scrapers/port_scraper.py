"""
Port authority scrapers - scrape public news/status pages from key port authorities.
"""
from __future__ import annotations

import logging
import httpx
from bs4 import BeautifulSoup

from web.scrapers.base import BaseScraper
from web.database import upsert_article, insert_alert

logger = logging.getLogger("control_tower.scraper.ports")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _scrape_news_page(url, source_name, module, region, db, max_items=10):
    """Generic news page scraper - works for most port authority sites."""
    articles = 0
    try:
        r = httpx.get(url, timeout=25, headers={"User-Agent": UA}, follow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")

        for item in soup.select("article, .news-item, .post, .views-row, .item, li.news")[:max_items]:
            title_el = item.select_one("h2, h3, h4, .title, a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)[:200]
            if len(title) < 15:
                continue

            link_el = item.select_one("a[href]")
            link = ""
            if link_el:
                link = link_el.get("href", "")
                if link and not link.startswith("http"):
                    from urllib.parse import urljoin
                    link = urljoin(url, link)

            summary_el = item.select_one("p, .excerpt, .summary, .teaser")
            summary = summary_el.get_text(strip=True)[:500] if summary_el else ""

            signal = ""
            lower = f"{title} {summary}".lower()
            if any(kw in lower for kw in ("congestion", "delay", "closure", "strike", "shortage")):
                signal = "shortage"
            elif any(kw in lower for kw in ("throughput", "record", "expansion", "normal")):
                signal = "surplus"
            elif any(kw in lower for kw in ("update", "vessel", "schedule", "capacity")):
                signal = "general"

            upsert_article(db, title, link, source_name, module, region, signal, summary, summary, "")
            articles += 1

            if signal == "shortage":
                insert_alert(db, title[:120], module, "high", region, ["CONGESTION", source_name.upper()])

    except Exception as exc:
        logger.warning("%s scrape failed: %s", source_name, exc)

    return articles


class PortOfRotterdamScraper(BaseScraper):
    name = "port_rotterdam"
    module = "congestion"

    def run(self, db) -> tuple[int, int]:
        n = _scrape_news_page(
            "https://www.portofrotterdam.com/en/news-and-press-releases",
            "Port of Rotterdam", "congestion", "north_europe", db
        )
        return n, 0


class HamburgPortScraper(BaseScraper):
    name = "port_hamburg"
    module = "congestion"

    def run(self, db) -> tuple[int, int]:
        n = _scrape_news_page(
            "https://www.hafen-hamburg.de/en/news/",
            "Hamburg Port Authority", "congestion", "north_europe", db
        )
        return n, 0


class KenyaPortsScraper(BaseScraper):
    name = "port_mombasa"
    module = "congestion"

    def run(self, db) -> tuple[int, int]:
        n = _scrape_news_page(
            "https://www.kpa.co.ke/Pages/News.aspx",
            "Kenya Ports Authority", "congestion", "east_africa", db
        )
        return n, 0


class SantosPortScraper(BaseScraper):
    name = "port_santos"
    module = "congestion"

    def run(self, db) -> tuple[int, int]:
        n = _scrape_news_page(
            "https://www.portodesantos.com.br/en/news/",
            "Santos Port Authority", "congestion", "brazil", db
        )
        return n, 0
