"""
Google News scraper - runs all module queries and stores articles.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.rss_aggregator import fetch_google_news
from web.scrapers.base import BaseScraper
from web.database import upsert_article, insert_alert, update_module_status

MODULE_QUERIES = {
    "freight": [
        ('ocean freight container shipping (Kenya OR Mombasa OR "East Africa")', "East Africa"),
        ('ocean freight container shipping (Panama OR "Central America")', "Central America"),
        ('ocean freight container shipping (Brazil OR Santos)', "Brazil"),
        ('ocean freight container shipping (Netherlands OR Rotterdam)', "North Europe"),
        ('ocean freight container shipping (Vietnam OR "Ho Chi Minh City")', "Vietnam"),
        ('"container shortage" OR "blank sailing" OR "port congestion" ocean freight', "Container Watch"),
        ('"MSC" shipping containers freight news', "MSC"),
        ('"Maersk" shipping containers freight news', "Maersk"),
        ('"CMA CGM" shipping containers freight news', "CMA CGM"),
        ('"Hapag-Lloyd" shipping containers freight news', "Hapag-Lloyd"),
    ],
    "oil": [
        ('VLSFO price (Singapore OR Fujairah OR Rotterdam) shipping bunker', "VLSFO"),
        ('"marine fuel" OR "bunker fuel" (shortage OR crisis OR surcharge)', "Marine Fuel"),
        ('"Brent crude" (shipping OR freight OR surcharge)', "Brent"),
        ('"fuel surcharge" OR "EFS" OR "emergency fuel" shipping carrier', "EFS"),
    ],
    "diesel": [
        ('diesel price Germany transport logistics trucking', "DE Diesel"),
        ('diesel price Spain transport logistics', "ES Diesel"),
        ('diesel price Italy transport logistics', "IT Diesel"),
        ('diesel price "United Kingdom" OR UK transport logistics', "UK Diesel"),
        ('"fuel surcharge" trucking (Germany OR Spain OR Italy OR UK)', "Trucking Surcharges"),
        ('diesel refinery crack spread Europe', "Crack Spreads"),
    ],
    "geopolitical": [
        ('"Strait of Hormuz" OR "Red Sea" (shipping OR blockade OR attack)', "Chokepoints"),
        ('(sanctions OR embargo) (shipping OR freight)', "Sanctions"),
        ('(tariff OR "trade war") shipping freight supply chain', "Tariffs"),
    ],
    "strikes": [
        ('"port strike" OR "dock worker strike" shipping', "Port Strikes"),
        ('"trucker strike" OR "transport strike" logistics', "Trucking Strikes"),
        ('"customs strike" OR "customs slowdown" freight', "Customs"),
    ],
    "weather": [
        ('(hurricane OR typhoon OR cyclone) (port OR shipping) disruption', "Cyclones"),
        ('drought (Brazil OR Vietnam OR Ethiopia) (coffee OR agriculture)', "Drought"),
        ('"Panama Canal" (water level OR draft) shipping', "Panama Canal"),
        ('flood (port OR logistics) (Kenya OR Tanzania OR Brazil OR Vietnam)', "Flooding"),
    ],
    "congestion": [
        ('"port congestion" OR "vessel waiting" (Mombasa OR Santos OR Rotterdam OR Hamburg)', "Congestion"),
        ('"blank sailing" OR "schedule reliability" container shipping', "Schedule"),
        ('"equipment availability" OR "container shortage" port', "Equipment"),
    ],
}


# Region keyword map for inline tagging (no need to load YAML configs)
REGION_KEYWORDS = {
    "east_africa": ["mombasa", "dar es salaam", "kenya", "tanzania", "uganda", "ethiopia", "djibouti", "kampala", "kigali", "nairobi", "east africa", "lamu"],
    "central_america": ["panama", "honduras", "guatemala", "nicaragua", "costa rica", "colon", "puerto cortes", "corinto", "balboa", "central america"],
    "brazil": ["brazil", "santos", "paranagua", "itajai", "brasil"],
    "north_europe": ["rotterdam", "hamburg", "antwerp", "germany", "netherlands", "belgium", "bremerhaven", "felixstowe", "north europe", "le havre"],
    "vietnam": ["vietnam", "ho chi minh", "hai phong", "cat lai", "cai mep", "vietnamese"],
    "middle_east": ["middle east", "hormuz", "red sea", "suez", "houthi", "iran", "gulf", "fujairah", "jebel ali", "dubai", "saudi", "qatar", "bahrain", "kuwait", "oman", "iraq", "yemen"],
    "germany": ["germany", "german", "deutschland", "autobahn", "hamburg", "bremerhaven"],
    "spain": ["spain", "spanish", "valencia", "barcelona", "algeciras"],
    "italy": ["italy", "italian", "genoa", "trieste", "la spezia", "livorno"],
    "united_kingdom": ["united kingdom", "uk", "britain", "british", "felixstowe", "southampton", "london gateway", "tilbury"],
    "singapore": ["singapore", "vlsfo singapore", "mpa"],
}

def _tag_regions(text_lower: str) -> str:
    """Return comma-separated region keys matching the text."""
    matched = []
    for region, keywords in REGION_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            matched.append(region)
    return ",".join(matched)


class NewsScraper(BaseScraper):
    name = "google_news"
    module = "all"

    def run(self, db) -> tuple[int, int]:
        total_articles = 0
        module_counts = {}

        for module, queries in MODULE_QUERIES.items():
            seen = set()
            count = 0
            for query, label in queries:
                articles = fetch_google_news(query=query, source_label=label, lookback_days=7)
                for a in articles:
                    key = a.title.lower()[:80]
                    if key in seen:
                        continue
                    seen.add(key)

                    text_lower = f"{a.title} {a.raw_text}".lower()

                    signal = ""
                    if any(kw in text_lower for kw in ("shortage", "crisis", "suspend", "halt", "strike")):
                        signal = "shortage"
                    elif any(kw in text_lower for kw in ("surcharge", "advisory", "update", "price")):
                        signal = "general"

                    regions = _tag_regions(text_lower)
                    upsert_article(db, a.title, a.url, a.source, module, regions, signal,
                                   a.summary or "", a.raw_text or "", a.published_date.isoformat())

                    # Create alert for critical signals
                    if signal == "shortage":
                        insert_alert(db, a.title[:120], module, "critical", regions, [module.upper(), label])
                    elif signal == "general":
                        insert_alert(db, a.title[:120], module, "high", regions, [module.upper(), label])

                    count += 1
                    total_articles += 1

            module_counts[module] = count

            # Update module status
            critical = db.execute(
                "SELECT COUNT(*) FROM articles WHERE module=? AND signal='shortage' AND scraped_at > datetime('now', '-7 days')",
                (module,)).fetchone()[0]
            general = db.execute(
                "SELECT COUNT(*) FROM articles WHERE module=? AND signal='general' AND scraped_at > datetime('now', '-7 days')",
                (module,)).fetchone()[0]

            if critical >= 3:
                status = "CRITICAL"
            elif critical >= 1 or general >= 3:
                status = "HIGH"
            elif general >= 1:
                status = "WATCH"
            else:
                status = "STABLE"

            update_module_status(db, module, status, count, critical, general)

        return total_articles, 0
