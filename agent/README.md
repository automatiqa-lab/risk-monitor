# agent/

The pipeline modules. Shared library code, not agent classes - the classes live in `agents/`.

Every agent draws on these for the five steps it runs:

| Module | Step |
|---|---|
| `rss_aggregator.py` | collect - Google News RSS, and the `Article` dataclass everything else passes around |
| `crawler.py` | collect - Playwright scraping of carrier and authority sites |
| `eml_loader.py`, `manual_loader.py` | collect - `.eml` drops with PDF/XLSX attachments, and manual YAML/DOCX input |
| `filter.py` | filter - region tagging and container-signal classification |
| `summarizer.py` | summarize - **the only module in the repo that calls a model** |
| `composer.py` | compose and save - Jinja2 HTML and Markdown, plus the PDF |

`conflict_agent.py` is a standalone sub-pipeline for Middle East conflict briefs, with its own
templates and its own output.

Two things worth knowing before changing anything here. `summarizer.py::_complete` is the single
model chokepoint, so every LLM call in the system goes through one function - keep it that way.
And the two fields it produces, an article `summary` and the executive summary, are the only
AI-written text in any output, which is what makes per-item disclosure marking possible at all.
