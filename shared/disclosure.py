"""EU AI Act Article 50 disclosure for Operations Risk Monitor.

Two channels, always both: a sentence a person can read, and metadata a machine
can detect after the file has been converted from HTML to PDF and forwarded on.

Four rules this module exists to enforce.

- **Conditional.** The envelope is built only when the model actually wrote
  something. A report with zero model-written summaries carries no marking and no
  footer sentence, which is what makes the presence of an envelope worth trusting.
- **Truthful per item.** ``Article.summary`` and the ``summary`` column hold either
  model text or scraped source text, depending on which path produced them. The
  ``summary_source`` field decides the marking, item by item. Marking the column
  uniformly would be a false statement in both directions.
- **Idempotent.** Keyed off ``schema``, so applying the marking twice is a no-op.
- **Config-driven.** Wording comes from ``config/disclosure.yaml``, never from a
  string literal at the call site.

Importing this module has no side effects: the config is read on first use and
cached, and a missing config degrades to no marking rather than an exception.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

logger = logging.getLogger(__name__)

SCHEMA = "automatiqa-disclosure/1"

CONFIG_PATH = Path(__file__).parent.parent / "config" / "disclosure.yaml"

# Provenance values for a summary. 'model' means the LLM wrote it; 'scraped'
# means it came off the source page or the headline. Anything else is treated as
# 'scraped', because provenance we cannot prove is provenance we do not claim.
SOURCE_MODEL = "model"
SOURCE_SCRAPED = "scraped"

# Scope vocabulary, in the product's own words.
SCOPE_ARTICLE_SUMMARIES = "article_summaries"
SCOPE_EXECUTIVE_SUMMARY = "executive_summary"

_config_cache: dict[str, Any] | None = None


# ── Config ───────────────────────────────────────────────────────────────────

def load_config(path: Path | None = None, *, refresh: bool = False) -> dict[str, Any]:
    """Read config/disclosure.yaml once and cache it.

    A missing or unreadable file yields an empty config. Callers then emit no
    wording, which fails closed rather than inventing strings.
    """
    global _config_cache
    if path is None and _config_cache is not None and not refresh:
        return _config_cache

    target = path or CONFIG_PATH
    try:
        with open(target, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning("disclosure config not found at %s - marking disabled", target)
        cfg = {}
    except Exception as exc:  # malformed YAML should not take the report down
        logger.error("disclosure config unreadable (%s) - marking disabled", exc)
        cfg = {}

    if path is None:
        _config_cache = cfg
    return cfg


def wording(key: str, default: str = "") -> str:
    """Look up a dotted key in the disclosure config, e.g. 'report.item_chip'."""
    node: Any = load_config()
    for part in key.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    return str(node).strip() if isinstance(node, str) else default


def item_chip() -> str:
    """Chip shown on a summary the model wrote."""
    return wording("report.item_chip")


def source_chip() -> str:
    """Chip shown on text lifted from the source. Not an AI marking."""
    return wording("report.source_chip")


def deployer_notice() -> str:
    return wording("deployer.notice")


def _configured_model() -> str | None:
    cfg = load_config()
    if not cfg.get("include_model"):
        return None
    model = cfg.get("model")
    return str(model) if model else None


# ── The envelope ─────────────────────────────────────────────────────────────

def envelope(
    *,
    scope: Iterable[str],
    drafted: int = 0,
    total: int = 0,
    review_state: str = "none",
    system: str | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    """The machine-readable marking, or None when there is nothing to mark.

    None rather than ``{"value": False}`` is deliberate: a consumer that finds no
    envelope learns the same thing, and we never emit a marking that implies a
    review step this product does not have.
    """
    scope_list = [s for s in scope if s]
    if drafted <= 0 or not scope_list:
        return None

    cfg = load_config()
    env: dict[str, Any] = {
        "value": True,
        "scope": scope_list,
        "schema": cfg.get("schema", SCHEMA),
        "system": system or cfg.get("system", "ops-risk-monitor"),
        "review_state": review_state,
        "items_drafted": int(drafted),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if total:
        env["items_total"] = int(total)

    chosen = model if model is not None else _configured_model()
    if chosen:
        env["model"] = chosen
    return env


def summary_source_of(item: Any) -> str:
    """Read the provenance of one item's summary, defaulting to 'scraped'.

    Accepts an Article, a SQLite Row, or a plain dict, because the same question
    gets asked in the report pipeline and in the dashboard API.
    """
    value: Any = None
    if isinstance(item, Mapping):
        value = item.get("summary_source")
    else:
        try:
            value = item["summary_source"]  # sqlite3.Row
        except (TypeError, KeyError, IndexError):
            value = getattr(item, "summary_source", None)
    return SOURCE_MODEL if value == SOURCE_MODEL else SOURCE_SCRAPED


def is_model_written(item: Any) -> bool:
    """True when this item's summary was written by the model and is non-empty."""
    if summary_source_of(item) != SOURCE_MODEL:
        return False
    if isinstance(item, Mapping):
        text = item.get("summary") or ""
    else:
        try:
            text = item["summary"] or ""
        except (TypeError, KeyError, IndexError):
            text = getattr(item, "summary", "") or ""
    return bool(str(text).strip())


def count_drafted(items: Sequence[Any]) -> int:
    return sum(1 for i in items if is_model_written(i))


def report_envelope(
    items: Sequence[Any],
    *,
    exec_summary_source: str | None = None,
    review_state: str = "none",
) -> dict[str, Any] | None:
    """Envelope for a report built from a list of articles.

    ``exec_summary_source`` is 'model' when the model wrote the executive
    briefing, 'human' when it came from a hand-written file, and None when the
    artefact has no executive summary at all. The freight report and the conflict
    brief differ on exactly this point, so it is never assumed.
    """
    drafted = count_drafted(items)
    scope: list[str] = []
    if drafted:
        scope.append(SCOPE_ARTICLE_SUMMARIES)

    total = len(items)
    if exec_summary_source is not None:
        total += 1
    if exec_summary_source == SOURCE_MODEL:
        drafted += 1
        scope.append(SCOPE_EXECUTIVE_SUMMARY)

    return envelope(scope=scope, drafted=drafted, total=total, review_state=review_state)


def already_marked(payload: Mapping[str, Any]) -> bool:
    """Idempotency check - callers may wrap a payload more than once."""
    marker = payload.get("ai_generated")
    return isinstance(marker, Mapping) and marker.get("schema") == SCHEMA


# ── Per-format emitters ──────────────────────────────────────────────────────

def report_footer(env: Mapping[str, Any] | None) -> str:
    """The human-readable sentence. Empty when nothing was model-generated."""
    return wording("report.footer") if env else ""


def mark_json(payload: dict[str, Any], env: Mapping[str, Any] | None) -> dict[str, Any]:
    """Attach the envelope to an API response body."""
    if env is None or already_marked(payload):
        return payload
    return {"ai_generated": dict(env), **payload}


def http_headers(env: Mapping[str, Any] | None) -> dict[str, str]:
    return {"X-AI-Generated": "true"} if env else {}


def html_meta(env: Mapping[str, Any] | None) -> str:
    """Meta tags for the document head. Empty string when nothing was generated."""
    if not env:
        return ""
    tags = [
        '<meta name="ai-generated" content="true">',
        f'<meta name="ai-scope" content="{",".join(env["scope"])}">',
        f'<meta name="ai-schema" content="{env["schema"]}">',
    ]
    if env.get("model"):
        tags.append(f'<meta name="ai-model" content="{env["model"]}">')
    return "\n".join(tags)


def markdown_front_matter(env: Mapping[str, Any] | None) -> str:
    if not env:
        return ""
    lines = [
        "---",
        f"generated_at: {env['ts']}",
        "ai_generated: true",
        f"ai_scope: [{', '.join(env['scope'])}]",
        f"ai_schema: {env['schema']}",
    ]
    if env.get("model"):
        lines.insert(3, f"ai_model: {env['model']}")
    return "\n".join(lines + ["---", ""])


def keywords_string(env: Mapping[str, Any] | None) -> str:
    """Flat form for metadata slots that accept only a single string.

    Used for the PDF /Keywords entry and the PPTX category property.
    """
    if not env:
        return ""
    parts = ["ai-generated=true", f"scope={'|'.join(env['scope'])}", f"schema={env['schema']}"]
    if env.get("model"):
        parts.insert(1, f"model={env['model']}")
    return "; ".join(parts)


XMP_TEMPLATE = """<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:aiq="https://automatiqa.io/ns/disclosure/1#">
   <aiq:aiGenerated>true</aiq:aiGenerated>
   <aiq:scope>{scope}</aiq:scope>
   <aiq:reviewState>{review_state}</aiq:reviewState>
   <aiq:schemaVersion>{schema}</aiq:schemaVersion>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""


def xmp(env: Mapping[str, Any] | None) -> str:
    if not env:
        return ""
    return XMP_TEMPLATE.format(
        scope=",".join(env["scope"]),
        review_state=env.get("review_state", "none"),
        schema=env["schema"],
    )


def template_block(env: Mapping[str, Any] | None) -> dict[str, Any]:
    """Everything a Jinja template needs, in one context key.

    Templates stay free of disclosure logic: they render what is here, and get an
    empty block when nothing was generated.
    """
    return {
        "envelope": dict(env) if env else None,
        "html_meta": html_meta(env),
        "front_matter": markdown_front_matter(env),
        "footer": report_footer(env),
        "item_chip": item_chip(),
        "source_chip": source_chip(),
    }


def item_marking(item: Any) -> dict[str, Any]:
    """Per-item chip data for a template: which chip, and whether it is an AI one."""
    drafted = is_model_written(item)
    return {
        "ai_generated": drafted,
        "chip": item_chip() if drafted else source_chip(),
    }


# ── File post-passes ─────────────────────────────────────────────────────────

def mark_pdf(pdf_path: Path | str, env: Mapping[str, Any] | None) -> bool:
    """Write the marking into an already-rendered PDF.

    Playwright's ``page.pdf()`` exposes no metadata API, so the document
    information dictionary and the XMP packet are written afterwards with
    PyMuPDF, incrementally, leaving the rendered pages untouched.

    Returns True when the file was marked.
    """
    if not env:
        return False
    path = Path(pdf_path)
    if not path.exists():
        logger.warning("PDF not found for marking: %s", path)
        return False

    try:
        import fitz  # PyMuPDF, already a dependency for EML attachment parsing
    except ImportError:
        logger.error("PyMuPDF unavailable - PDF left unmarked: %s", path)
        return False

    try:
        doc = fitz.open(str(path))
        meta = dict(doc.metadata or {})
        meta.update(
            {
                "producer": f"{env['system']} ({env['schema']})",
                "keywords": keywords_string(env),
                "subject": report_footer(env),
            }
        )
        doc.set_metadata(meta)
        doc.set_xml_metadata(xmp(env))
        doc.saveIncr()
        doc.close()
        return True
    except Exception as exc:
        logger.error("PDF marking failed for %s: %s", path, exc)
        return False


def mark_pptx(prs: Any, env: Mapping[str, Any] | None) -> bool:
    """Write the marking into a python-pptx Presentation's core properties.

    Core properties survive a save/reopen cycle and are readable by Office, so
    they are the PPTX equivalent of the PDF info dictionary.
    """
    if not env:
        return False
    try:
        props = prs.core_properties
        props.author = str(env["system"])
        if not (props.title or "").strip():
            props.title = "Operations Risk Monitor briefing"
        props.category = keywords_string(env)
        props.content_status = f"ai-generated; review={env.get('review_state', 'none')}"
        props.comments = report_footer(env)
        return True
    except Exception as exc:
        logger.error("PPTX marking failed: %s", exc)
        return False
