"""
CongestionAgent - Port Congestion Index Monitor.

Tracks vessel waiting times, berth utilisation, dwell times, blank sailings,
and container equipment availability across key ports. Produces a 6-slide
PPTX congestion dashboard.

Pipeline: Google News + port authority/MarineTraffic scraping + manual input → PPTX
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

import yaml

from agent.rss_aggregator import Article, fetch_google_news
from agent.filter import apply_filters, clear_pattern_cache
from agent.summarizer import summarize_all, generate_executive_summary
from agent.manual_loader import load_manual_articles
from agents.base import BaseAgent

logger = logging.getLogger(__name__)

_AGENT_DIR = Path(__file__).parent / "congestion"
_GLOBAL_CONFIG = Path(__file__).parent.parent / "config" / "settings.yaml"


class CongestionAgent(BaseAgent):
    """Port Congestion Index - PPTX output."""

    name = "congestion"
    description = "Port Congestion Index"

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
            for a in load_manual_articles(manual_path):
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
        severity_cfg = config["agent"].get("severity", {})
        text = f"{article.title} {article.summary or ''} {article.raw_text or ''}".lower()
        for level in ("critical", "high", "monitoring"):
            keywords = severity_cfg.get(level, {}).get("keywords", [])
            if any(kw.lower() in text for kw in keywords):
                return level
        return "monitoring"

    def compose(
        self, articles: List[Article], exec_summary: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        from shared.pptx_builder import (
            create_presentation, add_blank_slide, title_slide, header_bar,
            txt, bullets, footer, rect, hline,
            VF_GREEN, VF_BURG, VF_GOLD, VF_DARK, VF_TAN, VF_LTGRAY,
            WHITE, RED_ALERT, AMBER, STABLE_GRN, VF_OLIVE,
        )
        from pptx.enum.text import PP_ALIGN

        prs = create_presentation()
        ports = config["agent"].get("ports", [])

        critical = [a for a in articles if self._classify_severity(a, config) == "critical"]
        high = [a for a in articles if self._classify_severity(a, config) == "high"]

        # ── Slide 1: Title + Congestion KPIs ─────────────────────────────
        s1 = add_blank_slide(prs)
        title_slide(
            s1,
            "Port Congestion Index",
            f"Week of {self.date_str}  |  Operations Risk Monitor",
            f"Monitoring {len(ports)} ports across 5 trade lanes  |  Sources: port authorities, MarineTraffic, carrier advisories",
        )

        kpis = [
            (f"{len(critical)}", "Critical Ports", RED_ALERT),
            (f"{len(high)}", "Congested", AMBER),
            (f"{len(ports) - len(critical) - len(high)}", "Normal", STABLE_GRN),
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

        footer(s1, f"Operations Risk Monitor - Congestion Report - {self.date_str}")

        # ── Slide 2: Port Status Matrix ──────────────────────────────────
        s2 = add_blank_slide(prs)
        header_bar(s2, "Port Status Matrix")

        # Table header
        for label, cx, cw in [("Port", 0.5, 2.5), ("Country", 3.2, 1.5),
                                ("Region", 4.9, 2.0), ("Status", 7.1, 1.5),
                                ("Signals", 8.8, 3.5)]:
            txt(s2, cx, 1.0, cw, 0.25, label, sz=10, color=VF_GREEN, bold=True)
        hline(s2, 0.5, 1.28, 12, VF_TAN)

        y = 1.4
        for port in ports:
            port_name = port["name"]
            region_key = port.get("region", "")
            port_articles = [a for a in articles
                           if port_name.lower() in (a.title + " " + (a.raw_text or "")).lower()]
            status = ("CRITICAL" if any(self._classify_severity(a, config) == "critical" for a in port_articles) else
                     "HIGH" if any(self._classify_severity(a, config) == "high" for a in port_articles) else
                     "OK" if not port_articles else "MONITORING")
            sev_color = (RED_ALERT if status == "CRITICAL" else AMBER if status == "HIGH" else
                        VF_OLIVE if status == "MONITORING" else STABLE_GRN)

            bg = VF_LTGRAY if ports.index(port) % 2 == 0 else WHITE
            rect(s2, 0.5, y - 0.02, 12, 0.28, bg)
            txt(s2, 0.5, y, 2.5, 0.22, port_name, sz=10, color=VF_DARK, bold=True)
            txt(s2, 3.2, y, 1.5, 0.22, port.get("country", ""), sz=10, color=VF_DARK)
            txt(s2, 4.9, y, 2.0, 0.22, region_key.replace("_", " ").title(), sz=10, color=VF_TAN)
            txt(s2, 7.1, y, 1.5, 0.22, status, sz=10, color=sev_color, bold=True)
            detail = f"{len(port_articles)} article(s)" if port_articles else "-"
            txt(s2, 8.8, y, 3.5, 0.22, detail, sz=10, color=VF_DARK)
            y += 0.28
            if y > 6.8:
                break

        footer(s2, f"Operations Risk Monitor - Congestion Report - {self.date_str}")

        # ── Slide 3: Regional Detail ─────────────────────────────────────
        s3 = add_blank_slide(prs)
        header_bar(s3, "Regional Congestion Detail")

        regions_config = config["regions"].get("regions", {})
        y = 1.0
        for region_key, region_cfg in regions_config.items():
            region_name = region_cfg.get("display_name", region_key)
            region_articles = [a for a in articles if region_key in a.regions]
            if not region_articles:
                continue

            txt(s3, 0.5, y, 4, 0.25, region_name, sz=12, color=VF_GREEN, bold=True)
            y += 0.3
            for a in region_articles[:3]:
                sev = self._classify_severity(a, config)
                sev_color = RED_ALERT if sev == "critical" else AMBER if sev == "high" else VF_OLIVE
                txt(s3, 0.5, y, 1.0, 0.22, sev.upper(), sz=9, color=sev_color, bold=True)
                txt(s3, 1.6, y, 10.5, 0.22,
                    f"{a.title[:70]}  -  {(a.summary or '')[:80]}", sz=10, color=VF_DARK)
                y += 0.26
            y += 0.2
            if y > 6.5:
                break

        footer(s3, f"Operations Risk Monitor - Congestion Report - {self.date_str}")

        # ── Slide 4: Schedule Reliability & Blank Sailings ───────────────
        s4 = add_blank_slide(prs)
        header_bar(s4, "Schedule Reliability & Blank Sailings")

        schedule_articles = [a for a in articles if any(kw in (a.title + " " + (a.summary or "")).lower()
                            for kw in ("blank sailing", "schedule", "reliability", "rolling", "delay"))]
        if schedule_articles:
            items = [f"[{', '.join(a.regions[:2]) or 'Global'}] {a.summary or a.title}"[:130]
                     for a in schedule_articles[:10]]
            bullets(s4, 0.5, 1.2, 12, 5.0, items, sz=11, bc=AMBER)
        else:
            txt(s4, 0.5, 1.2, 12, 0.3,
                "No blank sailing or schedule reliability alerts this week.", sz=12, color=VF_TAN)

        footer(s4, f"Operations Risk Monitor - Congestion Report - {self.date_str}")

        # ── Slide 5: Recommended Actions ─────────────────────────────────
        s5 = add_blank_slide(prs)
        header_bar(s5, "Recommended Mitigations")

        actions = [
            "IMMEDIATE: Check vessel ETAs at any port showing CRITICAL or HIGH congestion",
            "IMMEDIATE: Pre-book capacity on congested lanes to avoid roll-overs",
            "THIS WEEK: Review carrier blank sailing schedules for the next 4 weeks",
            "THIS WEEK: Assess container equipment availability at origin ports (20ft/40ft stock)",
            "30 DAYS: Consider merchant haulage at ports where carrier haulage is delayed",
            "30 DAYS: Diversify transshipment hub selection to avoid single-point-of-failure congestion",
        ]
        bullets(s5, 0.5, 1.2, 12, 5.0, actions, sz=11, bc=VF_GOLD)

        footer(s5, f"Operations Risk Monitor - Congestion Report - {self.date_str}")

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
