"""
Conflict sub-agent - focused pipeline for Middle East conflict impact on
ocean freight routing decisions.

Reuses the core pipeline modules (RSS, filter, summarizer, composer) but
with conflict-specific Google News queries, manual article loading, and
dedicated templates.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from agent.rss_aggregator import Article, fetch_google_news
from agent.filter import apply_filters
from agent.summarizer import summarize_all
from agent.manual_loader import load_manual_articles, load_weekly_briefing

logger = logging.getLogger(__name__)

_CONFLICT_INPUT = Path(__file__).parent.parent / "input" / "conflict_articles.yaml"

# ── Region / carrier display config for conflict newsletter ──────────────────

_CONFLICT_REGION_ORDER = ["middle_east_conflict"]

_CHOKEPOINT_HINTS = {
    "middle_east_conflict": "Red Sea, Suez, Strait of Hormuz, Gulf of Aden",
}

_CARRIER_ORDER = ["msc", "hapag_lloyd", "cma_cgm", "maersk", "evergreen", "cosco"]


# ── Conflict-specific Google News queries ────────────────────────────────────

def _conflict_queries(lookback_days: int = 7) -> List[tuple[str, str]]:
    """Return (query_string, label) pairs for conflict-focused news collection."""
    w = f"when:{lookback_days}d"
    return [
        (
            f'"Red Sea" shipping (attack OR disruption OR diversion OR Houthi) {w}',
            "Red Sea Disruption",
        ),
        (
            f'"Strait of Hormuz" OR "Persian Gulf" shipping (Iran OR blockade OR threat OR naval) {w}',
            "Strait of Hormuz",
        ),
        (
            f'("Suez Canal" OR "Cape of Good Hope") (rerouting OR diversion OR transit) shipping freight {w}',
            "Suez / Cape Routing",
        ),
        (
            f'(Maersk OR MSC OR "Hapag-Lloyd" OR "CMA CGM") ("Red Sea" OR "Suez" OR "Middle East") routing {w}',
            "Carrier Routing Decisions",
        ),
        (
            f'"war risk premium" OR "shipping insurance" ("Red Sea" OR "Middle East" OR Iran) {w}',
            "War Risk & Insurance",
        ),
    ]


def collect_conflict_news(lookback_days: int = 7) -> List[Article]:
    """Fetch articles from conflict-specific Google News queries."""
    all_articles: List[Article] = []
    seen_urls: set[str] = set()

    for query, label in _conflict_queries(lookback_days=lookback_days):
        articles = fetch_google_news(
            query=query,
            source_label=label,
            lookback_days=lookback_days,
        )
        for a in articles:
            if a.url not in seen_urls:
                seen_urls.add(a.url)
                all_articles.append(a)

    logger.info("Conflict news collection: %d unique articles", len(all_articles))
    return all_articles


# ── Context builder for conflict templates ───────────────────────────────────

def _format_date(dt: datetime) -> str:
    return dt.strftime("%d %b %Y")


def _article_to_dict(article: Article) -> dict:
    return {
        "title": article.title,
        "url": article.url,
        "source": article.source,
        "published_date": _format_date(article.published_date),
        "summary": article.summary or article.title,
    }


def build_conflict_context(
    articles: List[Article],
    executive_summary: str,
    regions_config: dict,
    sources_config: dict,
    settings: dict,
) -> dict:
    """Build the template context for the conflict-focused newsletter."""
    now = datetime.now(timezone.utc)
    agent_cfg: dict = settings.get("agent", {})
    max_per_carrier: int = agent_cfg.get("max_articles_per_carrier", 3)

    week_label = f"Week of {now.strftime('%d %b %Y')}"

    # ── Conflict region articles ─────────────────────────────────────────
    regions_data = regions_config.get("regions", {})
    conflict_articles = [a for a in articles if "middle_east_conflict" in a.regions]

    regions_list = []
    for region_key in _CONFLICT_REGION_ORDER:
        region_cfg = regions_data.get(region_key, {})
        display_name = region_cfg.get("display_name", region_key.replace("_", " ").title())
        region_arts = [
            _article_to_dict(a)
            for a in articles
            if region_key in a.regions
        ][:10]  # Allow more articles for the focused newsletter
        regions_list.append({
            "key": region_key,
            "display_name": display_name,
            "ports_hint": _CHOKEPOINT_HINTS.get(region_key, ""),
            "articles": region_arts,
        })

    # ── Carrier sections ─────────────────────────────────────────────────
    shipping_line_cfg: dict = sources_config.get("shipping_lines", {})
    carriers_list = []
    for carrier_key in _CARRIER_ORDER:
        cfg = shipping_line_cfg.get(carrier_key)
        if not cfg:
            continue
        display_name: str = cfg.get("name", carrier_key.upper())
        short_name = display_name.split(" - ")[0].split("(")[0].strip().lower()
        carrier_arts = [
            _article_to_dict(a)
            for a in conflict_articles
            if short_name in a.title.lower() or short_name in a.source.lower()
        ][:max_per_carrier]
        carriers_list.append({
            "key": carrier_key,
            "name": display_name,
            "articles": carrier_arts,
        })

    # ── Risk categories (mapped to container_watch for template compat) ──
    risk_high = []
    risk_general = []
    risk_positive = []
    for article in conflict_articles:
        item = {
            "region": "Middle East",
            "summary": article.summary or article.title,
            "url": article.url,
            "source": article.source,
        }
        if article.container_signal == "shortage":
            risk_high.append(item)
        elif article.container_signal == "surplus":
            risk_positive.append(item)
        else:
            risk_general.append(item)

    container_watch = {
        "shortage": risk_high[:8],
        "surplus": risk_positive[:8],
        "general": risk_general[:8],
    }

    # ── Sources consulted ────────────────────────────────────────────────
    seen_sources: set[str] = set()
    sources_consulted = []
    for article in articles:
        if article.source not in seen_sources:
            seen_sources.add(article.source)
            sources_consulted.append({
                "name": article.source,
                "url": article.url,
            })

    from agent.composer import _load_logo_data_url
    return {
        "week_label": week_label,
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "executive_summary": executive_summary,
        "regions": regions_list,
        "carriers": carriers_list,
        "container_watch": container_watch,
        "sources_consulted": sources_consulted,
        "logo_src": _load_logo_data_url(),
    }


# ── Template rendering ───────────────────────────────────────────────────────

def render_conflict_html(context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    templates_dir = Path(__file__).parent.parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("conflict.html.j2")
    return template.render(**context)


def render_conflict_md(context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader
    templates_dir = Path(__file__).parent.parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=False,
    )
    template = env.get_template("conflict.md.j2")
    return template.render(**context)


# ── Main pipeline ────────────────────────────────────────────────────────────

async def run_conflict_pipeline(
    sources_config: dict,
    regions_config: dict,
    settings: dict,
    date_str: Optional[str] = None,
    no_scrape: bool = True,
    dry_run: bool = False,
) -> None:
    """Run the conflict-focused newsletter pipeline."""

    lookback_days: int = settings["agent"]["lookback_days"]

    # ── Step 1: Collect conflict-focused articles ────────────────────────
    logger.info("Conflict Agent - Step 1: Collecting conflict-focused news…")
    news_articles = collect_conflict_news(lookback_days=lookback_days)
    logger.info("  %d articles from Google News (conflict queries)", len(news_articles))

    # ── Step 1b: Manual articles from input/conflict_articles.yaml ───────
    manual_articles = load_manual_articles(_CONFLICT_INPUT)
    if manual_articles:
        logger.info("  %d manual conflict article(s) loaded", len(manual_articles))

    all_articles = news_articles + manual_articles

    # ── Step 2: Filter & tag ─────────────────────────────────────────────
    logger.info("Conflict Agent - Step 2: Filtering and tagging…")
    tagged = apply_filters(all_articles, regions_config)
    # Keep only articles that matched the conflict region
    conflict_tagged = [a for a in tagged if "middle_east_conflict" in a.regions]
    logger.info("  %d conflict-related articles after filtering", len(conflict_tagged))

    # Also keep articles that have a container signal even if not conflict-tagged
    # (they may mention Red Sea in general alerts)
    for a in tagged:
        if a not in conflict_tagged and a.container_signal:
            text = f"{a.title} {a.summary or ''}".lower()
            if any(kw in text for kw in ["red sea", "suez", "hormuz", "houthi", "iran", "middle east"]):
                conflict_tagged.append(a)

    logger.info("  %d total articles for conflict brief", len(conflict_tagged))

    # ── Step 3: Summarize ────────────────────────────────────────────────
    logger.info("Conflict Agent - Step 3: Summarizing…")
    summarized = summarize_all(conflict_tagged, settings)
    executive_summary = load_weekly_briefing(_CONFLICT_INPUT) or \
        "No manually written conflict briefing provided. See articles below for details."

    # ── Step 4: Compose ──────────────────────────────────────────────────
    logger.info("Conflict Agent - Step 4: Composing conflict brief…")
    context = build_conflict_context(
        summarized, executive_summary, regions_config, sources_config, settings
    )
    html_content = render_conflict_html(context)
    md_content = render_conflict_md(context)

    # ── Step 5: Output ───────────────────────────────────────────────────
    date_label = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_dir = Path(settings["output"]["directory"])
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("Conflict Agent - Step 5: Dry run - printing to stdout…")
        print(md_content)
    else:
        logger.info("Conflict Agent - Step 5: Saving output…")
        html_path = output_dir / f"conflict-brief-{date_label}.html"
        md_path = output_dir / f"conflict-brief-{date_label}.md"
        html_path.write_text(html_content, encoding="utf-8")
        md_path.write_text(md_content, encoding="utf-8")
        logger.info("Conflict brief saved: %s", html_path)

        # PDF
        pdf_path = html_path.with_suffix(".pdf")
        try:
            from agent.composer import render_pdf
            await render_pdf(html_path, pdf_path)
        except Exception as exc:
            logger.error("PDF generation failed: %s", exc)
            pdf_path = None

        print(f"\nConflict Brief saved:")
        print(f"  HTML:     {html_path}")
        print(f"  Markdown: {md_path}")
        if pdf_path:
            print(f"  PDF:      {pdf_path}")
