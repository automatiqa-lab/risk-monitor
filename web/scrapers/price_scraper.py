"""
Price scrapers - scrape live fuel and commodity prices from public websites.
No APIs, no auth - pure Playwright scraping of publicly available data.

Sources:
  - Ship & Bunker (VLSFO prices)
  - Fuelo.eu (European diesel pump prices)
  - Google Finance (Brent crude)
"""
from __future__ import annotations

import logging
import re

from web.scrapers.base import BaseScraper
from web.database import insert_metric, insert_alert

logger = logging.getLogger("control_tower.scraper.prices")


class BunkerPriceScraper(BaseScraper):
    """Scrape VLSFO bunker prices from Ship & Bunker."""
    name = "bunker_prices"
    module = "oil"

    def run(self, db) -> tuple[int, int]:
        from playwright.sync_api import sync_playwright
        metrics = 0

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                page.goto("https://shipandbunker.com/prices/emea/nwe/nl-rtm-rotterdam#VLSFO",
                          timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                # Extract price from page
                content = page.content()

                # Try to find VLSFO prices in page content
                # Ship & Bunker shows prices in a structured format
                vlsfo_matches = re.findall(r'VLSFO[^$]*?\$\s*([\d,]+(?:\.\d+)?)', content)
                if vlsfo_matches:
                    price = float(vlsfo_matches[0].replace(",", ""))
                    insert_metric(db, "vlsfo_rotterdam", "oil", price, "$/mt", "VLSFO Rotterdam", "Ship & Bunker")
                    metrics += 1
                    logger.info("VLSFO Rotterdam: $%.0f/mt", price)

            except Exception as exc:
                logger.warning("Ship & Bunker scrape failed: %s", exc)

            # Try Singapore
            try:
                page.goto("https://shipandbunker.com/prices/apac/sea/sg-sin-singapore#VLSFO",
                          timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                content = page.content()
                vlsfo_matches = re.findall(r'VLSFO[^$]*?\$\s*([\d,]+(?:\.\d+)?)', content)
                if vlsfo_matches:
                    price = float(vlsfo_matches[0].replace(",", ""))
                    insert_metric(db, "vlsfo_singapore", "oil", price, "$/mt", "VLSFO Singapore", "Ship & Bunker")
                    metrics += 1
                    logger.info("VLSFO Singapore: $%.0f/mt", price)
            except Exception as exc:
                logger.warning("Ship & Bunker Singapore failed: %s", exc)

            browser.close()

        return 0, metrics


class DieselPriceScraper(BaseScraper):
    """Scrape European diesel prices from GlobalPetrolPrices.com (public, no auth)."""
    name = "diesel_prices"
    module = "diesel"

    COUNTRIES = [
        {"country": "Germany", "slug": "Germany", "key": "diesel_germany", "flag": "DE", "currency": "EUR"},
        {"country": "Spain", "slug": "Spain", "key": "diesel_spain", "flag": "ES", "currency": "EUR"},
        {"country": "Italy", "slug": "Italy", "key": "diesel_italy", "flag": "IT", "currency": "EUR"},
        {"country": "United Kingdom", "slug": "United-Kingdom", "key": "diesel_uk", "flag": "UK", "currency": "GBP"},
    ]

    def run(self, db) -> tuple[int, int]:
        from playwright.sync_api import sync_playwright
        metrics = 0

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

            for cfg in self.COUNTRIES:
                try:
                    url = f"https://www.globalpetrolprices.com/{cfg['slug']}/diesel_prices/"
                    page.goto(url, timeout=25000, wait_until="domcontentloaded")
                    page.wait_for_timeout(2500)
                    text = page.inner_text("body")

                    # Format: "The current price of diesel fuel in X is EUR 2.15 per liter"
                    # Also: "Current price\t2.48" (USD) or EUR value in text
                    eur_match = re.search(r'EUR\s+([\d.]+)\s*per\s*liter', text, re.IGNORECASE)
                    usd_match = re.search(r'USD\s+([\d.]+)\s*per\s*liter', text, re.IGNORECASE)
                    gbp_match = re.search(r'GBP\s+([\d.]+)\s*per\s*liter', text, re.IGNORECASE)

                    # Get local currency price
                    local_price = None
                    local_unit = "EUR/L"
                    if cfg["currency"] == "GBP" and gbp_match:
                        local_price = float(gbp_match.group(1))
                        local_unit = "GBP/L"
                    elif eur_match:
                        local_price = float(eur_match.group(1))
                        local_unit = "EUR/L"

                    # Also get USD price for comparison
                    usd_price = float(usd_match.group(1)) if usd_match else None

                    if local_price:
                        insert_metric(db, cfg["key"], "diesel", local_price, local_unit,
                                     f"Diesel {cfg['flag']} ({cfg['country']})", "GlobalPetrolPrices")
                        metrics += 1
                        logger.info("Diesel %s: %.3f %s", cfg["flag"], local_price, local_unit)

                    if usd_price:
                        insert_metric(db, f"{cfg['key']}_usd", "diesel", usd_price, "USD/L",
                                     f"Diesel {cfg['flag']} (USD)", "GlobalPetrolPrices")
                        metrics += 1

                    # Extract last update date
                    date_match = re.search(r'(?:Last\s*update|from)\s*(\d{4}-\d{2}-\d{2})', text)
                    if date_match:
                        logger.info("  %s data as of: %s", cfg["flag"], date_match.group(1))

                    page.wait_for_timeout(1500)  # Polite delay

                except Exception as exc:
                    logger.warning("GlobalPetrolPrices %s failed: %s", cfg["country"], exc)

            browser.close()

        return 0, metrics


class EUOilBulletinScraper(BaseScraper):
    """Scrape EU Weekly Oil Bulletin for official diesel prices."""
    name = "eu_oil_bulletin"
    module = "diesel"

    def run(self, db) -> tuple[int, int]:
        import httpx
        from bs4 import BeautifulSoup
        metrics = 0

        try:
            r = httpx.get(
                "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en",
                timeout=25, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                follow_redirects=True,
            )
            text = r.text

            # EU Oil Bulletin publishes prices in EUR/1000L
            # Try to extract from the page tables
            soup = BeautifulSoup(text, "html.parser")

            # Look for country-specific diesel prices in tables
            for table in soup.select("table"):
                rows = table.select("tr")
                for row in rows:
                    cells = [td.get_text(strip=True) for td in row.select("td, th")]
                    if len(cells) < 2:
                        continue
                    row_text = " ".join(cells).lower()

                    for country, key, flag in [("germany", "eu_diesel_germany", "DE"),
                                                ("spain", "eu_diesel_spain", "ES"),
                                                ("italy", "eu_diesel_italy", "IT"),
                                                ("united kingdom", "eu_diesel_uk", "UK")]:
                        if country in row_text:
                            # Find numeric value (EUR/1000L format: 1,723 or 1723)
                            for cell in cells[1:]:
                                cleaned = cell.replace(",", "").replace(".", "").strip()
                                if cleaned.isdigit() and 800 < int(cleaned) < 5000:
                                    price = int(cleaned)
                                    insert_metric(db, key, "diesel", price, "EUR/1000L",
                                                 f"EU Diesel {flag} (Official)", "EU Oil Bulletin")
                                    metrics += 1
                                    logger.info("EU OB Diesel %s: %d EUR/1000L", flag, price)
                                    break

        except Exception as exc:
            logger.warning("EU Oil Bulletin scrape failed: %s", exc)

        return 0, metrics


class BrentCrudeScraper(BaseScraper):
    """Scrape Brent crude price from Google Finance (public, no API)."""
    name = "brent_crude"
    module = "oil"

    def run(self, db) -> tuple[int, int]:
        import httpx
        metrics = 0

        try:
            # Use Google Finance page which shows commodity prices
            r = httpx.get(
                "https://www.google.com/finance/quote/BZ=F:NYMEX",
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                follow_redirects=True,
            )
            # Extract price from page HTML
            matches = re.findall(r'data-last-price="([\d.]+)"', r.text)
            if matches:
                price = float(matches[0])
                insert_metric(db, "brent_crude", "oil", price, "$/bbl", "Brent Crude", "Google Finance")
                metrics += 1
                logger.info("Brent Crude: $%.2f/bbl", price)
            else:
                # Fallback: try another pattern
                matches = re.findall(r'class="YMlKec fxKbKc">([\d.,]+)</span>', r.text)
                if matches:
                    price = float(matches[0].replace(",", ""))
                    insert_metric(db, "brent_crude", "oil", price, "$/bbl", "Brent Crude", "Google Finance")
                    metrics += 1
                    logger.info("Brent Crude: $%.2f/bbl", price)
        except Exception as exc:
            logger.warning("Brent crude scrape failed: %s", exc)

        return 0, metrics
