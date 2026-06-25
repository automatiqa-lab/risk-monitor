"""
Unified scraper scheduler - runs all agents + data enrichment scrapers.

The agents handle: Google News collection + filtering + region tagging + DB storage.
The data scrapers handle: quantitative metrics (prices, vessel counts).

Default: every 6 hours.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("control_tower.scheduler")


def run_all_scrapers():
    """Execute all agents and data scrapers sequentially."""
    start = time.time()
    logger.info("=" * 60)
    logger.info("SCRAPER RUN STARTED - %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    logger.info("=" * 60)

    total_articles = 0
    total_metrics = 0

    from web.database import init_db, db_session

    # ── Phase 1: Run agents (collect + filter + store to DB) ─────────
    logger.info("PHASE 1: Running agents...")
    from agents.registry import get_agent
    agent_names = ["freight", "oil", "diesel", "strikes", "weather", "geopolitical", "congestion"]

    for name in agent_names:
        try:
            agent = get_agent(name, no_scrape=True)  # Playwright scraping handled in Phase 2
            with db_session() as db:
                count = agent.run_for_dashboard(db)
                total_articles += count

                # Log scraper run
                db.execute(
                    "INSERT INTO scraper_runs (scraper, status, articles_count, metrics_count, duration_ms) VALUES (?,?,?,?,?)",
                    (f"agent_{name}", "ok", count, 0, 0)
                )
        except Exception as exc:
            logger.error("Agent %s failed: %s", name, exc)
            try:
                with db_session() as db:
                    db.execute(
                        "INSERT INTO scraper_runs (scraper, status, error) VALUES (?,?,?)",
                        (f"agent_{name}", "error", str(exc)[:500])
                    )
            except Exception as log_exc:
                logger.warning("Failed to log agent error for %s: %s", name, log_exc)

    # ── Phase 2: Data enrichment scrapers (prices, vessels) ──────────
    logger.info("PHASE 2: Running data enrichment scrapers...")
    from web.scrapers.price_scraper import BunkerPriceScraper, DieselPriceScraper, EUOilBulletinScraper, BrentCrudeScraper
    from web.scrapers.disaster_scraper import GDACScraper, NOAAHurricaneScraper, PanamaCanalScraper, OFACScraper
    from web.scrapers.port_scraper import PortOfRotterdamScraper, HamburgPortScraper, KenyaPortsScraper, SantosPortScraper
    from web.scrapers.carrier_scraper import CarrierWebsiteScraper
    from web.scrapers.vessel_scraper import VesselFinderScraper, MarineTrafficScraper, FreightosScraper

    data_scrapers = [
        # Carrier websites (Playwright - supplements Google News)
        CarrierWebsiteScraper(),
        # Prices
        BunkerPriceScraper(),
        DieselPriceScraper(),
        EUOilBulletinScraper(),
        BrentCrudeScraper(),
        # Disasters & geopolitical
        GDACScraper(),
        NOAAHurricaneScraper(),
        PanamaCanalScraper(),
        OFACScraper(),
        # Port authorities
        PortOfRotterdamScraper(),
        HamburgPortScraper(),
        KenyaPortsScraper(),
        SantosPortScraper(),
        # Vessel data
        VesselFinderScraper(),
        MarineTrafficScraper(),
        FreightosScraper(),
    ]

    for scraper in data_scrapers:
        a, m = scraper.execute()
        total_articles += a
        total_metrics += m

    elapsed = time.time() - start
    logger.info("=" * 60)
    logger.info("SCRAPER RUN COMPLETE - %d articles, %d metrics in %.0fs", total_articles, total_metrics, elapsed)
    logger.info("  Phase 1 (agents): %d modules", len(agent_names))
    logger.info("  Phase 2 (data):   %d scrapers", len(data_scrapers))
    logger.info("=" * 60)
    return total_articles, total_metrics


def create_scheduler(interval_hours: int = 6) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_all_scrapers,
        "interval",
        hours=interval_hours,
        id="scraper_run",
        name=f"Run all agents + scrapers every {interval_hours}h",
        next_run_time=datetime.now(timezone.utc),
    )
    return scheduler


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(message)s", datefmt="%H:%M:%S")
    from web.database import init_db
    init_db()
    run_all_scrapers()
