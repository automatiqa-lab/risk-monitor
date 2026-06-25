"""
Vessel & port congestion scrapers - scrape public pages for quantitative
port data: vessels in port, at anchor, expected arrivals.

Sources:
  - VesselFinder (public port pages - simpler than MarineTraffic)
  - MarineTraffic (public port pages - JS-heavy, Playwright required)
  - Freightos Baltic Index (headline container rates)
"""
from __future__ import annotations

import logging
import re

from web.scrapers.base import BaseScraper
from web.database import insert_metric

logger = logging.getLogger("control_tower.scraper.vessels")

# Port configs: key, name, VesselFinder URL slug, MarineTraffic port ID
PORTS = [
    {"key": "mombasa", "name": "Mombasa", "vf_slug": "KEMBA", "mt_id": "346", "region": "east_africa"},
    {"key": "dar_es_salaam", "name": "Dar es Salaam", "vf_slug": "TZDAR", "mt_id": "706", "region": "east_africa"},
    {"key": "djibouti", "name": "Djibouti", "vf_slug": "DJJIB", "mt_id": "714", "region": "east_africa"},
    {"key": "santos", "name": "Santos", "vf_slug": "BRSSZ", "mt_id": "563", "region": "brazil"},
    {"key": "rotterdam", "name": "Rotterdam", "vf_slug": "NLRTM", "mt_id": "1", "region": "north_europe"},
    {"key": "hamburg", "name": "Hamburg", "vf_slug": "DEHAM", "mt_id": "39", "region": "north_europe"},
    {"key": "antwerp", "name": "Antwerp", "vf_slug": "BEANR", "mt_id": "10", "region": "north_europe"},
    {"key": "ho_chi_minh", "name": "Ho Chi Minh City", "vf_slug": "VNSGN", "mt_id": "417", "region": "vietnam"},
    {"key": "colon", "name": "Colón", "vf_slug": "PAMIT", "mt_id": "659", "region": "central_america"},
    {"key": "puerto_cortes", "name": "Puerto Cortes", "vf_slug": "HNPCR", "mt_id": "4449", "region": "central_america"},
    {"key": "felixstowe", "name": "Felixstowe", "vf_slug": "GBFXT", "mt_id": "127", "region": "north_europe"},
    {"key": "valencia", "name": "Valencia", "vf_slug": "ESVLC", "mt_id": "455", "region": "north_europe"},
]


class VesselFinderScraper(BaseScraper):
    """Scrape VesselFinder public port pages for vessel counts."""
    name = "vesselfinder"
    module = "congestion"

    def run(self, db) -> tuple[int, int]:
        from playwright.sync_api import sync_playwright
        metrics = 0

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

            for port in PORTS:
                try:
                    url = f"https://www.vesselfinder.com/ports/{port['vf_slug']}"
                    page.goto(url, timeout=20000, wait_until="domcontentloaded")
                    page.wait_for_timeout(4000)

                    # Use inner_text for clean parsing (no HTML tags)
                    text = page.inner_text("body")

                    # VesselFinder format: "Ships in port:\n42" and "Expected ships:\n29"
                    # Also: "X vessels have arrived within the past 24 hours"
                    in_port = _extract_number(text, r'Ships\s*in\s*port\s*[:\n]\s*(\d+)', r'in\s*port\s*[:\n]\s*(\d+)')
                    at_anchor = _extract_number(text, r'(?:At\s*anchor|Anchored)\s*[:\n]\s*(\d+)')
                    expected = _extract_number(text, r'Expected\s*ships?\s*[:\n]\s*(\d+)', r'(\d+)\s*ships?\s*(?:are\s*)?expected')
                    arrivals_24h = _extract_number(text, r'(\d+)\s*vessels?\s*have\s*arrived\s*within\s*the\s*past\s*24')

                    pk = port["key"]
                    if in_port is not None:
                        insert_metric(db, f"port_{pk}_in_port", "congestion", in_port, "vessels", f"{port['name']} - In Port", "VesselFinder")
                        metrics += 1
                    if at_anchor is not None:
                        insert_metric(db, f"port_{pk}_at_anchor", "congestion", at_anchor, "vessels", f"{port['name']} - At Anchor", "VesselFinder")
                        metrics += 1
                    if expected is not None:
                        insert_metric(db, f"port_{pk}_expected", "congestion", expected, "vessels", f"{port['name']} - Expected", "VesselFinder")
                        metrics += 1
                    if arrivals_24h is not None:
                        insert_metric(db, f"port_{pk}_arrivals_24h", "congestion", arrivals_24h, "vessels", f"{port['name']} - Arrivals (24h)", "VesselFinder")
                        metrics += 1

                    found = sum(1 for x in [in_port, at_anchor, expected, arrivals_24h] if x is not None)
                    if found > 0:
                        logger.info("  %s: in_port=%s at_anchor=%s expected=%s", port["name"], in_port, at_anchor, expected)
                    else:
                        logger.debug("  %s: no vessel data found", port["name"])

                except Exception as exc:
                    logger.warning("  %s scrape failed: %s", port["name"], str(exc)[:100])

            browser.close()

        return 0, metrics


class MarineTrafficScraper(BaseScraper):
    """Scrape MarineTraffic public port pages for vessel counts."""
    name = "marinetraffic"
    module = "congestion"

    def run(self, db) -> tuple[int, int]:
        from playwright.sync_api import sync_playwright
        metrics = 0

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

            for port in PORTS[:6]:  # Limit to avoid rate limiting
                try:
                    url = f"https://www.marinetraffic.com/en/ais/details/ports/{port['mt_id']}"
                    page.goto(url, timeout=25000, wait_until="domcontentloaded")
                    page.wait_for_timeout(4000)
                    content = page.content()

                    # MarineTraffic shows vessel counts in various formats
                    in_port = _extract_number(content, r'In\s*Port[^0-9]*(\d+)', r'(\d+)[^0-9]*vessels?\s*in\s*port')
                    expected = _extract_number(content, r'Expected[^0-9]*(\d+)', r'(\d+)[^0-9]*expected')
                    arrivals = _extract_number(content, r'Arrivals[^0-9]*(\d+)', r'(\d+)[^0-9]*arrivals?')

                    pk = port["key"]
                    if in_port is not None:
                        insert_metric(db, f"mt_{pk}_in_port", "congestion", in_port, "vessels", f"{port['name']} - In Port", "MarineTraffic")
                        metrics += 1
                    if expected is not None:
                        insert_metric(db, f"mt_{pk}_expected", "congestion", expected, "vessels", f"{port['name']} - Expected", "MarineTraffic")
                        metrics += 1

                    page.wait_for_timeout(2000)  # Polite delay

                except Exception as exc:
                    logger.debug("  MT %s: %s", port["name"], str(exc)[:80])

            browser.close()

        return 0, metrics


class FreightosScraper(BaseScraper):
    """Scrape Freightos Baltic Index (FBX) headline rates from public page."""
    name = "freightos_fbx"
    module = "freight"

    def run(self, db) -> tuple[int, int]:
        import httpx
        metrics = 0

        try:
            r = httpx.get("https://fbx.freightos.com/", timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                         follow_redirects=True)
            content = r.text

            # FBX shows headline index value and route-specific rates
            fbx_global = _extract_number(content, r'FBX[^0-9]*\$?([\d,]+)', r'global[^0-9]*\$?([\d,]+)')
            if fbx_global is not None:
                insert_metric(db, "fbx_global", "freight", fbx_global, "$/FEU", "FBX Global Index", "Freightos")
                metrics += 1
                logger.info("FBX Global: $%d/FEU", fbx_global)

            # Try route-specific: China to N.Europe, China to US West Coast
            for pattern, key, label in [
                (r'China.*?(?:North\s*)?Europe[^0-9]*\$?([\d,]+)', "fbx_china_europe", "FBX China→Europe"),
                (r'China.*?(?:US|West\s*Coast)[^0-9]*\$?([\d,]+)', "fbx_china_us", "FBX China→US"),
            ]:
                val = _extract_number(content, pattern)
                if val is not None:
                    insert_metric(db, key, "freight", val, "$/FEU", label, "Freightos")
                    metrics += 1

        except Exception as exc:
            logger.warning("Freightos scrape failed: %s", exc)

        return 0, metrics


def _extract_number(text: str, *patterns) -> int | None:
    """Try multiple regex patterns to extract a number from text."""
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except (ValueError, IndexError):
                continue
    return None
