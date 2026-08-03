"""Tests for the EU AI Act Article 50 disclosure layer.

The property under test throughout is truthfulness, in both directions:

- text a model wrote is marked, in every format the report leaves in;
- text a model did not write is not marked, and an artefact with no model text
  carries no marking at all.

The second half is the one that breaks silently in production, so most of these
tests assert the absence of a label rather than its presence.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.rss_aggregator import Article
from shared import disclosure


# ── Fixtures ─────────────────────────────────────────────────────────────────

def make_article(title="Mombasa berth waiting times rise", summary="", source="scraped"):
    a = Article(
        title=title,
        url="https://example.com/story",
        source="Splash247",
        published_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
        raw_text="Vessels are queueing outside Mombasa.",
    )
    a.summary = summary
    a.summary_source = source
    return a


@pytest.fixture
def drafted_article():
    return make_article(summary="Waiting times at Mombasa doubled this week.", source="model")


@pytest.fixture
def scraped_article():
    return make_article(summary="Vessels are queueing outside Mombasa.", source="scraped")


@pytest.fixture
def env(drafted_article):
    return disclosure.report_envelope([drafted_article], exec_summary_source="model")


# ── Conditional emission ─────────────────────────────────────────────────────

class TestConditionalEmission:
    def test_envelope_is_none_when_nothing_drafted(self):
        assert disclosure.envelope(scope=["article_summaries"], drafted=0) is None

    def test_envelope_is_none_without_scope(self):
        assert disclosure.envelope(scope=[], drafted=5) is None

    def test_report_envelope_is_none_for_scraped_only(self, scraped_article):
        assert disclosure.report_envelope([scraped_article] * 4) is None

    def test_report_envelope_is_none_for_empty_report(self):
        assert disclosure.report_envelope([]) is None

    def test_human_written_exec_summary_alone_does_not_mark(self, scraped_article):
        # A hand-written briefing over scraped articles is nobody's AI output.
        assert disclosure.report_envelope(
            [scraped_article], exec_summary_source="scraped"
        ) is None

    def test_model_exec_summary_marks_even_when_articles_are_scraped(self, scraped_article):
        env = disclosure.report_envelope([scraped_article], exec_summary_source="model")
        assert env is not None
        assert env["scope"] == ["executive_summary"]
        assert env["items_drafted"] == 1
        assert env["items_total"] == 2

    def test_all_emitters_are_empty_without_an_envelope(self):
        assert disclosure.html_meta(None) == ""
        assert disclosure.markdown_front_matter(None) == ""
        assert disclosure.keywords_string(None) == ""
        assert disclosure.xmp(None) == ""
        assert disclosure.report_footer(None) == ""
        assert disclosure.http_headers(None) == {}
        assert disclosure.mark_json({"articles": []}, None) == {"articles": []}


# ── Envelope shape ───────────────────────────────────────────────────────────

class TestEnvelope:
    def test_required_fields_present(self, env):
        for key in ("value", "scope", "schema", "system", "items_drafted", "ts"):
            assert key in env
        assert env["value"] is True
        assert env["schema"] == "automatiqa-disclosure/1"
        assert env["system"] == "ops-risk-monitor"

    def test_scope_names_both_generated_parts(self, env):
        assert env["scope"] == ["article_summaries", "executive_summary"]

    def test_counts_include_the_executive_summary(self, drafted_article, scraped_article):
        env = disclosure.report_envelope(
            [drafted_article, scraped_article], exec_summary_source="model"
        )
        assert env["items_drafted"] == 2   # one summary + the briefing
        assert env["items_total"] == 3     # two articles + the briefing

    def test_model_is_named_in_metadata(self, env):
        # This project turns model identity on because the operator picks the
        # model. It must never reach visible page text - see the report footer.
        assert env["model"] == "claude-sonnet-4-6"
        assert "claude" not in disclosure.report_footer(env).lower()

    def test_model_omitted_when_config_opts_out(self, tmp_path, monkeypatch):
        cfg = tmp_path / "disclosure.yaml"
        cfg.write_text(
            "schema: automatiqa-disclosure/1\nsystem: ops-risk-monitor\n"
            "include_model: false\nmodel: claude-sonnet-4-6\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(disclosure, "CONFIG_PATH", cfg)
        monkeypatch.setattr(disclosure, "_config_cache", None)
        env = disclosure.envelope(scope=["article_summaries"], drafted=1)
        assert "model" not in env

    def test_idempotent_marking(self, env):
        once = disclosure.mark_json({"articles": []}, env)
        twice = disclosure.mark_json(once, env)
        assert once == twice
        assert disclosure.already_marked(twice)


# ── Per-item provenance ──────────────────────────────────────────────────────

class TestItemProvenance:
    def test_scraped_article_is_not_marked_as_ai(self, scraped_article):
        marking = disclosure.item_marking(scraped_article)
        assert marking["ai_generated"] is False
        assert marking["chip"] == "source text"

    def test_drafted_article_is_marked(self, drafted_article):
        marking = disclosure.item_marking(drafted_article)
        assert marking["ai_generated"] is True
        assert marking["chip"] == "drafted by AI"

    def test_empty_summary_is_never_ai_even_if_flagged(self):
        # Nothing was written, so nothing can be claimed.
        assert disclosure.is_model_written(make_article(summary="", source="model")) is False

    def test_reads_provenance_from_a_dict_row(self):
        assert disclosure.is_model_written({"summary": "x", "summary_source": "model"})
        assert not disclosure.is_model_written({"summary": "x", "summary_source": "scraped"})

    def test_missing_provenance_defaults_to_scraped(self):
        assert disclosure.summary_source_of({"summary": "x"}) == "scraped"
        assert disclosure.is_model_written({"summary": "x"}) is False

    def test_reads_provenance_from_a_sqlite_row(self, tmp_path):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (summary TEXT, summary_source TEXT)")
        conn.execute("INSERT INTO t VALUES ('a model summary', 'model')")
        row = conn.execute("SELECT * FROM t").fetchone()
        assert disclosure.is_model_written(row) is True
        conn.close()


# ── Format emitters ──────────────────────────────────────────────────────────

class TestHtmlMeta:
    def test_emits_the_three_required_tags(self, env):
        meta = disclosure.html_meta(env)
        assert '<meta name="ai-generated" content="true">' in meta
        assert '<meta name="ai-scope" content="article_summaries,executive_summary">' in meta
        assert '<meta name="ai-schema" content="automatiqa-disclosure/1">' in meta

    def test_model_travels_in_metadata_only(self, env):
        assert '<meta name="ai-model" content="claude-sonnet-4-6">' in disclosure.html_meta(env)


class TestMarkdownFrontMatter:
    def test_front_matter_is_valid_yaml_block(self, env):
        import yaml

        fm = disclosure.markdown_front_matter(env)
        assert fm.startswith("---\n")
        assert fm.endswith("---\n")
        parsed = yaml.safe_load(fm.strip().strip("-"))
        assert parsed["ai_generated"] is True
        assert parsed["ai_schema"] == "automatiqa-disclosure/1"
        assert parsed["ai_scope"] == ["article_summaries", "executive_summary"]
        assert parsed["ai_model"] == "claude-sonnet-4-6"


class TestKeywordsString:
    def test_flat_form_carries_every_fact(self, env):
        kw = disclosure.keywords_string(env)
        assert "ai-generated=true" in kw
        assert "scope=article_summaries|executive_summary" in kw
        assert "schema=automatiqa-disclosure/1" in kw
        assert "model=claude-sonnet-4-6" in kw


class TestXmp:
    def test_packet_is_well_formed_xml_with_the_facts(self, env):
        import xml.etree.ElementTree as ET

        packet = disclosure.xmp(env)
        assert packet.startswith("<?xpacket begin=")
        body = packet.split("?>", 1)[1].rsplit("<?xpacket", 1)[0]
        root = ET.fromstring(body)
        ns = "{https://automatiqa.io/ns/disclosure/1#}"
        found = {el.tag.replace(ns, ""): el.text for el in root.iter() if ns in el.tag}
        assert found["aiGenerated"] == "true"
        assert found["schemaVersion"] == "automatiqa-disclosure/1"
        assert "article_summaries" in found["scope"]


class TestHttpMarking:
    def test_header_and_envelope_travel_together(self, env):
        assert disclosure.http_headers(env) == {"X-AI-Generated": "true"}
        payload = disclosure.mark_json({"articles": [{"title": "x"}]}, env)
        assert payload["ai_generated"]["schema"] == "automatiqa-disclosure/1"
        assert payload["articles"] == [{"title": "x"}]


# ── File round trips ─────────────────────────────────────────────────────────

class TestPdfRoundTrip:
    """Mark a real PDF on disk, reopen it, read the marking back.

    Playwright renders the production PDF, but a browser is not needed to prove
    the post-pass works: the same mark_pdf() runs against a real PDF file here.
    """

    def test_metadata_and_xmp_survive_the_incremental_save(self, tmp_path, env):
        fitz = pytest.importorskip("fitz")

        pdf_path = tmp_path / "report.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Operations Risk Monitor - weekly briefing")
        doc.save(str(pdf_path))
        doc.close()

        assert disclosure.mark_pdf(pdf_path, env) is True

        reopened = fitz.open(str(pdf_path))
        meta = reopened.metadata
        xml_meta = reopened.get_xml_metadata()
        reopened.close()

        assert "ai-generated=true" in meta["keywords"]
        assert "automatiqa-disclosure/1" in meta["keywords"]
        assert "claude-sonnet-4-6" in meta["keywords"]
        assert meta["subject"].startswith("The executive summary and the article summaries")
        assert "aiq:aiGenerated>true" in xml_meta
        assert "automatiqa-disclosure/1" in xml_meta

    def test_unmarked_report_leaves_the_pdf_alone(self, tmp_path):
        fitz = pytest.importorskip("fitz")

        pdf_path = tmp_path / "clean.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()
        before = pdf_path.read_bytes()

        assert disclosure.mark_pdf(pdf_path, None) is False
        assert pdf_path.read_bytes() == before

    def test_missing_file_fails_soft(self, tmp_path, env):
        assert disclosure.mark_pdf(tmp_path / "nope.pdf", env) is False


class TestPptxRoundTrip:
    def test_core_properties_survive_save_and_reopen(self, tmp_path, env):
        pptx = pytest.importorskip("pptx")
        from shared.pptx_builder import add_blank_slide, create_presentation

        prs = create_presentation(disclosure=env)
        add_blank_slide(prs)
        out = tmp_path / "deck.pptx"
        prs.save(str(out))

        props = pptx.Presentation(str(out)).core_properties
        assert props.author == "ops-risk-monitor"
        assert "ai-generated=true" in props.category
        assert "automatiqa-disclosure/1" in props.category
        assert props.content_status.startswith("ai-generated")
        assert props.comments.startswith("The executive summary and the article summaries")

    def test_deck_without_model_text_carries_no_marking(self, tmp_path):
        pptx = pytest.importorskip("pptx")
        from shared.pptx_builder import add_blank_slide, create_presentation

        prs = create_presentation(disclosure=None)
        add_blank_slide(prs)
        out = tmp_path / "clean.pptx"
        prs.save(str(out))

        props = pptx.Presentation(str(out)).core_properties
        assert "ai-generated" not in (props.category or "")
        assert (props.content_status or "") == ""
        # python-pptx seeds comments with its own boilerplate; what matters is
        # that no disclosure claim was written into it.
        assert "generated by AI" not in (props.comments or "")


# ── Rendered report, end to end ──────────────────────────────────────────────

class TestRenderedReport:
    """Build a report through the real pipeline with the model call mocked out,
    then machine-detect the marking in the rendered HTML and Markdown."""

    @staticmethod
    def _context(articles, exec_summary, exec_source):
        from agent.composer import build_template_context

        return build_template_context(
            articles,
            exec_summary,
            {"regions": {"east_africa": {"display_name": "East Africa"}}},
            {"shipping_lines": {}},
            {"agent": {}},
            exec_summary_source=exec_source,
        )

    def test_model_written_report_is_marked_in_html_and_markdown(self, monkeypatch):
        from agent import summarizer
        from agent.composer import render_html, render_markdown

        monkeypatch.setattr(
            summarizer, "_complete", lambda *a, **k: "Congestion at Mombasa worsened."
        )
        article = make_article()
        article.regions = ["east_africa"]
        article.container_signal = "shortage"
        summarizer.summarize_all([article], {"llm": {}})
        assert article.summary_source == "model"

        ctx = self._context([article], "Three paragraphs of briefing.", "model")

        html = render_html(ctx)
        assert '<meta name="ai-generated" content="true">' in html
        assert "The executive summary and the article summaries above were generated by AI" in html
        assert "drafted by AI" in html

        md = render_markdown(ctx)
        assert md.startswith("---\nai_generated: true") or "ai_generated: true" in md.split("---")[1]
        assert "ai_schema: automatiqa-disclosure/1" in md
        assert "_(drafted by AI)_" in md or "_drafted by AI_" in md

    def test_report_without_model_text_carries_no_marking(self):
        from agent.composer import render_html, render_markdown

        article = make_article(summary="Vessels are queueing outside Mombasa.")
        article.regions = ["east_africa"]
        article.container_signal = "shortage"

        ctx = self._context([article], "A hand-written briefing.", "scraped")
        assert ctx["ai_envelope"] is None

        html = render_html(ctx)
        assert "ai-generated" not in html
        assert "generated by AI" not in html
        assert "source text" in html  # provenance is still stated, truthfully

        md = render_markdown(ctx)
        assert not md.startswith("---\ngenerated_at")
        assert "ai_generated" not in md

    def test_fallback_headline_summary_is_not_claimed_as_ai(self, monkeypatch):
        from agent import summarizer

        def boom(*a, **k):
            raise RuntimeError("provider down")

        monkeypatch.setattr(summarizer, "_complete", boom)
        article = make_article()
        summarizer.summarize_all([article], {"llm": {}})

        assert article.summary == article.title
        assert article.summary_source == "scraped"
        assert disclosure.report_envelope([article]) is None


# ── Database provenance ──────────────────────────────────────────────────────

class TestDatabaseProvenance:
    @staticmethod
    def _fresh(tmp_path, monkeypatch):
        from web import database

        monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
        return database

    def test_new_database_has_the_column(self, tmp_path, monkeypatch):
        database = self._fresh(tmp_path, monkeypatch)
        database.init_db()
        with database.db_session() as db:
            cols = {r[1] for r in db.execute("PRAGMA table_info(articles)").fetchall()}
        assert "summary_source" in cols

    def test_default_is_scraped(self, tmp_path, monkeypatch):
        database = self._fresh(tmp_path, monkeypatch)
        database.init_db()
        with database.db_session() as db:
            database.upsert_article(db, "t", "u", "s", "freight", summary="scraped text")
        with database.db_session() as db:
            row = db.execute("SELECT * FROM articles").fetchone()
        assert row["summary_source"] == "scraped"
        assert disclosure.report_envelope([dict(row)]) is None

    def test_model_written_row_is_marked(self, tmp_path, monkeypatch):
        database = self._fresh(tmp_path, monkeypatch)
        database.init_db()
        with database.db_session() as db:
            database.upsert_article(
                db, "t", "u", "s", "freight",
                summary="A model summary.", summary_source="model",
            )
        with database.db_session() as db:
            row = dict(db.execute("SELECT * FROM articles").fetchone())
        env = disclosure.report_envelope([row])
        assert env is not None
        assert env["scope"] == ["article_summaries"]

    def test_migration_backfills_existing_rows_as_scraped(self, tmp_path, monkeypatch):
        """A database written before the column existed must not start claiming
        model authorship it cannot prove."""
        database = self._fresh(tmp_path, monkeypatch)
        db_path = tmp_path / "test.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        legacy = sqlite3.connect(str(db_path))
        legacy.executescript("""
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, url TEXT DEFAULT '', source TEXT DEFAULT '',
                module TEXT NOT NULL, region TEXT DEFAULT '', signal TEXT DEFAULT '',
                summary TEXT DEFAULT '', raw_text TEXT DEFAULT '', published_at TEXT,
                scraped_at TEXT DEFAULT (datetime('now')),
                UNIQUE(title, module)
            );
            INSERT INTO articles (title, module, summary) VALUES ('old row', 'freight', 'legacy text');
        """)
        legacy.commit()
        legacy.close()

        database.init_db()   # must migrate, not crash

        with database.db_session() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM articles WHERE title='old row'").fetchone()
        assert row["summary_source"] == "scraped"

        database.init_db()   # second run is a no-op

    def test_dashboard_path_records_scraped(self, tmp_path, monkeypatch):
        """run_for_dashboard skips the LLM, so its rows must say so."""
        database = self._fresh(tmp_path, monkeypatch)
        database.init_db()

        from agents.base import BaseAgent

        class StubAgent(BaseAgent):
            name = "stub"
            description = "Stub"

            def load_config(self):
                return {}

            def collect(self, config):
                return [make_article(summary="Raw page text about a port strike.")]

            def filter_articles(self, articles, config):
                return articles

            def summarize(self, articles, config):
                raise AssertionError("dashboard mode must not call the model")

            def compose(self, articles, exec_summary, config):
                return {}

            def save(self, artifacts, config):
                return []

        with database.db_session() as db:
            StubAgent().run_for_dashboard(db)

        with database.db_session() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM articles").fetchall()]
        assert rows and all(r["summary_source"] == "scraped" for r in rows)
        assert disclosure.report_envelope(rows) is None


# ── Wording lives in config, not in code ─────────────────────────────────────

class TestWording:
    def test_strings_come_from_the_config_file(self):
        assert disclosure.item_chip() == "drafted by AI"
        assert disclosure.source_chip() == "source text"
        assert disclosure.wording("report.footer").startswith(
            "The executive summary and the article summaries above were generated by AI"
        )

    def test_footer_never_names_the_model(self):
        footer = disclosure.wording("report.footer")
        for name in ("claude", "sonnet", "anthropic", "gpt", "openai", "ollama"):
            assert name not in footer.lower()

    def test_missing_config_degrades_to_no_wording(self, tmp_path, monkeypatch):
        monkeypatch.setattr(disclosure, "CONFIG_PATH", tmp_path / "absent.yaml")
        monkeypatch.setattr(disclosure, "_config_cache", None)
        assert disclosure.wording("report.footer") == ""
        assert disclosure.item_chip() == ""

    def test_config_matches_the_canonical_schema_version(self):
        cfg = disclosure.load_config(Path(disclosure.CONFIG_PATH))
        assert cfg["schema"] == disclosure.SCHEMA
        assert cfg["version"] == 1
