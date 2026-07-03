"""
DieselRisksAgent - European Diesel Price Monitor & Supply Chain Risk Assessment.

Tracks automotive gas oil (AGO) prices across DE/BE/IT/ES, trucking fuel
surcharges, strike risk, and policy divergence. Produces a 7-slide PPTX
risk report.

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

_AGENT_DIR = Path(__file__).parent / "diesel"
_GLOBAL_CONFIG = Path(__file__).parent.parent / "config" / "settings.yaml"


class DieselRisksAgent(BaseAgent):
    """European Diesel Risks - PPTX output."""

    name = "diesel"
    description = "European Diesel Risks"

    def load_config(self) -> Dict[str, Any]:
        with open(_AGENT_DIR / "config.yaml", encoding="utf-8") as f:
            agent_cfg = yaml.safe_load(f)
        with open(_AGENT_DIR / "regions.yaml", encoding="utf-8") as f:
            regions = yaml.safe_load(f)
        with open(_GLOBAL_CONFIG, encoding="utf-8") as f:
            global_settings = yaml.safe_load(f)
        agent_cfg["llm"] = global_settings.get("llm", {})
        agent_cfg["scraping"] = global_settings.get("scraping", {})
        return {"agent": agent_cfg, "regions": regions}

    def collect(self, config: Dict[str, Any]) -> List[Article]:
        agent_cfg = config["agent"]
        lookback = agent_cfg["agent"]["lookback_days"]
        all_articles: List[Article] = []
        seen: set[str] = set()

        for q in agent_cfg.get("queries", []):
            articles = fetch_google_news(
                query=q["query"], source_label=q["label"], lookback_days=lookback
            )
            for a in articles:
                key = a.title.lower()[:80]
                if key not in seen:
                    seen.add(key)
                    all_articles.append(a)

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
            WHITE, RED_ALERT, AMBER, STABLE_GRN, VF_OLIVE,
        )
        from pptx.enum.text import PP_ALIGN

        prs = create_presentation()
        countries = config["agent"].get("countries", [])

        # ── Slide 1: Title + KPI Dashboard ───────────────────────────────
        s1 = add_blank_slide(prs)
        title_slide(
            s1,
            "European Diesel Price Monitor",
            f"Supply Chain Risk Assessment  ·  {self.date_str}",
            f"Automotive Gas Oil · €/1,000 L incl. taxes · Sources: EU Oil Bulletin + Fuelo.eu",
        )

        # Country KPI placeholders
        kpi_x = 0.8
        for c in countries:
            txt(s1, kpi_x, 1.6, 2.5, 0.25, f"{c['flag']}  {c['name']}",
                sz=14, color=VF_GREEN, bold=True)
            txt(s1, kpi_x, 1.9, 2.5, 0.3, "- data pending -",
                sz=11, color=VF_TAN)
            kpi_x += 3.0

        if exec_summary:
            paragraphs = [p.strip() for p in exec_summary.split("\n\n") if p.strip()]
            bullets(s1, 0.8, 2.6, 11.5, 3.5, paragraphs[:4], sz=11, bc=VF_BURG)

        footer(s1, f"Operations Risk Monitor - Diesel Report - {self.date_str}")

        # ── Slide 2: Key Articles ────────────────────────────────────────
        s2 = add_blank_slide(prs)
        header_bar(s2, "Market Intelligence - Key Articles")

        y = 1.0
        for article in articles[:12]:
            summary_text = article.summary or article.title
            region_tag = ", ".join(article.regions[:2]) if article.regions else "Europe"
            txt(s2, 0.5, y, 1.5, 0.22, region_tag, sz=9, color=VF_BURG, bold=True)
            txt(s2, 2.2, y, 9.5, 0.22,
                f"{article.title[:80]}  -  {summary_text[:120]}", sz=10, color=VF_DARK)
            y += 0.28
            if y > 6.8:
                break

        footer(s2, f"Operations Risk Monitor - Diesel Report - {self.date_str}")

        # ── Slide 3: Supply Chain Risk Register ──────────────────────────
        s3 = add_blank_slide(prs)
        header_bar(s3, "Supply Chain Risk Register")

        risks = [
            ("CRITICAL", "Trucking Strikes & Driver Walkouts",
             "Owner-operators face direct margin compression. Historical precedent shows "
             "strike action triggers within 6-8 weeks of >20% sustained surges.", RED_ALERT),
            ("CRITICAL", "Shipper Insolvency & Capacity Loss",
             "Small-to-mid hauliers on 2-4% net margins cannot absorb sustained shocks. "
             "Insolvency risk highest in DE and IT.", RED_ALERT),
            ("HIGH", "Fuel Surcharge Renegotiations",
             "Standard logistics contracts include indexed fuel clauses activating above "
             "10-15% movement thresholds.", AMBER),
            ("HIGH", "Port & Cross-Dock Congestion",
             "Modal shift from road to rail/short-sea overloads alternative hubs. "
             "Hamburg, Rotterdam, Genoa risk dwell-time spikes.", AMBER),
        ]

        y = 1.2
        for severity, title, desc, color in risks:
            rect(s3, 0.5, y, 12.3, 0.04, color)
            txt(s3, 0.5, y + 0.1, 1.5, 0.22, severity, sz=10, color=color, bold=True)
            txt(s3, 2.2, y + 0.1, 4, 0.22, title, sz=11, color=VF_DARK, bold=True)
            txt(s3, 2.2, y + 0.38, 10, 0.4, desc, sz=10, color=VF_DARK)
            y += 1.0

        footer(s3, f"Operations Risk Monitor - Diesel Report - {self.date_str}")

        # ── Slide 4: Forward Scenarios ───────────────────────────────────
        s4 = add_blank_slide(prs)
        header_bar(s4, "Forward Scenarios - Next 8 Weeks")

        scenario_box(s4, 0.5, 1.2, 3.8, 3.5, "BASE CASE",
            "Brent retreats below €90/bbl. EU member states "
            "implement targeted diesel rebates. Prices plateau "
            "and begin slow reversal by mid-April. "
            "Fuel surcharges renegotiated within existing frameworks.",
            VF_GREEN)
        scenario_box(s4, 4.7, 1.2, 3.8, 3.5, "STRESS CASE",
            "Blockade persists 8+ weeks. Brent holds €95-105/bbl. "
            "No government fuel shields in DE/IT. Isolated trucker "
            "strikes cause 3-7 day disruptions. "
            "Spot capacity tightens 15-20%.",
            AMBER)
        scenario_box(s4, 8.9, 1.2, 3.8, 3.5, "SHOCK CASE",
            "Brent crosses €115/bbl. Coordinated strikes across "
            "DE, IT, ES. Shipper insolvencies surge; spot capacity "
            "falls 25-30%. Port congestion at Hamburg and Genoa. "
            "Emergency price controls create grey markets.",
            RED_ALERT)

        footer(s4, f"Operations Risk Monitor - Diesel Report - {self.date_str}")

        # ── Slide 5: Recommended Actions ─────────────────────────────────
        s5 = add_blank_slide(prs)
        header_bar(s5, "Recommended Actions")

        actions = [
            "IMMEDIATE: Review all fuel surcharge clauses - all active DE/IT/ES logistics contracts",
            "IMMEDIATE: Pre-book priority lanes 4-6 weeks ahead (DE-Hamburg, IT-Genoa, ES-Rotterdam)",
            "THIS WEEK: Model freight cost impact on delivered margins using Base/Stress/Shock projections",
            "THIS WEEK: Increase safety stock at Northern EU warehouses (+10-15 days buffer)",
            "30 DAYS: Establish contingency carrier agreements - qualify 1-2 backup carriers per lane",
            "30 DAYS: Monitor strike indicators and government fuel policy responses in DE, IT, ES",
        ]
        bullets(s5, 0.5, 1.2, 12, 5.0, actions, sz=11, bc=VF_GOLD)

        footer(s5, f"Operations Risk Monitor - Diesel Report - {self.date_str}")

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
