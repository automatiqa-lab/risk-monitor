"""
FreightNewsAgent - Global Freight News.

Thin wrapper over the existing agent/ pipeline. Produces HTML + Markdown + PDF.
This agent is the original "Ocean Freight Weekly" pipeline, now part of the
Operations Risk Navigator harness.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from agent.rss_aggregator import Article, collect_all_news
from agent.crawler import scrape_all_carriers
from agent.manual_loader import load_manual_articles, load_weekly_briefing
from agent.filter import apply_filters, clear_pattern_cache
from agent.summarizer import summarize_all, generate_executive_summary
from agent.composer import (
    build_template_context,
    render_html,
    render_markdown,
    render_pdf,
    save_outputs,
)
from agents.base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"


class FreightNewsAgent(BaseAgent):
    """Global Freight News - HTML + Markdown + PDF output."""

    name = "freight"
    description = "Global Freight News"

    def __init__(
        self,
        date_str: Optional[str] = None,
        dry_run: bool = False,
        no_scrape: bool = False,
        exclude_regions: Optional[List[str]] = None,
        conflict_mode: bool = False,
    ):
        super().__init__(date_str=date_str, dry_run=dry_run, no_scrape=no_scrape)
        self.exclude_regions = exclude_regions or []
        self.conflict_mode = conflict_mode

    def load_config(self) -> Dict[str, Any]:
        with open(CONFIG_DIR / "sources.yaml", encoding="utf-8") as f:
            sources = yaml.safe_load(f)
        with open(CONFIG_DIR / "regions.yaml", encoding="utf-8") as f:
            regions = yaml.safe_load(f)
        with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        return {"sources": sources, "regions": regions, "settings": settings}

    def collect(self, config: Dict[str, Any]) -> List[Article]:
        lookback = config["settings"]["agent"]["lookback_days"]

        # Google News
        news = collect_all_news(config["sources"], config["regions"], lookback)
        self.logger.info("%d articles from Google News", len(news))

        # Carrier scraping (skipped in dashboard mode - handled by Phase 2 CarrierWebsiteScraper)
        carrier = []
        if not self.no_scrape:
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                carrier = loop.run_until_complete(
                    scrape_all_carriers(config["sources"], lookback_days=lookback)
                )
                loop.close()
                self.logger.info("%d articles from carrier sites", len(carrier))
            except Exception as exc:
                self.logger.warning("Carrier scraping skipped: %s", exc)

        # Manual input
        manual = load_manual_articles()
        if manual:
            self.logger.info("%d manual articles loaded", len(manual))

        return news + carrier + manual

    def filter_articles(self, articles: List[Article], config: Dict[str, Any]) -> List[Article]:
        clear_pattern_cache()
        tagged = apply_filters(articles, config["regions"])

        if self.exclude_regions:
            excluded = set(self.exclude_regions)
            for a in tagged:
                a.regions = [r for r in a.regions if r not in excluded]
            tagged = [a for a in tagged if a.regions or a.container_signal]

        return tagged

    def summarize(
        self, articles: List[Article], config: Dict[str, Any]
    ) -> tuple[List[Article], str]:
        summarized = summarize_all(articles, config["settings"])
        exec_summary = load_weekly_briefing() or generate_executive_summary(summarized)
        return summarized, exec_summary

    def compose(
        self, articles: List[Article], exec_summary: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        context = build_template_context(
            articles, exec_summary, config["regions"], config["sources"], config["settings"]
        )
        return {
            "html": render_html(context),
            "md": render_markdown(context),
        }

    def save(self, artifacts: Dict[str, Any], config: Dict[str, Any]) -> List[Path]:
        output_dir = config["settings"]["output"]["directory"]
        html_path, md_path = save_outputs(
            artifacts["html"], artifacts["md"], output_dir, self.date_str
        )
        paths = [html_path, md_path]

        # PDF
        pdf_path = html_path.with_suffix(".pdf")
        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(render_pdf(html_path, pdf_path))
            paths.append(pdf_path)
        except Exception as exc:
            self.logger.error("PDF generation failed: %s", exc)

        return paths

    async def run(self) -> AgentResult:
        """Override to handle conflict sub-agent mode."""
        if self.conflict_mode:
            return await self._run_conflict()
        return await super().run()

    async def _run_conflict(self) -> AgentResult:
        """Delegate to the existing conflict sub-agent pipeline."""
        from agent.conflict_agent import run_conflict_pipeline

        config = self.load_config()
        await run_conflict_pipeline(
            sources_config=config["sources"],
            regions_config=config["regions"],
            settings=config["settings"],
            date_str=self.date_str,
            no_scrape=self.no_scrape,
            dry_run=self.dry_run,
        )
        return AgentResult(
            agent_name="freight-conflict",
            date_str=self.date_str,
            summary="Conflict brief generated",
        )
