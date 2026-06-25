"""
StrikesAgent - Strikes & Disruptions Monitor.

Monitors labour strikes, port blockades, customs slowdowns, union disputes,
and government shutdowns across key trade lanes. Produces a 6-slide PPTX
risk assessment.

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

_AGENT_DIR = Path(__file__).parent / "strikes"
_GLOBAL_CONFIG = Path(__file__).parent.parent / "config" / "settings.yaml"


class StrikesAgent(BaseAgent):
    """Strikes & Disruptions - PPTX output."""

    name = "strikes"
    description = "Strikes & Disruptions"

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

    def _classify_severity(self, article: Article, config: Dict[str, Any]) -> str:
        """Classify disruption severity: critical / high / monitoring."""
        risk_levels = config["agent"].get("risk_levels", {})
        text = f"{article.title} {article.summary or ''} {article.raw_text or ''}".lower()

        for level in ("critical", "high", "monitoring"):
            keywords = risk_levels.get(level, {}).get("keywords", [])
            if any(kw.lower() in text for kw in keywords):
                return level
        return "monitoring"

    def compose(
        self, articles: List[Article], exec_summary: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build a 6-slide PPTX deck."""
        from shared.pptx_builder import (
            create_presentation, add_blank_slide, title_slide, header_bar,
            txt, bullets, footer, rect, hline, scenario_box,
            VF_GREEN, VF_BURG, VF_GOLD, VF_DARK, VF_TAN, VF_LTGRAY,
            WHITE, RED_ALERT, AMBER, STABLE_GRN, VF_OLIVE,
        )
        from pptx.enum.text import PP_ALIGN

        prs = create_presentation()

        # Classify articles by severity
        critical = []
        high = []
        monitoring = []
        for a in articles:
            sev = self._classify_severity(a, config)
            if sev == "critical":
                critical.append(a)
            elif sev == "high":
                high.append(a)
            else:
                monitoring.append(a)

        # ── Slide 1: Title + Active Disruption Dashboard ─────────────────
        s1 = add_blank_slide(prs)
        title_slide(
            s1,
            "Strikes & Disruptions Monitor",
            f"Week of {self.date_str}  |  Operations Risk Navigator",
            "Labour strikes · Port blockades · Customs slowdowns · Union disputes",
        )

        # KPI cards
        kpis = [
            (f"{len(critical)}", "Critical", RED_ALERT),
            (f"{len(high)}", "High", AMBER),
            (f"{len(monitoring)}", "Monitoring", VF_OLIVE),
            (f"{len(articles)}", "Total Signals", VF_GREEN),
        ]
        kpi_x = 0.8
        for val, label, color in kpis:
            rect(s1, kpi_x, 1.5, 2.5, 1.0, VF_LTGRAY)
            rect(s1, kpi_x, 1.5, 2.5, 0.05, color)
            txt(s1, kpi_x, 1.7, 2.5, 0.4, val,
                sz=28, color=color, bold=True, align=PP_ALIGN.CENTER)
            txt(s1, kpi_x, 2.15, 2.5, 0.25, label,
                sz=10, color=VF_DARK, align=PP_ALIGN.CENTER)
            kpi_x += 3.0

        if exec_summary:
            paragraphs = [p.strip() for p in exec_summary.split("\n\n") if p.strip()]
            bullets(s1, 0.8, 2.8, 11.5, 3.5, paragraphs[:4], sz=11, bc=VF_BURG)

        footer(s1, f"Operations Risk Navigator - Strikes Report - {self.date_str}")

        # ── Slide 2: Active Disruptions by Region ────────────────────────
        s2 = add_blank_slide(prs)
        header_bar(s2, "Active Disruptions by Region")

        regions_config = config["regions"].get("regions", {})
        y = 1.0
        for region_key, region_cfg in regions_config.items():
            region_name = region_cfg.get("display_name", region_key)
            region_articles = [a for a in articles if region_key in a.regions]
            if not region_articles:
                continue

            txt(s2, 0.5, y, 3, 0.25, region_name, sz=12, color=VF_GREEN, bold=True)
            y += 0.3
            for a in region_articles[:4]:
                sev = self._classify_severity(a, config)
                sev_color = RED_ALERT if sev == "critical" else AMBER if sev == "high" else VF_OLIVE
                txt(s2, 0.5, y, 1.0, 0.22, sev.upper(), sz=9, color=sev_color, bold=True)
                txt(s2, 1.6, y, 10.5, 0.22,
                    f"{a.title[:70]}  -  {(a.summary or '')[:80]}", sz=10, color=VF_DARK)
                y += 0.26
            y += 0.2
            if y > 6.5:
                break

        footer(s2, f"Operations Risk Navigator - Strikes Report - {self.date_str}")

        # ── Slide 3: Trade Lane Impact Assessment ────────────────────────
        s3 = add_blank_slide(prs)
        header_bar(s3, "Trade Lane Impact Assessment")

        lanes = [
            ("East Africa → Europe", "Mombasa, Dar es Salaam", "east_africa"),
            ("Central America → Europe/US", "Colón, Puerto Cortes", "central_america"),
            ("Brazil → Europe/US", "Santos, Paranaguá", "brazil"),
            ("North Europe (inland)", "Hamburg, Rotterdam, Antwerp", "north_europe"),
            ("Vietnam → Europe", "HCMC, Hai Phong", "vietnam"),
        ]

        y = 1.2
        for lane_name, ports, region_key in lanes:
            affected = [a for a in articles if region_key in a.regions]
            severity = "CLEAR" if not affected else self._classify_severity(affected[0], config).upper()
            sev_color = (STABLE_GRN if severity == "CLEAR" else
                        RED_ALERT if severity == "CRITICAL" else
                        AMBER if severity == "HIGH" else VF_OLIVE)

            rect(s2, 0.5, y, 12.3, 0.04, sev_color)
            txt(s3, 0.5, y + 0.1, 3.5, 0.22, lane_name, sz=11, color=VF_DARK, bold=True)
            txt(s3, 4.2, y + 0.1, 3, 0.22, ports, sz=10, color=VF_TAN)
            txt(s3, 7.5, y + 0.1, 1.5, 0.22, severity,
                sz=10, color=sev_color, bold=True, align=PP_ALIGN.CENTER)
            detail = f"{len(affected)} signal(s)" if affected else "No disruptions detected"
            txt(s3, 9.2, y + 0.1, 3.5, 0.22, detail, sz=10, color=VF_DARK)
            y += 0.55

        footer(s3, f"Operations Risk Navigator - Strikes Report - {self.date_str}")

        # ── Slide 4: Forward Outlook ─────────────────────────────────────
        s4 = add_blank_slide(prs)
        header_bar(s4, "Forward Outlook - Strike Probability & Negotiations")

        txt(s4, 0.5, 1.0, 12, 0.3,
            "Assessment of upcoming disruption risk based on union activity, "
            "policy signals, and historical patterns",
            sz=11, color=VF_TAN)

        outlook_items = [
            "Monitor union communications in key disruption-prone regions",
            "Track government responses to fuel price / wage pressure",
            "Historical pattern: sustained cost pressure → strike action within 6-8 weeks",
            "Pre-position inventory and diversify carrier base on high-risk lanes",
            "Maintain contingency routing plans for each key trade lane",
        ]
        bullets(s4, 0.5, 1.5, 12, 4.0, outlook_items, sz=11, bc=VF_BURG)

        footer(s4, f"Operations Risk Navigator - Strikes Report - {self.date_str}")

        # ── Slide 5: Recommended Actions ─────────────────────────────────
        s5 = add_blank_slide(prs)
        header_bar(s5, "Recommended Mitigations")

        actions = [
            "IMMEDIATE: Map all active/threatened disruptions against open shipments and ETAs",
            "IMMEDIATE: Activate contingency routing for any lane with CRITICAL disruption status",
            "THIS WEEK: Pre-book capacity on alternative lanes to avoid disrupted corridors",
            "THIS WEEK: Notify customers on affected lanes of potential delays and ETA revisions",
            "30 DAYS: Qualify backup carriers and alternative ports for each key origin",
            "30 DAYS: Review force majeure clauses in logistics contracts for strike coverage",
        ]
        bullets(s5, 0.5, 1.2, 12, 5.0, actions, sz=11, bc=VF_GOLD)

        footer(s5, f"Operations Risk Navigator - Strikes Report - {self.date_str}")

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
