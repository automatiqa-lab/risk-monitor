"""Tests for agent/composer.py - template rendering and output structure."""
import pytest
from datetime import datetime, timezone
from pathlib import Path

from agent.rss_aggregator import Article
from agent.composer import render_html, render_markdown


@pytest.fixture
def sample_context():
    """Minimal valid template context for rendering tests."""
    return {
        "week_label": "Week of 2025-07-07",
        "generated_at": "2025-07-07 09:00 UTC",
        "executive_summary": "Ocean freight markets saw mixed signals this week.",
        "regions": [
            {
                "display_name": "East Africa",
                "ports_hint": "Mombasa, Dar es Salaam",
                "articles": [
                    {
                        "title": "Mombasa port congestion persists",
                        "source": "Splash247",
                        "published_date": "2025-07-05",
                        "summary": "Congestion at Mombasa continues to affect dwell times.",
                        "url": "https://splash247.com/example",
                    }
                ],
            },
            {
                "display_name": "Vietnam",
                "ports_hint": "Ho Chi Minh City, Hai Phong",
                "articles": [],
            },
        ],
        "carriers": [
            {
                "name": "MSC",
                "articles": [
                    {
                        "title": "MSC launches new East Africa service",
                        "source": "MSC",
                        "published_date": "2025-07-04",
                        "summary": "MSC announced a new weekly service to Mombasa.",
                        "url": "https://www.msc.com/example",
                    }
                ],
            }
        ],
        "container_watch": {
            "shortage": [
                {
                    "region": "East Africa",
                    "summary": "40ft containers in short supply at Mombasa.",
                    "url": "https://example.com/shortage",
                }
            ],
            "surplus": [],
            "general": [],
        },
        "sources_consulted": [
            {"name": "Splash247", "url": "https://splash247.com"},
            {"name": "MSC", "url": "https://www.msc.com"},
        ],
    }


class TestRenderHtml:
    def test_renders_without_error(self, sample_context):
        html = render_html(sample_context)
        assert isinstance(html, str)
        assert len(html) > 100

    def test_contains_week_label(self, sample_context):
        html = render_html(sample_context)
        assert "Week of 2025-07-07" in html

    def test_contains_region_name(self, sample_context):
        html = render_html(sample_context)
        assert "East Africa" in html

    def test_contains_carrier_name(self, sample_context):
        html = render_html(sample_context)
        assert "MSC" in html

    def test_contains_shortage_signal(self, sample_context):
        html = render_html(sample_context)
        assert "Shortage" in html or "shortage" in html

    def test_no_items_placeholder_when_empty(self, sample_context):
        html = render_html(sample_context)
        # Vietnam has no articles - should show placeholder
        assert "No major news" in html


class TestRenderMarkdown:
    def test_renders_without_error(self, sample_context):
        md = render_markdown(sample_context)
        assert isinstance(md, str)
        assert len(md) > 50

    def test_contains_week_label(self, sample_context):
        md = render_markdown(sample_context)
        assert "Week of 2025-07-07" in md

    def test_contains_executive_summary(self, sample_context):
        md = render_markdown(sample_context)
        assert "Weekly Briefing" in md

    def test_contains_all_sections(self, sample_context):
        md = render_markdown(sample_context)
        assert "Regional Highlights" in md
        assert "Shipping Lines Update" in md
        assert "Container & Equipment Alerts" in md
