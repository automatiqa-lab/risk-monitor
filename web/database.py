"""
SQLite database for the Operations Risk Navigator.
Stores scraped data points, articles, alerts, and module status.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent.parent / "data" / "control_tower.db"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_session():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with db_session() as db:
        db.executescript("""
        -- Scraped articles / intelligence items
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT DEFAULT '',
            source TEXT DEFAULT '',
            module TEXT NOT NULL,
            region TEXT DEFAULT '',
            signal TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            raw_text TEXT DEFAULT '',
            published_at TEXT,
            scraped_at TEXT DEFAULT (datetime('now')),
            UNIQUE(title, module)
        );

        -- Quantitative data points (prices, metrics, indices)
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_key TEXT NOT NULL,
            module TEXT NOT NULL,
            value REAL,
            unit TEXT DEFAULT '',
            label TEXT DEFAULT '',
            source TEXT DEFAULT '',
            recorded_at TEXT DEFAULT (datetime('now')),
            UNIQUE(metric_key, module, recorded_at)
        );

        -- Module status (latest severity per module)
        CREATE TABLE IF NOT EXISTS module_status (
            module TEXT PRIMARY KEY,
            status TEXT DEFAULT 'STABLE',
            signal_count INTEGER DEFAULT 0,
            critical_count INTEGER DEFAULT 0,
            high_count INTEGER DEFAULT 0,
            last_updated TEXT DEFAULT (datetime('now'))
        );

        -- Scraper run log
        CREATE TABLE IF NOT EXISTS scraper_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scraper TEXT NOT NULL,
            status TEXT DEFAULT 'ok',
            articles_count INTEGER DEFAULT 0,
            metrics_count INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            run_at TEXT DEFAULT (datetime('now'))
        );

        -- Alerts (derived from articles, shown in live feed)
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            module TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            region TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_articles_module ON articles(module);
        CREATE INDEX IF NOT EXISTS idx_articles_scraped ON articles(scraped_at);
        CREATE INDEX IF NOT EXISTS idx_metrics_key ON metrics(metric_key, module);
        CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
        """)


def upsert_article(db, title, url, source, module, region="", signal="",
                    summary="", raw_text="", published_at=None):
    """Insert or update an article - refresh scraped_at on every cycle."""
    db.execute("""
        INSERT INTO articles (title, url, source, module, region, signal, summary, raw_text, published_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(title, module) DO UPDATE SET
            url = excluded.url,
            source = excluded.source,
            region = excluded.region,
            signal = excluded.signal,
            summary = excluded.summary,
            raw_text = excluded.raw_text,
            published_at = excluded.published_at,
            scraped_at = datetime('now')
    """, (title, url, source, module, region, signal, summary, raw_text, published_at))


def insert_metric(db, metric_key, module, value, unit="", label="", source=""):
    """Insert a metric data point."""
    db.execute("""
        INSERT OR REPLACE INTO metrics (metric_key, module, value, unit, label, source, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
    """, (metric_key, module, value, unit, label, source))


def update_module_status(db, module, status, signal_count, critical_count, high_count):
    db.execute("""
        INSERT OR REPLACE INTO module_status (module, status, signal_count, critical_count, high_count, last_updated)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
    """, (module, status, signal_count, critical_count, high_count))


def insert_alert(db, title, module, severity="info", region="", tags=None):
    db.execute("""
        INSERT INTO alerts (title, module, severity, region, tags)
        VALUES (?, ?, ?, ?, ?)
    """, (title, module, severity, region, json.dumps(tags or [])))


def get_recent_articles(db, module=None, limit=50):
    if module:
        return db.execute(
            "SELECT * FROM articles WHERE module=? ORDER BY scraped_at DESC LIMIT ?",
            (module, limit)).fetchall()
    return db.execute(
        "SELECT * FROM articles ORDER BY scraped_at DESC LIMIT ?", (limit,)).fetchall()


def get_latest_metrics(db, module=None):
    if module:
        return db.execute("""
            SELECT m.* FROM metrics m
            INNER JOIN (SELECT metric_key, module, MAX(recorded_at) as max_at FROM metrics WHERE module=? GROUP BY metric_key, module) latest
            ON m.metric_key = latest.metric_key AND m.module = latest.module AND m.recorded_at = latest.max_at
        """, (module,)).fetchall()
    return db.execute("""
        SELECT m.* FROM metrics m
        INNER JOIN (SELECT metric_key, module, MAX(recorded_at) as max_at FROM metrics GROUP BY metric_key, module) latest
        ON m.metric_key = latest.metric_key AND m.module = latest.module AND m.recorded_at = latest.max_at
    """).fetchall()


def get_all_module_status(db):
    return db.execute("SELECT * FROM module_status ORDER BY module").fetchall()


def get_recent_alerts(db, limit=50):
    return db.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


def get_scraper_runs(db, limit=20):
    return db.execute("SELECT * FROM scraper_runs ORDER BY run_at DESC LIMIT ?", (limit,)).fetchall()
