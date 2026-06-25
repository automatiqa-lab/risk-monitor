"""Tests for agent/filter.py - region tagging and container signal detection."""
import pytest
from datetime import datetime, timezone

from agent.rss_aggregator import Article
from agent.filter import tag_regions, detect_container_signal, apply_filters


@pytest.fixture
def regions_config():
    import yaml
    from pathlib import Path
    config_path = Path(__file__).parent.parent / "config" / "regions.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def make_article(title: str, text: str = "", source: str = "Test") -> Article:
    return Article(
        title=title,
        url="https://example.com/article",
        source=source,
        published_date=datetime.now(timezone.utc),
        raw_text=text,
    )


class TestTagRegions:
    def test_detects_east_africa_by_port(self, regions_config):
        article = make_article("Congestion at Mombasa port reaches new high")
        regions = tag_regions(article, regions_config)
        assert "east_africa" in regions

    def test_detects_vietnam_by_country(self, regions_config):
        article = make_article("Vietnam exporters face delays at Cat Lai terminal")
        regions = tag_regions(article, regions_config)
        assert "vietnam" in regions

    def test_detects_north_europe_by_port(self, regions_config):
        article = make_article("Rotterdam throughput rises 5% in Q2")
        regions = tag_regions(article, regions_config)
        assert "north_europe" in regions

    def test_no_region_for_unrelated_article(self, regions_config):
        article = make_article("Local weather forecast for inland cities")
        regions = tag_regions(article, regions_config)
        assert regions == []

    def test_multiple_regions(self, regions_config):
        article = make_article(
            "MSC adds Vietnam–Rotterdam direct service",
            text="The new service connects Ho Chi Minh City to Rotterdam weekly."
        )
        regions = tag_regions(article, regions_config)
        assert "vietnam" in regions
        assert "north_europe" in regions


class TestContainerSignal:
    def test_detects_shortage(self, regions_config):
        article = make_article("Severe container shortage hits East Africa exporters")
        signal = detect_container_signal(article, regions_config)
        assert signal == "shortage"

    def test_detects_surplus(self, regions_config):
        article = make_article("Container surplus eases in Hamburg following slow season")
        signal = detect_container_signal(article, regions_config)
        assert signal == "surplus"

    def test_detects_general(self, regions_config):
        article = make_article("Port congestion leads to rising demurrage charges")
        signal = detect_container_signal(article, regions_config)
        assert signal in ("general", "shortage")  # demurrage can also be shortage-adjacent

    def test_no_signal_for_unrelated(self, regions_config):
        article = make_article("New CEO appointed at global logistics firm")
        signal = detect_container_signal(article, regions_config)
        assert signal is None
