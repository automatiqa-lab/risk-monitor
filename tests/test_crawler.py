"""Tests for agent/crawler.py - carrier website scraping (stub placeholders)."""
import pytest


class TestScrapeCarrierNews:
    @pytest.mark.skip(reason="Requires Playwright and live network - integration test")
    def test_msc_returns_articles(self):
        """MSC news page should return at least 1 article within lookback window."""
        import asyncio
        from agent.crawler import scrape_carrier_news
        carrier_config = {
            "name": "MSC",
            "news_url": "https://www.msc.com/en/news",
        }
        articles = asyncio.run(scrape_carrier_news("msc", carrier_config, lookback_days=7))
        assert len(articles) > 0

    @pytest.mark.skip(reason="Requires Playwright and live network - integration test")
    def test_hapag_lloyd_returns_articles(self):
        """Hapag-Lloyd news page should return at least 1 article."""
        import asyncio
        from agent.crawler import scrape_carrier_news
        carrier_config = {
            "name": "Hapag-Lloyd",
            "news_url": "https://www.hapag-lloyd.com/en/news-insights/news.html",
        }
        articles = asyncio.run(scrape_carrier_news("hapag_lloyd", carrier_config, lookback_days=7))
        assert len(articles) > 0
