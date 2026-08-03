# Operations Risk Monitor

A multi-agent system that watches operational risk across ocean freight, fuel, labour, weather, and geopolitics, then turns what it finds into briefings you can actually use. It runs two ways: a CLI that produces weekly reports (HTML, Markdown, PDF, PPTX), and a live dashboard that refreshes itself every few hours.

Part of [Automatiqa Lab](https://www.automatiqa.io/risk-monitor/) - open-source experiments where operations meet the algorithm.

The model behind the summaries is yours to choose. Everything routes through LiteLLM, so the same code runs on Anthropic, OpenAI, or a local Ollama model. You change one line in `config/settings.yaml` and set the matching API key.

## What it watches

Seven agents, each owning a slice of risk:

| Agent | Covers | Output |
|-------|--------|--------|
| `freight` | Ocean freight, container availability, carrier disruptions | HTML + Markdown + PDF |
| `oil` | VLSFO, Brent, bunkering hubs, fuel surcharges | PPTX |
| `diesel` | European diesel prices, trucking surcharges, policy | PPTX |
| `strikes` | Labour action, port blockades, escalation timelines | PPTX |
| `weather` | Storms, floods, and other natural disruptions | PPTX |
| `geopolitical` | Sanctions, conflict, chokepoint risk | PPTX |
| `congestion` | Port congestion index across key terminals | PPTX |
| `cost` | Freight cost analysis | stub, not built yet |

Regions in scope by default: East Africa, Central America, Brazil, North Europe, Vietnam. Carriers tracked: MSC, Hapag-Lloyd, CMA CGM, Maersk, Evergreen, COSCO. All of it lives in `config/` as YAML, so you edit the watchlist without touching code.

## How the harness works

Every agent runs the same five-step pipeline, defined once in `agents/base.py`:

1. **collect** - pull articles from Google News RSS (`agent/rss_aggregator.py`), scrape carrier and authority sites with Playwright (`agent/crawler.py`), and load anything you drop in by hand as YAML, DOCX, or EML (`agent/manual_loader.py`).
2. **filter** - tag each article by region and flag container-availability signals, dropping whatever doesn't match (`agent/filter.py`).
3. **summarize** - write a short summary per article plus the executive briefing, through whatever model you configured (`agent/summarizer.py`).
4. **compose** - render the result. Freight goes to HTML/Markdown via Jinja2; the rest build slide decks through `shared/pptx_builder.py`.
5. **save** - write the files, or for the dashboard, upsert into SQLite.

`agents/registry.py` maps each name to its class, so adding an agent is a matter of subclassing `BaseAgent` and registering it. There's no orchestration framework underneath - the base class is the contract, and the registry is the wiring.

Agents run in two modes. `run()` is the full pipeline with model summaries and artifacts, used by the CLI. `run_for_dashboard()` skips the model and the slides, just collecting and filtering into SQLite so the dashboard stays cheap and fast. The scheduler (`web/scheduler.py`) calls the dashboard mode on an interval.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium      # needed for site scraping and PDF rendering

cp .env.example .env             # then add your provider key
```

Pick a model in `config/settings.yaml` under `llm.model` and set the matching key in `.env`:

- `claude-sonnet-4-6` needs `ANTHROPIC_API_KEY`
- `gpt-4o-mini` needs `OPENAI_API_KEY`
- `ollama/llama3.1` needs `LLM_API_BASE` pointing at your local server

## Running the reports (CLI)

```bash
python main.py                          # freight, the default
python main.py --agent oil              # one specific agent
python main.py --agent all              # every agent except the cost stub
python main.py --list                   # see what's available
python main.py --agent freight --dry-run --no-scrape
```

`--dry-run` collects and summarizes but writes nothing. `--no-scrape` skips Playwright and works off Google News plus manual input alone, which is handy when you don't want to wait on a browser. Reports land in `output/`.

## Running the dashboard (web)

```bash
python serve.py                  # http://localhost:8000
python serve.py --port 8080
python serve.py --scrape-now     # scrape once before serving
```

The dashboard pulls from SQLite and refreshes on the scheduler's interval. First run starts empty until a scrape populates it.

## Tests

```bash
pytest tests/ -v
```

## Deployment

`Dockerfile` and `docker-compose.yml` build and run the web service. The compose file ships with Traefik labels pointing at a placeholder host - set your own domain in the `Host(...)` rule before you deploy, and pass the provider key through the environment.

## Layout

```
agent/      collection, filtering, summarizing, composing (the freight pipeline)
agents/     the multi-agent harness: base class, registry, per-agent logic + config
web/        FastAPI app, scheduler, scrapers, SQLite, templates
shared/     PPTX builders and the Art. 50 disclosure layer (disclosure.py)
config/     sources, regions, settings, and disclosure wording as YAML
templates/  Jinja2 templates for the freight HTML/Markdown reports
tests/      pytest suite
```

## EU AI Act

Operations Risk Monitor writes summaries with a language model, so its output carries transparency
marking under Article 50.

- Content the model wrote is marked, and content it did not write is left alone. Two paths write
  the `summary` field - the CLI pipeline calls the model, the dashboard pipeline deliberately
  skips it - so every article carries a `summary_source` of `model` or `scraped`, recorded when the
  text is created. Marking reads that field rather than guessing from the text.
- Marking travels in two channels: a line you can read, and metadata a machine can detect. HTML
  `<meta>` tags, Markdown YAML front matter, the PDF info dictionary plus an XMP packet, PPTX core
  properties, and an `ai_generated` envelope with an `X-AI-Generated` header on the JSON endpoints
  that return summaries. The schema is `automatiqa-disclosure/1`.
- A report with no model-written text carries no marking and no disclosure footer. The absence is
  informative, which is what makes the presence worth trusting.
- The model is never named in visible output. Article 50 requires disclosing that content is
  AI-generated, not which system produced it. It does travel in file metadata, because you choose
  the model yourself - set `include_model: false` in `config/disclosure.yaml` to turn that off.
- Wording lives in `config/disclosure.yaml`, so it changes in one place.

If you deploy this for other people in the EU you are a deployer: keep the marking intact, make
sure recipients can recognise AI-generated content as such, and secure the dashboard, which ships
unauthenticated on purpose.

Full classification and reasoning: [COMPLIANCE.md](COMPLIANCE.md). Upstream model providers:
[NOTICE](NOTICE).

## License

MIT. See [LICENSE](LICENSE). Attribution and upstream model providers: [NOTICE](NOTICE).

---

Automatiqa Lab by Aleks Sidorecs · aleks@automatiqa.io
