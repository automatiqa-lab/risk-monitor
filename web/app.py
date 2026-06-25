"""
FastAPI app for Operations Risk Navigator.
Serves the live dashboard and the JSON endpoints behind it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
# Jinja2Templates replaced with direct Environment to avoid cache key bug

from web.database import (
    init_db, get_db, db_session,
    get_recent_articles, get_latest_metrics, get_all_module_status,
    get_recent_alerts, get_scraper_runs,
)

logger = logging.getLogger("ops_risk_navigator.web")

app = FastAPI(
    title="Operations Risk Navigator",
    description="Multi-agent supply chain and operations risk monitoring",
    version="1.0.0",
)

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

from jinja2 import Environment, FileSystemLoader
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


@app.on_event("startup")
async def startup():
    init_db()
    logger.info("Database initialised")

    # Start background scheduler
    from web.scheduler import create_scheduler, run_all_scrapers
    scheduler = create_scheduler(interval_hours=6)
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("Scheduler started (every 6h)")

    # Scheduler now runs immediately on startup (next_run_time=now),
    # so no need for a separate initial-scrape check.


@app.on_event("shutdown")
async def shutdown():
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown()
        logger.info("Scheduler stopped")


# ── Dashboard (serves the locked HTML design) ────────────────────────────────

def _row_to_dict(row):
    """Convert SQLite Row to a plain dict with only string/int/float values."""
    if row is None:
        return {}
    d = dict(row)
    # Ensure all values are JSON-safe primitives
    return {k: (str(v) if not isinstance(v, (str, int, float, type(None))) else v) for k, v in d.items()}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard view."""
    with db_session() as db:
        module_status = get_all_module_status(db)
        alerts = get_recent_alerts(db, limit=20)
        metrics = get_latest_metrics(db)

    # Build carrier status from DB
    carrier_data = []
    carrier_defs = [
        ("msc", "MSC", ["msc", "mediterranean shipping"]),
        ("maersk", "Maersk", ["maersk"]),
        ("cma-cgm", "CMA CGM", ["cma cgm", "cma-cgm"]),
        ("hapag-lloyd", "Hapag-Lloyd", ["hapag-lloyd", "hapag lloyd"]),
        ("one", "ONE", ["one line", "ocean network express"]),
        ("evergreen", "Evergreen", ["evergreen"]),
        ("cosco", "COSCO", ["cosco"]),
        ("wec", "WEC Lines", ["wec line", "wec lines"]),
    ]
    with db_session() as db2:
        for ck, cn, terms in carrier_defs:
            conds = " OR ".join(["title LIKE ? OR source LIKE ?"] * len(terms))
            params = []
            for t in terms:
                params.extend([f"%{t}%", f"%{t}%"])
            total = db2.execute(f"SELECT COUNT(*) FROM articles WHERE ({conds}) AND scraped_at > datetime('now', '-7 days')", params).fetchone()[0]
            critical = db2.execute(f"SELECT COUNT(*) FROM articles WHERE ({conds}) AND signal='shortage' AND scraped_at > datetime('now', '-7 days')", params).fetchone()[0]

            if critical >= 3:
                cstatus, ccolor = "CRITICAL", "red"
            elif critical >= 1:
                cstatus, ccolor = "HIGH", "red"
            elif total >= 3:
                cstatus, ccolor = "ACTIVE", "amber"
            elif total > 0:
                cstatus, ccolor = "MONITORING", "olive"
            else:
                cstatus, ccolor = "NORMAL", "green"

            carrier_data.append({"key": ck, "name": cn, "status": cstatus, "color": ccolor, "count": total, "critical": critical})

    # Build port congestion insights - combine article mentions + vessel metrics
    port_configs = [
        ("mombasa", "Mombasa"), ("dar es salaam", "Dar es Salaam"), ("djibouti", "Djibouti"),
        ("puerto cortes", "Puerto Cortes"), ("colon", "Colón"), ("santos", "Santos"),
        ("rotterdam", "Rotterdam"), ("hamburg", "Hamburg"), ("antwerp", "Antwerp"),
        ("ho chi minh", "Ho Chi Minh"), ("hai phong", "Hai Phong"), ("felixstowe", "Felixstowe"),
    ]
    port_data = []
    port_vessel_data = []  # For vessel chart
    with db_session() as db3:
        for port_search, port_label in port_configs:
            total = db3.execute("SELECT COUNT(*) FROM articles WHERE (title LIKE ? OR summary LIKE ?) AND scraped_at > datetime('now', '-7 days')",
                               (f"%{port_search}%", f"%{port_search}%")).fetchone()[0]
            critical = db3.execute("SELECT COUNT(*) FROM articles WHERE (title LIKE ? OR summary LIKE ?) AND signal='shortage' AND scraped_at > datetime('now', '-7 days')",
                                  (f"%{port_search}%", f"%{port_search}%")).fetchone()[0]
            if total > 0:
                port_data.append({"name": port_label, "total": total, "critical": critical, "high": total - critical})

        # Vessel metrics from VesselFinder/MarineTraffic
        vessel_rows = db3.execute("""
            SELECT m.* FROM metrics m
            INNER JOIN (SELECT metric_key, MAX(recorded_at) as max_at FROM metrics WHERE module='congestion' GROUP BY metric_key) latest
            ON m.metric_key = latest.metric_key AND m.recorded_at = latest.max_at
            ORDER BY m.metric_key
        """).fetchall()
        port_vessels = {}
        for row in vessel_rows:
            r = _row_to_dict(row)
            key = r.get("metric_key", "")
            # Parse port_mombasa_in_port → mombasa, in_port
            parts = key.replace("port_", "").replace("mt_", "")
            for pk, pl in port_configs:
                pk_clean = pk.replace(" ", "_")
                if pk_clean in parts:
                    metric_type = parts.replace(pk_clean + "_", "")
                    if pk_clean not in port_vessels:
                        port_vessels[pk_clean] = {"name": pl}
                    port_vessels[pk_clean][metric_type] = r.get("value", 0)
                    break

        for pk, pv in port_vessels.items():
            port_vessel_data.append(pv)

    # Build carrier signal counts for chart
    carrier_chart = []
    for cd in carrier_data:
        if cd["count"] > 0:
            carrier_chart.append({"name": cd["name"], "total": cd["count"], "critical": cd["critical"]})

    # Region signal distribution
    region_chart = []
    with db_session() as db4:
        for rk, rl in [("east_africa", "E. Africa"), ("central_america", "C. America"), ("brazil", "Brazil"),
                        ("north_europe", "N. Europe"), ("vietnam", "Vietnam"), ("middle_east", "Middle East")]:
            cnt = db4.execute("SELECT COUNT(*) FROM articles WHERE region LIKE ? AND scraped_at > datetime('now', '-7 days')",
                             (f"%{rk}%",)).fetchone()[0]
            crit = db4.execute("SELECT COUNT(*) FROM articles WHERE region LIKE ? AND signal='shortage' AND scraped_at > datetime('now', '-7 days')",
                              (f"%{rk}%",)).fetchone()[0]
            if cnt > 0:
                region_chart.append({"name": rl, "total": cnt, "critical": crit})

    tmpl = _jinja_env.get_template("dashboard.html")
    html = tmpl.render(
        modules=[_row_to_dict(m) for m in module_status],
        alerts=[_row_to_dict(a) for a in alerts],
        metrics=[_row_to_dict(m) for m in metrics],
        carriers=carrier_data,
        port_data=port_data,
        port_vessel_data=port_vessel_data,
        carrier_chart=carrier_chart,
        region_chart=region_chart,
        last_update=datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
    )
    return HTMLResponse(content=html)


REGION_CONFIG = {
    "east_africa": {"icon": "\U0001F30D", "label": "East Africa", "subtitle": "Kenya, Tanzania, Uganda, Rwanda, Ethiopia, Djibouti - Mombasa, Dar es Salaam, Lamu"},
    "central_america": {"icon": "\U0001F30E", "label": "Central America", "subtitle": "Panama, Honduras, Guatemala, Nicaragua, Costa Rica - Colón, Puerto Cortes, Corinto"},
    "brazil": {"icon": "\U0001F30E", "label": "Brazil", "subtitle": "Santos, Paranaguá, Itajaí - coffee exports, soybean trade"},
    "north_europe": {"icon": "\U0001F30D", "label": "North Europe", "subtitle": "Germany, Netherlands, Belgium, France, UK - Rotterdam, Hamburg, Antwerp, Felixstowe"},
    "vietnam": {"icon": "\U0001F30F", "label": "Vietnam & Southeast Asia", "subtitle": "Ho Chi Minh City, Hai Phong - coffee, manufacturing exports"},
    "middle_east": {"icon": "\U0001F30D", "label": "Middle East & Gulf", "subtitle": "Strait of Hormuz, Red Sea, Suez Canal - chokepoint risks, fuel supply"},
}

MODULE_ICONS = {
    "freight": "\U0001F6A2", "oil": "\U0001F6E2\uFE0F", "diesel": "\u26FD",
    "strikes": "\u26A0\uFE0F", "weather": "\U0001F30A", "geopolitical": "\U0001F310", "congestion": "\U0001F3D7\uFE0F",
}
MODULE_LABELS = {
    "freight": "Freight", "oil": "Oil & Fuel", "diesel": "Diesel",
    "strikes": "Disruptions", "weather": "Weather", "geopolitical": "Geopolitical", "congestion": "Congestion",
}

MODULE_CONFIG = {
    "freight": {"icon": "\U0001F6A2", "label": "Global Freight News", "subtitle": "Ocean freight rates, carrier advisories, equipment availability, regional highlights"},
    "oil": {"icon": "\U0001F6E2\uFE0F", "label": "Oil & Marine Fuel", "subtitle": "VLSFO prices, Brent crude, bunkering hub status, carrier fuel surcharges"},
    "diesel": {"icon": "\u26FD", "label": "European Diesel", "subtitle": "Diesel pump prices DE/ES/IT/UK, trucking fuel surcharges, policy changes"},
    "strikes": {"icon": "\u26A0\uFE0F", "label": "Strikes & Disruptions", "subtitle": "Labour strikes, port blockades, customs slowdowns, union disputes"},
    "weather": {"icon": "\U0001F30A", "label": "Weather & Natural Disasters", "subtitle": "Hurricanes, floods, droughts, Panama Canal water levels, origin conditions"},
    "geopolitical": {"icon": "\U0001F310", "label": "Geopolitical & Sanctions", "subtitle": "Trade wars, sanctions, tariffs, chokepoint sovereignty, compliance changes"},
    "congestion": {"icon": "\U0001F3D7\uFE0F", "label": "Port Congestion Index", "subtitle": "Vessel queues, equipment stock, schedule reliability, blank sailings"},
}


@app.get("/region/{region_key}", response_class=HTMLResponse)
async def region_detail_view(region_key: str, module: str = None):
    """Detail view for a specific trade lane / region across all modules."""
    cfg = REGION_CONFIG.get(region_key)
    if not cfg:
        return HTMLResponse(content="Region not found", status_code=404)

    with db_session() as db:
        # Get all articles matching this region across all modules
        if module:
            rows = db.execute(
                "SELECT * FROM articles WHERE region LIKE ? AND module=? ORDER BY scraped_at DESC LIMIT 60",
                (f"%{region_key}%", module)).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM articles WHERE region LIKE ? ORDER BY scraped_at DESC LIMIT 60",
                (f"%{region_key}%",)).fetchall()

        # Count per module for dimension cards
        dim_counts = {}
        for mod in ["freight", "oil", "diesel", "strikes", "weather", "geopolitical", "congestion"]:
            cnt = db.execute(
                "SELECT COUNT(*) FROM articles WHERE region LIKE ? AND module=?",
                (f"%{region_key}%", mod)).fetchone()[0]
            dim_counts[mod] = cnt

    articles = [_row_to_dict(r) for r in rows]

    # Build dimension cards
    dimensions = []
    for mod in ["freight", "oil", "diesel", "strikes", "weather", "geopolitical", "congestion"]:
        dimensions.append({
            "module": mod,
            "icon": MODULE_ICONS.get(mod, ""),
            "label": MODULE_LABELS.get(mod, mod),
            "count": dim_counts.get(mod, 0),
        })

    total = sum(d["count"] for d in dimensions)
    critical_count = sum(1 for d in dimensions if d["count"] > 3)
    if critical_count >= 2:
        overall = "CRITICAL"
    elif critical_count >= 1 or any(d["count"] > 0 for d in dimensions):
        overall = "HIGH" if any(d["count"] > 3 for d in dimensions) else "WATCH"
    else:
        overall = "STABLE"

    tmpl = _jinja_env.get_template("region_detail.html")
    html = tmpl.render(
        region_key=region_key,
        region_icon=cfg["icon"],
        region_label=cfg["label"],
        subtitle=cfg["subtitle"],
        overall_status=overall,
        dimensions=dimensions,
        articles=articles,
        filter_module=module,
        active_nav="",
        last_update=datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
    )
    return HTMLResponse(content=html)


CARRIER_CONFIG = {
    "msc": {"label": "MSC", "search": ["msc", "mediterranean shipping"], "news_url": "https://www.msc.com/en/news", "advisory_url": "https://www.msc.com/en/news"},
    "maersk": {"label": "Maersk", "search": ["maersk"], "news_url": "https://www.maersk.com/news", "advisory_url": "https://www.maersk.com/news/service-updates"},
    "cma-cgm": {"label": "CMA CGM", "search": ["cma cgm", "cma-cgm"], "news_url": "https://www.cma-cgm.com/news", "advisory_url": "https://www.cma-cgm.com/news"},
    "hapag-lloyd": {"label": "Hapag-Lloyd", "search": ["hapag-lloyd", "hapag lloyd", "hpl"], "news_url": "https://www.hapag-lloyd.com/en/news-insights/news.html", "advisory_url": "https://www.hapag-lloyd.com/en/news-insights/customer-advisories.html"},
    "one": {"label": "Ocean Network Express (ONE)", "search": ["one line", "ocean network express", " one "], "news_url": "https://www.one-line.com/en/news", "advisory_url": "https://www.one-line.com/en/news"},
    "evergreen": {"label": "Evergreen Line", "search": ["evergreen"], "news_url": "https://www.evergreen-line.com/static/jsp/news_list.jsp", "advisory_url": ""},
    "cosco": {"label": "COSCO Shipping", "search": ["cosco"], "news_url": "https://lines.coscoshipping.com/home/Notices", "advisory_url": ""},
    "wec": {"label": "WEC Lines", "search": ["wec line", "wec lines"], "news_url": "", "advisory_url": ""},
    "zim": {"label": "ZIM", "search": ["zim"], "news_url": "https://www.zim.com/news", "advisory_url": ""},
    "hmm": {"label": "HMM", "search": ["hmm", "hyundai merchant"], "news_url": "", "advisory_url": ""},
    "yang-ming": {"label": "Yang Ming", "search": ["yang ming"], "news_url": "https://www.yangming.com/e-service/news/index.aspx", "advisory_url": ""},
    "pil": {"label": "PIL", "search": ["pacific international lines", "pil"], "news_url": "https://www.pilship.com/en-news/", "advisory_url": ""},
    "messina": {"label": "Messina Line", "search": ["messina"], "news_url": "", "advisory_url": ""},
    "safmarine": {"label": "Safmarine", "search": ["safmarine"], "news_url": "", "advisory_url": ""},
    "emirates": {"label": "Emirates Shipping Line", "search": ["emirates shipping"], "news_url": "https://www.emiratesline.com/news/", "advisory_url": ""},
}


@app.get("/carrier/{carrier_key}", response_class=HTMLResponse)
async def carrier_detail_view(carrier_key: str):
    """Detail view for a specific shipping line."""
    cfg = CARRIER_CONFIG.get(carrier_key)
    if not cfg:
        return HTMLResponse(content="Carrier not found", status_code=404)

    search_terms = cfg["search"]

    with db_session() as db:
        # Search articles by carrier name in title, source, or summary
        conditions = " OR ".join(["(title LIKE ? OR source LIKE ? OR summary LIKE ?)"] * len(search_terms))
        params = []
        for term in search_terms:
            params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])

        articles = db.execute(
            f"SELECT * FROM articles WHERE ({conditions}) ORDER BY scraped_at DESC LIMIT 60",
            params).fetchall()

        # Separate advisories (articles with dates, from carrier source)
        advisories = db.execute(
            f"SELECT * FROM articles WHERE ({conditions}) AND signal != '' ORDER BY published_at DESC LIMIT 30",
            params).fetchall()

    article_dicts = [_row_to_dict(a) for a in articles]
    advisory_dicts = [_row_to_dict(a) for a in advisories]

    # Count stats
    total = len(article_dicts)
    critical = sum(1 for a in article_dicts if a.get("signal") == "shortage")
    regions_set = set()
    for a in article_dicts:
        for r in (a.get("region") or "").split(","):
            if r.strip():
                regions_set.add(r.strip())

    tmpl = _jinja_env.get_template("carrier_detail.html")
    html = tmpl.render(
        carrier_key=carrier_key,
        carrier_label=cfg["label"],
        subtitle=f"News, advisories, surcharges, and operational updates from {cfg['label']}",
        news_url=cfg.get("news_url", ""),
        advisory_url=cfg.get("advisory_url", ""),
        articles=article_dicts,
        advisories=advisory_dicts,
        total_signals=total,
        critical_count=critical,
        regions_affected=len(regions_set),
        top_regions=", ".join(sorted(regions_set)[:5]) if regions_set else "Global",
        active_nav="",
        last_update=datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
    )
    return HTMLResponse(content=html)


@app.get("/{module}", response_class=HTMLResponse)
async def module_detail_view(module: str, request: Request):
    """Detail view for any module."""
    cfg = MODULE_CONFIG.get(module)
    if not cfg:
        return HTMLResponse(content="Module not found", status_code=404)

    with db_session() as db:
        articles = get_recent_articles(db, module=module, limit=50)
        metrics = get_latest_metrics(db, module=module)
        alerts = db.execute(
            "SELECT * FROM alerts WHERE module=? ORDER BY created_at DESC LIMIT 30",
            (module,)).fetchall()
        status_row = db.execute(
            "SELECT * FROM module_status WHERE module=?", (module,)).fetchone()

    # Build KPIs from status
    kpis = []
    if status_row:
        s = _row_to_dict(status_row)
        kpis.append({"label": "Total Signals", "value": str(s.get("signal_count", 0)), "color": "green"})
        kpis.append({"label": "Critical", "value": str(s.get("critical_count", 0)), "color": "red"})
        kpis.append({"label": "High", "value": str(s.get("high_count", 0)), "color": "amber"})

    # Diesel-specific: prominent country price cards
    if module == "diesel":
        diesel_prices = {}
        for m in metrics:
            md = _row_to_dict(m)
            key = md.get("metric_key", "")
            if "_usd" in key:
                continue  # Skip USD duplicates
            for code, flag, name in [("germany", "\U0001F1E9\U0001F1EA", "Germany"), ("spain", "\U0001F1EA\U0001F1F8", "Spain"),
                                      ("italy", "\U0001F1EE\U0001F1F9", "Italy"), ("uk", "\U0001F1EC\U0001F1E7", "UK")]:
                if code in key:
                    val = md.get("value", 0)
                    unit = md.get("unit", "EUR/L")
                    source = md.get("source", "")
                    if isinstance(val, (int, float)):
                        display = f"\u20AC{val:.2f}/L" if "EUR" in unit else f"\u00A3{val:.2f}/L" if "GBP" in unit else f"{val}"
                        if "1000" in unit:
                            display = f"\u20AC{int(val):,}/1000L"
                    else:
                        display = str(val)
                    kpis.append({"label": f"{flag} {name}", "value": display, "sub": source, "color": "amber"})
                    diesel_prices[code] = val
                    break

    # Oil-specific: prominent fuel KPIs
    elif module == "oil":
        for m in metrics:
            md = _row_to_dict(m)
            key = md.get("metric_key", "")
            val = md.get("value", 0)
            if "vlsfo" in key:
                kpis.append({"label": f"\U0001F6E2\uFE0F {md.get('label', key)}", "value": f"${val:,.0f}/mt" if isinstance(val, (int, float)) else str(val), "sub": md.get("source", ""), "color": "red"})
            elif "brent" in key:
                kpis.append({"label": f"\U0001F4B0 {md.get('label', key)}", "value": f"${val:.2f}/bbl" if isinstance(val, (int, float)) else str(val), "sub": md.get("source", ""), "color": "red"})

    # Congestion-specific: vessel count KPIs
    elif module == "congestion":
        port_kpis = {}
        for m in metrics:
            md = _row_to_dict(m)
            key = md.get("metric_key", "")
            val = md.get("value", 0)
            label = md.get("label", "")
            if "in_port" in key and isinstance(val, (int, float)) and val > 0:
                kpis.append({"label": f"\U0001F6A2 {label}", "value": str(int(val)), "sub": md.get("source", ""), "color": "green" if val < 100 else "amber"})
            elif "expected" in key and isinstance(val, (int, float)) and val > 0:
                kpis.append({"label": f"\U0001F4C5 {label}", "value": str(int(val)), "sub": md.get("source", ""), "color": "olive"})

    # Generic: add remaining metrics as KPIs
    else:
        for m in metrics:
            md = _row_to_dict(m)
            kpis.append({
                "label": md.get("label") or md.get("metric_key", ""),
                "value": str(md.get("value", "")),
                "sub": md.get("source", ""),
                "color": "green",
            })

    tmpl = _jinja_env.get_template("module_detail.html")
    html = tmpl.render(
        module_icon=cfg["icon"],
        module_label=cfg["label"],
        subtitle=cfg["subtitle"],
        active_nav=module,
        articles=[_row_to_dict(a) for a in articles],
        metrics=[_row_to_dict(m) for m in metrics],
        alerts=[_row_to_dict(a) for a in alerts],
        status=_row_to_dict(status_row) if status_row else None,
        kpis=kpis,
        last_update=datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
    )
    return HTMLResponse(content=html)


# ── API endpoints ────────────────────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    """Overall system status."""
    with db_session() as db:
        modules = get_all_module_status(db)
        runs = get_scraper_runs(db, limit=5)
    return {
        "modules": [dict(m) for m in modules],
        "last_runs": [dict(r) for r in runs],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/modules/{module}")
async def api_module(module: str):
    """Get data for a specific module."""
    with db_session() as db:
        articles = get_recent_articles(db, module=module, limit=30)
        metrics = get_latest_metrics(db, module=module)
    return {
        "module": module,
        "articles": [dict(a) for a in articles],
        "metrics": [dict(m) for m in metrics],
    }


@app.get("/api/alerts")
async def api_alerts(limit: int = 50):
    """Live alert feed."""
    with db_session() as db:
        alerts = get_recent_alerts(db, limit=limit)
    return {"alerts": [dict(a) for a in alerts]}


@app.get("/api/metrics")
async def api_metrics(module: str = None):
    """Latest metrics across all or specific module."""
    with db_session() as db:
        metrics = get_latest_metrics(db, module=module)
    return {"metrics": [dict(m) for m in metrics]}


@app.get("/api/articles")
async def api_articles(module: str = None, limit: int = 30):
    """Recent articles."""
    with db_session() as db:
        articles = get_recent_articles(db, module=module, limit=limit)
    return {"articles": [dict(a) for a in articles]}


@app.post("/api/scrape")
async def trigger_scrape():
    """Manually trigger a full scraper run."""
    import threading
    from web.scheduler import run_all_scrapers
    threading.Thread(target=run_all_scrapers, daemon=True).start()
    return {"status": "started", "message": "Scraper run triggered in background"}


@app.get("/api/scraper-status")
async def scraper_status():
    """Latest scraper run results."""
    with db_session() as db:
        runs = get_scraper_runs(db, limit=20)
        total_articles = db.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        total_metrics = db.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        total_alerts = db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    return {
        "total_articles": total_articles,
        "total_metrics": total_metrics,
        "total_alerts": total_alerts,
        "recent_runs": [dict(r) for r in runs],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "ops-risk-navigator", "version": "1.0.0"}
