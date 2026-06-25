"""
Manual article loader - reads input/manual_articles.yaml and converts entries
into Article objects that are merged into the main pipeline before filtering.

Users add entries to that YAML file to inject articles from paywalled sources,
internal reports, or anything not surfaced by Google News.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import yaml

from agent.rss_aggregator import Article

logger = logging.getLogger(__name__)

_DEFAULT_INPUT = Path(__file__).parent.parent / "input" / "manual_articles.yaml"

_VALID_SIGNALS = {"shortage", "surplus", "general"}


def _parse_date(raw) -> datetime:
    """Parse a YAML date value into a timezone-aware datetime."""
    if raw is None:
        return datetime.now(timezone.utc)
    # PyYAML may already parse a bare date (2026-02-20) as datetime.date
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw
    try:
        from datetime import date as _date
        if isinstance(raw, _date):
            return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
    except Exception:
        pass
    # String: try ISO datetime then ISO date
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.warning("Could not parse date %r - defaulting to now", raw)
    return datetime.now(timezone.utc)


def load_manual_articles(input_file: Path | None = None) -> List[Article]:
    """
    Load manual articles from a YAML file, or scan the input directory
    for .eml files and parse them automatically.

    Args:
        input_file: Path to YAML file, or directory to scan for EML/YAML.
                    Defaults to input/manual_articles.yaml.

    Returns:
        List of Article objects ready to be merged into the main pipeline.
        Returns empty list if no input files found.
    """
    path = Path(input_file) if input_file else _DEFAULT_INPUT

    # If path is a directory, scan it for EML + YAML files
    if path.is_dir():
        return _load_from_directory(path)

    # If pointing to an .eml file directly
    if path.suffix.lower() == ".eml" and path.exists():
        from agent.eml_loader import load_eml
        return load_eml(path)

    if not path.exists():
        logger.debug("No manual articles file at %s - skipping", path)
        return []

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or not isinstance(data, dict):
        logger.debug("manual_articles.yaml is empty or invalid - skipping")
        return []

    entries = data.get("articles") or []
    if not entries:
        logger.debug("manual_articles.yaml has no article entries")
        return []

    articles: List[Article] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            logger.warning("manual_articles.yaml entry #%d is not a dict - skipping", i + 1)
            continue

        title = str(entry.get("title") or "").strip()
        if not title:
            logger.warning("manual_articles.yaml entry #%d missing title - skipping", i + 1)
            continue

        url = str(entry.get("url") or "").strip()
        source = str(entry.get("source") or "Manual").strip()
        published_date = _parse_date(entry.get("date"))
        summary = str(entry.get("summary") or "").strip()

        # Region: accept string or list
        raw_region = entry.get("region")
        if isinstance(raw_region, str):
            regions = [raw_region.strip()]
        elif isinstance(raw_region, list):
            regions = [str(r).strip() for r in raw_region if r]
        else:
            regions = []

        # Container signal
        raw_signal = str(entry.get("container_signal") or "").strip().lower()
        container_signal = raw_signal if raw_signal in _VALID_SIGNALS else None

        articles.append(Article(
            title=title,
            url=url,
            source=source,
            published_date=published_date,
            raw_text=summary,   # raw_text is used as input to summarizer if summary is empty
            summary=summary,    # pre-set so summarizer skips this article
            regions=regions,
            container_signal=container_signal,
        ))
        logger.debug("Loaded manual article: %s", title[:60])

    logger.info("Manual articles loaded: %d from %s", len(articles), path)
    return articles


def _load_from_directory(input_dir: Path) -> List[Article]:
    """Scan a directory for .eml and .yaml files and load all articles."""
    articles: List[Article] = []

    # Load EML files
    eml_files = sorted(input_dir.glob("*.eml"))
    if eml_files:
        from agent.eml_loader import load_all_eml
        eml_articles = load_all_eml(input_dir)
        articles.extend(eml_articles)
        logger.info("EML files: %d articles from %d file(s)", len(eml_articles), len(eml_files))

    # Load YAML files
    for yaml_path in sorted(input_dir.glob("*.yaml")) + sorted(input_dir.glob("*.yml")):
        yaml_articles = load_manual_articles(yaml_path)
        articles.extend(yaml_articles)

    return articles


def load_weekly_briefing(input_file: Path | None = None) -> Optional[str]:
    """
    Return the manually written weekly_briefing string from the YAML file,
    or None if not set. When present, this overrides the model-generated
    executive summary so the pipeline can run without API credits.
    """
    path = Path(input_file) if input_file else _DEFAULT_INPUT
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or not isinstance(data, dict):
        return None
    text = data.get("weekly_briefing") or ""
    return text.strip() or None
