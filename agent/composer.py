"""
Composer - assembles the final newsletter from tagged, summarised articles
and renders it using the Jinja2 HTML and Markdown templates.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from agent.rss_aggregator import Article

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_LOGO_PATH = Path(__file__).parent.parent / "input" / "logo@2x.png"


def _load_logo_data_url() -> str:
    """Return the brand logo as a base64 data URL, or empty string if not found.

    Drop your own logo at input/logo@2x.png to brand the report.
    """
    if not _LOGO_PATH.exists():
        return ""
    mime = "image/png" if _LOGO_PATH.suffix.lower() == ".png" else "image/jpeg"
    data = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
    return f"data:{mime};base64,{data}"

# Canonical display order for regions
_REGION_ORDER = ["east_africa", "central_america", "brazil", "north_europe", "vietnam"]

_REGION_PORTS_HINT = {
    "east_africa":     "Mombasa, Dar es Salaam",
    "central_america": "Colón, Puerto Limón",
    "brazil":          "Santos, Paranaguá",
    "north_europe":    "Rotterdam, Hamburg, Antwerp",
    "vietnam":         "Ho Chi Minh City, Hai Phong",
}

# Carrier display names matching sources.yaml keys
_CARRIER_ORDER = ["msc", "hapag_lloyd", "cma_cgm", "maersk", "evergreen", "cosco"]


def _build_jinja_env(autoescape: bool = True) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]) if autoescape else False,
    )


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


def build_template_context(
    articles: List[Article],
    executive_summary: str,
    regions_config: dict,
    sources_config: dict,
    settings: dict,
) -> dict:
    """
    Transform flat article list + config into the nested dict expected
    by the Jinja2 templates.

    Returns a dict with keys: week_label, generated_at, executive_summary,
    regions, carriers, container_watch, sources_consulted.
    """
    now = datetime.now(timezone.utc)
    agent_cfg: dict = settings.get("agent", {})
    max_per_region: int = agent_cfg.get("max_articles_per_region", 5)
    max_per_carrier: int = agent_cfg.get("max_articles_per_carrier", 3)
    max_container: int = agent_cfg.get("max_container_articles", 6)

    # ── Week label ────────────────────────────────────────────────────────────
    week_label = f"Week of {now.strftime('%d %b %Y')}"
    generated_at = now.strftime("%Y-%m-%d %H:%M UTC")

    # ── Regional sections ─────────────────────────────────────────────────────
    regions_data = regions_config.get("regions", {})
    regions_list = []
    for region_key in _REGION_ORDER:
        region_cfg = regions_data.get(region_key, {})
        display_name = region_cfg.get("display_name", region_key.replace("_", " ").title())
        region_articles = [
            _article_to_dict(a)
            for a in articles
            if region_key in a.regions
        ][:max_per_region]
        regions_list.append({
            "key": region_key,
            "display_name": display_name,
            "ports_hint": _REGION_PORTS_HINT.get(region_key, ""),
            "articles": region_articles,
        })

    # ── Carrier sections ──────────────────────────────────────────────────────
    shipping_line_cfg: dict = sources_config.get("shipping_lines", {})
    carriers_list = []
    for carrier_key in _CARRIER_ORDER:
        cfg = shipping_line_cfg.get(carrier_key)
        if not cfg:
            continue
        display_name: str = cfg.get("name", carrier_key.upper())
        # Carrier name variants to search in title/source (e.g. "Hapag-Lloyd", "Maersk")
        short_name = display_name.split(" - ")[0].split("(")[0].strip().lower()
        carrier_articles = [
            _article_to_dict(a)
            for a in articles
            if short_name in a.title.lower() or short_name in a.source.lower()
        ][:max_per_carrier]
        carriers_list.append({
            "key": carrier_key,
            "name": display_name,
            "articles": carrier_articles,
        })

    # ── Container availability watch ──────────────────────────────────────────
    container_shortage = []
    container_surplus = []
    container_general = []

    for article in articles:
        if article.container_signal is None:
            continue
        # Determine the best region label for display
        if article.regions:
            region_display = regions_data.get(article.regions[0], {}).get(
                "display_name", article.regions[0].replace("_", " ").title()
            )
        else:
            region_display = "Global"

        item = {
            "region": region_display,
            "summary": article.summary or article.title,
            "url": article.url,
            "source": article.source,
        }
        if article.container_signal == "shortage":
            container_shortage.append(item)
        elif article.container_signal == "surplus":
            container_surplus.append(item)
        else:
            container_general.append(item)

    container_watch = {
        "shortage": container_shortage[:max_container],
        "surplus": container_surplus[:max_container],
        "general": container_general[:max_container],
    }

    # ── Sources consulted ─────────────────────────────────────────────────────
    seen_sources: set[str] = set()
    sources_consulted = []
    for article in articles:
        if article.source not in seen_sources:
            seen_sources.add(article.source)
            sources_consulted.append({
                "name": article.source,
                "url": article.url,  # Best available link for this source
            })

    return {
        "week_label": week_label,
        "generated_at": generated_at,
        "executive_summary": executive_summary,
        "regions": regions_list,
        "carriers": carriers_list,
        "container_watch": container_watch,
        "sources_consulted": sources_consulted,
        "logo_src": _load_logo_data_url(),
    }


def render_html(context: dict) -> str:
    """Render the HTML newsletter from the template context."""
    env = _build_jinja_env(autoescape=True)
    template = env.get_template("newsletter.html.j2")
    return template.render(**context)


def render_markdown(context: dict) -> str:
    """Render the Markdown newsletter from the template context."""
    env = _build_jinja_env(autoescape=False)
    template = env.get_template("newsletter.md.j2")
    return template.render(**context)


async def render_pdf(html_path: Path, pdf_path: Path) -> None:
    """
    Render the HTML newsletter to PDF using Playwright (Chromium).

    Args:
        html_path: Absolute path to the saved HTML file.
        pdf_path: Destination path for the PDF file.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            margin={"top": "16mm", "bottom": "16mm", "left": "12mm", "right": "12mm"},
            print_background=True,
        )
        await browser.close()
    logger.info("PDF saved: %s", pdf_path)


def save_outputs(
    html_content: str,
    md_content: str,
    output_dir: str,
    date_str: str,
) -> Tuple[Path, Path]:
    """
    Write HTML and Markdown files to output_dir.

    Args:
        html_content: Rendered HTML string.
        md_content: Rendered Markdown string.
        output_dir: Directory to write to.
        date_str: ISO date string for filenames (e.g. '2025-07-07').

    Returns:
        Tuple of (html_path, md_path).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / f"sc-risk-report-{date_str}.html"
    md_path = out / f"sc-risk-report-{date_str}.md"

    html_path.write_text(html_content, encoding="utf-8")
    md_path.write_text(md_content, encoding="utf-8")
    logger.info("Risk report saved: %s", html_path)

    return html_path, md_path
