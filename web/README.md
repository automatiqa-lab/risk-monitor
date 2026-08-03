# web/

The live dashboard: a FastAPI app that refreshes itself and serves what the agents have found.

`app.py` holds the routes - four HTML pages and a small JSON API. `scheduler.py` runs the agents on
an interval through APScheduler, calling `run_for_dashboard` rather than the full pipeline.
`database.py` is the SQLite layer: schema, migrations and the upserts the scrapers write through.
`templates/` holds the Jinja2 pages, `static/` the assets.

The dashboard path skips the model, so most rows hold scraped text rather than model output. Every
row therefore records a `summary_source`, and the pages mark only the rows whose text a model
actually wrote. The JSON endpoints that return that text carry a disclosure envelope and an
`X-AI-Generated` header; the ones that return counts and status do not, because claiming otherwise
would be inaccurate.

**There is no authentication.** Anyone who can reach the host can read every page and trigger a
scrape. If you deploy this beyond localhost, put it behind your own reverse-proxy auth - that is the
deployer's responsibility, and it is recorded as such in [COMPLIANCE.md](../COMPLIANCE.md).
