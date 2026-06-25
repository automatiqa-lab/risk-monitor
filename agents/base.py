"""
BaseAgent - the shape every risk agent shares.

Each agent runs a 5-step pipeline:
  1. collect   - gather raw articles (Google News + scraping + manual input)
  2. filter    - tag and filter articles by relevance
  3. summarize - summarize articles + write the executive briefing
  4. compose   - build output artifacts (HTML, PPTX, etc.)
  5. save      - write output files to disk

Two ways to run it:
  - run()               - full pipeline with LLM summaries + artifacts (batch/CLI)
  - run_for_dashboard() - collect + filter + store to SQLite (live dashboard, no LLM)
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.rss_aggregator import Article

logger = logging.getLogger(__name__)

# Region keywords for inline tagging (shared across all agents)
REGION_KEYWORDS = {
    "east_africa": ["mombasa", "dar es salaam", "kenya", "tanzania", "uganda", "ethiopia", "djibouti", "kampala", "kigali", "nairobi", "east africa", "lamu"],
    "central_america": ["panama", "honduras", "guatemala", "nicaragua", "costa rica", "colon", "puerto cortes", "corinto", "balboa", "central america"],
    "brazil": ["brazil", "santos", "paranagua", "itajai", "brasil"],
    "north_europe": ["rotterdam", "hamburg", "antwerp", "germany", "netherlands", "belgium", "bremerhaven", "felixstowe", "north europe", "le havre"],
    "vietnam": ["vietnam", "ho chi minh", "hai phong", "cat lai", "cai mep", "vietnamese"],
    "middle_east": ["middle east", "hormuz", "red sea", "suez", "houthi", "iran", "gulf", "fujairah", "jebel ali", "dubai", "saudi", "qatar", "bahrain", "kuwait", "oman", "iraq", "yemen"],
    "germany": ["germany", "german", "deutschland", "autobahn"],
    "spain": ["spain", "spanish", "valencia", "barcelona", "algeciras"],
    "italy": ["italy", "italian", "genoa", "trieste", "la spezia", "livorno"],
    "united_kingdom": ["united kingdom", "uk", "britain", "british", "felixstowe", "southampton", "london gateway", "tilbury"],
    "singapore": ["singapore", "vlsfo singapore", "mpa"],
}


def tag_regions(text: str) -> str:
    """Return comma-separated region keys matching the text."""
    lower = text.lower()
    return ",".join(r for r, kws in REGION_KEYWORDS.items() if any(kw in lower for kw in kws))


def classify_signal(text: str) -> str:
    """Classify text into shortage/general/surplus/empty."""
    lower = text.lower()
    if any(kw in lower for kw in ("shortage", "crisis", "suspend", "halt", "strike", "force majeure", "emergency")):
        return "shortage"
    if any(kw in lower for kw in ("surcharge", "advisory", "update", "price", "reroute", "efs", "war risk")):
        return "general"
    if any(kw in lower for kw in ("relief", "normalised", "resumed", "easing", "stable")):
        return "surplus"
    return ""


@dataclass
class AgentResult:
    """Uniform return type from any agent run."""
    agent_name: str
    date_str: str
    output_files: List[Path] = field(default_factory=list)
    article_count: int = 0
    summary: str = ""


class BaseAgent(ABC):
    """Contract that every Operations Risk Navigator agent implements."""

    name: str = ""             # e.g. "freight", "oil", "diesel"
    description: str = ""      # Human-readable label

    def __init__(
        self,
        date_str: Optional[str] = None,
        dry_run: bool = False,
        no_scrape: bool = False,
    ):
        self.date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.dry_run = dry_run
        self.no_scrape = no_scrape
        self.logger = logging.getLogger(f"sc_control_tower.{self.name}")

    # ── Abstract pipeline steps ──────────────────────────────────────────────

    @abstractmethod
    def load_config(self) -> Dict[str, Any]:
        """Load agent-specific configuration (settings, regions, sources)."""
        ...

    @abstractmethod
    def collect(self, config: Dict[str, Any]) -> List[Article]:
        """Step 1: Gather raw articles from all data sources."""
        ...

    @abstractmethod
    def filter_articles(self, articles: List[Article], config: Dict[str, Any]) -> List[Article]:
        """Step 2: Tag and filter articles by relevance."""
        ...

    @abstractmethod
    def summarize(
        self, articles: List[Article], config: Dict[str, Any]
    ) -> tuple[List[Article], str]:
        """Step 3: Summarize articles + generate executive summary.
        Returns (summarized_articles, executive_summary_text)."""
        ...

    @abstractmethod
    def compose(
        self, articles: List[Article], exec_summary: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Step 4: Build output artifacts. Returns {"format_name": content}."""
        ...

    @abstractmethod
    def save(self, artifacts: Dict[str, Any], config: Dict[str, Any]) -> List[Path]:
        """Step 5: Write output files. Returns list of saved file paths."""
        ...

    # ── Full pipeline (batch/CLI - with LLM summaries + PPTX generation) ─────

    async def run(self) -> AgentResult:
        """Execute the full pipeline including AI summarisation and output generation."""
        self.logger.info("Starting %s (full pipeline)...", self.description)

        config = self.load_config()
        articles = self.collect(config)
        self.logger.info("Collected %d articles", len(articles))

        filtered = self.filter_articles(articles, config)
        self.logger.info("Filtered to %d articles", len(filtered))

        summarized, exec_summary = self.summarize(filtered, config)

        if self.dry_run:
            self.logger.info("Dry run - skipping compose and save.")
            return AgentResult(
                agent_name=self.name,
                date_str=self.date_str,
                article_count=len(summarized),
                summary=f"[DRY RUN] {self.name}: {len(summarized)} articles collected",
            )

        artifacts = self.compose(summarized, exec_summary, config)
        output_files = self.save(artifacts, config)

        result = AgentResult(
            agent_name=self.name,
            date_str=self.date_str,
            output_files=output_files,
            article_count=len(summarized),
            summary=f"{self.description}: {len(summarized)} articles -> {len(output_files)} files",
        )
        self.logger.info("Done: %s", result.summary)
        return result

    # ── Dashboard pipeline (fast - no AI, writes to SQLite) ──────────────────

    def run_for_dashboard(self, db) -> int:
        """
        Execute collect + tag + store to SQLite for the live dashboard.
        Stores ALL collected articles (not just filtered ones) to maximise coverage.
        Region tagging and signal classification are applied but don't discard articles.
        Skips LLM summarisation and PPTX generation for speed.

        This is the unified entry point called by the web scheduler.
        """
        from web.database import upsert_article, insert_alert, update_module_status

        self.logger.info("Dashboard run: %s...", self.description)
        config = self.load_config()
        articles = self.collect(config)
        self.logger.info("  Collected %d articles", len(articles))

        # Also run filter to tag regions on articles (mutates in-place)
        # but we store ALL collected articles, not just filtered ones
        try:
            self.filter_articles(articles, config)
        except Exception as e:
            self.logger.warning("Filter failed for %s: %s - articles stored untagged", self.name, e)

        # Store ALL articles to DB with region tagging and signal classification
        count = 0
        critical_count = 0
        high_count = 0

        for a in articles:
            text = f"{a.title} {a.raw_text or ''} {a.summary or ''}"

            # Use agent filter tags if available, fall back to global keyword tagging
            regions = (",".join(a.regions) if a.regions else "") or tag_regions(text)
            signal = a.container_signal or classify_signal(text)

            upsert_article(
                db, a.title, a.url, a.source, self.name,
                regions, signal,
                a.summary or "", a.raw_text or "",
                a.published_date.isoformat() if a.published_date else "",
            )
            count += 1

            if signal == "shortage":
                critical_count += 1
                insert_alert(db, a.title[:120], self.name, "critical", regions, [self.name.upper()])
            elif signal == "general":
                high_count += 1
                insert_alert(db, a.title[:120], self.name, "high", regions, [self.name.upper()])

        # Update module status
        if critical_count >= 3:
            status = "CRITICAL"
        elif critical_count >= 1 or high_count >= 3:
            status = "HIGH"
        elif high_count >= 1:
            status = "WATCH"
        else:
            status = "STABLE"

        update_module_status(db, self.name, status, count, critical_count, high_count)
        self.logger.info("  %s: %d stored, status=%s (critical=%d, high=%d)",
                        self.name, count, status, critical_count, high_count)
        return count
