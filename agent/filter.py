"""
Filter - tags articles by target region and container availability signal.

Operates on normalised Article objects and mutates them in-place by
setting .regions and .container_signal fields.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from agent.rss_aggregator import Article

logger = logging.getLogger(__name__)

# Pre-compiled pattern cache: {region_key: compiled_regex}
_region_patterns: dict[str, re.Pattern] = {}
# Pre-compiled container signal patterns
_container_patterns: dict[str, re.Pattern] = {}


def _build_region_pattern(region_cfg: dict) -> re.Pattern:
    """Build a single compiled regex from all region keywords (countries, ports, keywords)."""
    terms: List[str] = []
    for field in ("countries", "ports", "keywords"):
        terms.extend(region_cfg.get(field, []))
    # Escape and join with word-boundary anchors where appropriate
    escaped = [re.escape(t.strip()) for t in terms if t.strip()]
    pattern_str = r"(?i)\b(" + "|".join(escaped) + r")\b"
    return re.compile(pattern_str)


def _build_container_patterns(container_cfg: dict) -> dict[str, re.Pattern]:
    """Build compiled regex patterns for each container signal type."""
    patterns: dict[str, re.Pattern] = {}
    for signal_type, keywords in container_cfg.items():
        # Map yaml key names to canonical signal type
        if "shortage" in signal_type:
            canonical = "shortage"
        elif "surplus" in signal_type:
            canonical = "surplus"
        else:
            canonical = "general"
        escaped = [re.escape(kw.strip()) for kw in keywords if kw.strip()]
        if escaped:
            p = re.compile(r"(?i)\b(" + "|".join(escaped) + r")\b")
            # Merge into existing pattern for this canonical type
            if canonical in patterns:
                # Combine patterns: extend with new alternation
                existing_src = patterns[canonical].pattern
                patterns[canonical] = re.compile(existing_src + "|" + p.pattern, re.IGNORECASE)
            else:
                patterns[canonical] = p
    return patterns


def _get_region_patterns(regions_config: dict) -> dict[str, re.Pattern]:
    """Return cached (or freshly built) region patterns keyed by region key."""
    if not _region_patterns:
        for region_key, region_cfg in regions_config.get("regions", {}).items():
            _region_patterns[region_key] = _build_region_pattern(region_cfg)
    return _region_patterns


def _get_container_patterns(regions_config: dict) -> dict[str, re.Pattern]:
    """Return cached (or freshly built) container signal patterns."""
    if not _container_patterns:
        container_cfg = regions_config.get("container_availability", {})
        built = _build_container_patterns(container_cfg)
        _container_patterns.update(built)
    return _container_patterns


def clear_pattern_cache() -> None:
    """Clear cached region and container patterns.

    Must be called when switching between agents that use different
    region configurations (e.g. freight regions vs oil regions).
    """
    _region_patterns.clear()
    _container_patterns.clear()


def _article_text(article: Article) -> str:
    """Return combined searchable text for an article."""
    return f"{article.title} {article.raw_text}"


def tag_regions(article: Article, regions_config: dict) -> List[str]:
    """
    Inspect article title and raw_text against region keyword maps and
    return a list of matching region keys (e.g. ['east_africa', 'vietnam']).

    Preserves any pre-set regions (e.g. from manual_loader) and merges
    auto-detected regions on top. Mutates article.regions in-place.
    """
    text = _article_text(article)
    patterns = _get_region_patterns(regions_config)
    # Start from any regions already set (e.g. explicit YAML input)
    matched: List[str] = list(article.regions)
    for region_key, pattern in patterns.items():
        if region_key not in matched and pattern.search(text):
            matched.append(region_key)
    article.regions = matched
    return matched


def detect_container_signal(
    article: Article,
    regions_config: dict,
) -> Optional[str]:
    """
    Scan article text for container availability keywords and return
    the signal type: "shortage", "surplus", "general", or None.

    Priority: shortage > surplus > general.
    Mutates article.container_signal in-place and also returns it.
    """
    # Honor pre-set container signal (e.g. from manual_loader)
    if article.container_signal is not None:
        return article.container_signal

    text = _article_text(article)
    patterns = _get_container_patterns(regions_config)

    for signal_type in ("shortage", "surplus", "general"):
        pattern = patterns.get(signal_type)
        if pattern and pattern.search(text):
            article.container_signal = signal_type
            return signal_type

    article.container_signal = None
    return None


def apply_filters(
    articles: List[Article],
    regions_config: dict,
) -> List[Article]:
    """
    Apply region tagging and container signal detection to all articles.

    Keeps only articles that matched at least one target region OR carry
    a container signal. Articles with neither are discarded.

    Args:
        articles: Raw list of articles from aggregator + crawler.
        regions_config: Parsed content of config/regions.yaml.

    Returns:
        Filtered and tagged article list (order preserved).
    """
    kept: List[Article] = []
    discarded = 0

    for article in articles:
        regions = tag_regions(article, regions_config)
        signal = detect_container_signal(article, regions_config)
        if regions or signal:
            kept.append(article)
        else:
            discarded += 1

    logger.info(
        "Filter: kept %d articles, discarded %d (no region or container match)",
        len(kept),
        discarded,
    )
    return kept
