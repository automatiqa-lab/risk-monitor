"""
Base scraper class - all scrapers inherit from this.
Handles timing, error logging, and DB storage.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from web.database import db_session, upsert_article, insert_metric, insert_alert, update_module_status

logger = logging.getLogger("control_tower.scraper")


class BaseScraper(ABC):
    name: str = ""
    module: str = ""

    @abstractmethod
    def run(self, db) -> tuple[int, int]:
        """Run the scraper. Returns (articles_count, metrics_count)."""
        ...

    def execute(self):
        """Execute with timing, logging, and error handling."""
        start = time.time()
        try:
            with db_session() as db:
                a_count, m_count = self.run(db)
                db.execute(
                    "INSERT INTO scraper_runs (scraper, status, articles_count, metrics_count, duration_ms) VALUES (?,?,?,?,?)",
                    (self.name, "ok", a_count, m_count, int((time.time() - start) * 1000))
                )
                logger.info("%s: %d articles, %d metrics (%.1fs)", self.name, a_count, m_count, time.time() - start)
                return a_count, m_count
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            logger.error("%s failed: %s", self.name, exc)
            try:
                with db_session() as db:
                    db.execute(
                        "INSERT INTO scraper_runs (scraper, status, error, duration_ms) VALUES (?,?,?,?)",
                        (self.name, "error", str(exc)[:500], elapsed)
                    )
            except Exception as log_exc:
                logger.warning("Failed to log scraper error for %s: %s", self.name, log_exc)
            return 0, 0
