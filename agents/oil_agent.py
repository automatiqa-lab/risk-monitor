"""
OilNewsAgent - Global Oil News & Marine Fuel Risk Assessment.

Monitors VLSFO prices, Brent crude, bunkering hub disruptions, and
carrier fuel surcharges. Produces a 7-slide PPTX risk report.

Pipeline: Google News + Playwright scraping + manual input → PPTX
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from agent.rss_aggregator import Article, fetch_google_news
from agent.filter import apply_filters, clear_pattern_cache
from agent.summarizer import summarize_all, generate_executive_summary
from agent.manual_loader import load_manual_articles
from agents.base import BaseAgent

logger = logging.getLogger(__name__)

_AGENT_DIR = Path(__file__).parent / "oil"
_GLOBAL_CONFIG = Path(__file__).parent.parent / "config" / "settings.yaml"

# Oil-specific AI prompts
_ARTICLE_SYSTEM = (
    "You are a marine fuel and oil market analyst. Summarize this article "
    "in 2-3 sentences for a professional supply chain risk report. Focus on: "
    "(1) price movement or supply disruption, (2) which bunkering hubs or "
    "chokepoints are affected, (3) any carrier surcharge or routing impact."
)
_EXECUTIVE_SYSTEM = (
    "You are a marine fuel analyst writing the executive briefing for the "
    "Operations Risk Monitor Global Oil report. "
    "Write exactly 3 paragraphs: (1) Current price action and supply status, "
    "(2) Bunkering hub and chokepoint disruptions, (3) Forward outlook and "
    "carrier surcharge trajectory. Plain prose, no markdown."
)


class OilNewsAgent(BaseAgent):
    """Global Oil News - PPTX output."""

    name = "oil"
    description = "Global Oil News"

    def load_config(self) -> Dict[str, Any]:
        with open(_AGENT_DIR / "config.yaml", encoding="utf-8") as f:
            agent_cfg = yaml.safe_load(f)
        with open(_AGENT_DIR / "regions.yaml", encoding="utf-8") as f:
            regions = yaml.safe_load(f)
        with open(_GLOBAL_CONFIG, encoding="utf-8") as f:
            global_settings = yaml.safe_load(f)
        # Merge global LLM/scraping settings into agent config
        agent_cfg["llm"] = global_settings.get("llm", {})
        agent_cfg["scraping"] = global_settings.get("scraping", {})
        return {"agent": agent_cfg, "regions": regions}

    def collect(self, config: Dict[str, Any]) -> List[Article]:
        agent_cfg = config["agent"]
        lookback = agent_cfg["agent"]["lookback_days"]
        all_articles: List[Article] = []
        seen: set[str] = set()

        # Google News queries
        for q in agent_cfg.get("queries", []):
            articles = fetch_google_news(
                query=q["query"], source_label=q["label"], lookback_days=lookback
            )
            for a in articles:
                key = a.title.lower()[:80]
                if key not in seen:
                    seen.add(key)
                    all_articles.append(a)

        # Manual articles
        manual_path = _AGENT_DIR / "input" / "manual_articles.yaml"
        if manual_path.exists():
            manual = load_manual_articles(manual_path)
            for a in manual:
                all_articles.append(a)

        all_articles.sort(key=lambda a: a.published_date, reverse=True)
        return all_articles[:agent_cfg["agent"]["max_articles"]]

    def filter_articles(self, articles: List[Article], config: Dict[str, Any]) -> List[Article]:
        clear_pattern_cache()
        return apply_filters(articles, config["regions"])

    def summarize(
        self, articles: List[Article], config: Dict[str, Any]
    ) -> tuple[List[Article], str]:
        # Uses shared summarizer with global LLM settings
        settings_wrapper = {"llm": config["agent"].get("llm", {})}
        summarized = summarize_all(articles, settings_wrapper)
        exec_summary = generate_executive_summary(summarized)
        return summarized, exec_summary

    def compose(
        self, articles: List[Article], exec_summary: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build a 7-slide PPTX deck."""
        from shared.pptx_builder import (
            create_presentation, add_blank_slide, title_slide, header_bar,
            txt, bullets, footer, rect, hline, scenario_box, status_card,
            VF_GREEN, VF_BURG, VF_GOLD, VF_DARK, VF_TAN, VF_LTGRAY,
            WHITE, RED_ALERT, AMBER, STABLE_GRN,
        )

        prs = create_presentation()

        # ── Slide 1: Title + KPI Dashboard ───────────────────────────────
        s1 = add_blank_slide(prs)
        title_slide(
            s1,
            "Global Marine Fuel Risk Report",
            f"Week of {self.date_str}  |  Operations Risk Monitor",
            "Sources: Ship & Bunker, Enterprise Singapore, S&P Global Platts, Argus, ENGINE",
        )

        # Exec summary bullets
        if exec_summary:
            paragraphs = [p.strip() for p in exec_summary.split("\n\n") if p.strip()]
            bullets(s1, 0.8, 1.6, 11.5, 4.0, paragraphs[:6], sz=11, bc=VF_BURG)

        footer(s1, f"Operations Risk Monitor - Oil Report - {self.date_str}")

        # ── Slide 2: Key Articles ────────────────────────────────────────
        s2 = add_blank_slide(prs)
        header_bar(s2, "Market Intelligence - Key Articles")

        y = 1.0
        for i, article in enumerate(articles[:12]):
            summary_text = article.summary or article.title
            region_tag = ", ".join(article.regions[:2]) if article.regions else "Global"
            txt(s2, 0.5, y, 1.5, 0.22, region_tag, sz=9, color=VF_BURG, bold=True)
            txt(s2, 2.2, y, 9.5, 0.22,
                f"{article.title[:80]}  -  {summary_text[:120]}", sz=10, color=VF_DARK)
            y += 0.28
            if y > 6.8:
                break

        footer(s2, f"Operations Risk Monitor - Oil Report - {self.date_str}")

        # ── Slide 3: Risk Signals ────────────────────────────────────────
        s3 = add_blank_slide(prs)
        header_bar(s3, "Risk Signals & Supply Disruptions")

        shortage = [a for a in articles if a.container_signal == "shortage"]
        general = [a for a in articles if a.container_signal == "general"]
        positive = [a for a in articles if a.container_signal == "surplus"]

        y = 1.0
        if shortage:
            txt(s3, 0.5, y, 4, 0.25, "HIGH RISK - Active Disruptions", sz=12, color=RED_ALERT, bold=True)
            y += 0.35
            items = [f"[{a.regions[0] if a.regions else 'Global'}] {a.summary or a.title}"[:120] for a in shortage[:6]]
            bullets(s3, 0.5, y, 12, 2.0, items, sz=10, bc=RED_ALERT)
            y += len(items) * 0.28 + 0.3

        if general:
            txt(s3, 0.5, y, 4, 0.25, "MONITORING - Developments", sz=12, color=AMBER, bold=True)
            y += 0.35
            items = [f"[{a.regions[0] if a.regions else 'Global'}] {a.summary or a.title}"[:120] for a in general[:6]]
            bullets(s3, 0.5, y, 12, 2.0, items, sz=10, bc=AMBER)
            y += len(items) * 0.28 + 0.3

        if positive:
            txt(s3, 0.5, y, 4, 0.25, "POSITIVE - Recovery Signals", sz=12, color=STABLE_GRN, bold=True)
            y += 0.35
            items = [f"[{a.regions[0] if a.regions else 'Global'}] {a.summary or a.title}"[:120] for a in positive[:4]]
            bullets(s3, 0.5, y, 12, 1.5, items, sz=10, bc=STABLE_GRN)

        footer(s3, f"Operations Risk Monitor - Oil Report - {self.date_str}")

        # ── Slide 4: Forward Scenarios ───────────────────────────────────
        s4 = add_blank_slide(prs)
        header_bar(s4, "Forward Scenarios - Next 8 Weeks")

        scenario_box(s4, 0.5, 1.2, 3.8, 3.5, "BASE CASE",
            "Partial stabilisation. Brent retreats below $90. "
            "VLSFO retreats to $700–800/mt range. "
            "Surcharges remain but reduce. "
            "Bunkering hubs resume normal operations.",
            VF_GREEN)
        scenario_box(s4, 4.7, 1.2, 3.8, 3.5, "STRESS CASE",
            "Prolonged disruption (8+ weeks). "
            "VLSFO holds $1,000–1,200/mt. "
            "Lead times extend. Cape rerouting "
            "accelerates fuel demand spiral. "
            "Spot bunker premiums widen.",
            AMBER)
        scenario_box(s4, 8.9, 1.2, 3.8, 3.5, "SHOCK CASE",
            "Infrastructure destruction. Major refinery "
            "or storage permanently damaged. "
            "VLSFO could exceed $1,500/mt. "
            "Rationing and allocation begin. "
            "Full supply chain disruption.",
            RED_ALERT)

        txt(s4, 0.5, 5.0, 12, 0.3,
            "Note: Scenarios are illustrative and based on current intelligence. "
            "Update with actual price data when available.",
            sz=9, color=VF_TAN)

        footer(s4, f"Operations Risk Monitor - Oil Report - {self.date_str}")

        # ── Slide 5: Recommended Actions ─────────────────────────────────
        s5 = add_blank_slide(prs)
        header_bar(s5, "Implications & Recommended Actions")

        actions = [
            "IMMEDIATE: Quantify freight cost exposure - model VLSFO impact on all active shipping contracts",
            "IMMEDIATE: Review carrier Emergency Fuel Surcharge notifications against open POs",
            "THIS WEEK: Monitor Singapore & Fujairah inventory trends for forward depletion signals",
            "THIS WEEK: Assess Cape rerouting impact on key coffee origin lanes (Brazil, Vietnam, East Africa)",
            "30 DAYS: Establish fuel contingency framework with pre-agreed surcharge caps",
            "30 DAYS: Watch chokepoint status for recovery signals (Brent backwardation, crack spread narrowing)",
        ]
        bullets(s5, 0.5, 1.2, 12, 5.0, actions, sz=11, bc=VF_GOLD)

        footer(s5, f"Operations Risk Monitor - Oil Report - {self.date_str}")

        return {"pptx": prs}

    def save(self, artifacts: Dict[str, Any], config: Dict[str, Any]) -> List[Path]:
        output_dir = Path(config["agent"]["output"]["directory"])
        output_dir.mkdir(parents=True, exist_ok=True)
        pattern = config["agent"]["output"]["filename_pattern"]
        filename = pattern.format(date=self.date_str)
        path = output_dir / filename
        artifacts["pptx"].save(str(path))
        self.logger.info("Saved: %s", path)
        return [path]
