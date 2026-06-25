"""
Entry point for the Operations Risk Navigator web service.

Usage:
    python serve.py                  # Dev server on port 8000
    python serve.py --port 8080      # Custom port
    python serve.py --scrape-now     # Run scrapers immediately then start
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(description="Operations Risk Navigator - Web Service")
    parser.add_argument("--port", type=int, default=8000, help="Port to serve on (default: 8000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--scrape-now", action="store_true", help="Run all scrapers before starting")
    args = parser.parse_args()

    # Init database
    from web.database import init_db
    init_db()

    # Optional immediate scrape
    if args.scrape_now:
        from web.scheduler import run_all_scrapers
        run_all_scrapers()

    # Start server
    import uvicorn
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
