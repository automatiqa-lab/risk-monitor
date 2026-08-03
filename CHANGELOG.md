# Changelog

Notable changes to Operations Risk Monitor. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Pre-1.0: interfaces may change between
minor versions.

## [0.1.0] - 2026-08-03

First tagged release. The project has been running since June; this marks the point where it gained
tests, continuous integration, and transparency marking on every output.

### Added

- **EU AI Act Article 50 disclosure across every output path.** Model-written text is marked, and
  everything else is left alone. Marking is conditional: a report with no model-written summaries
  carries no marking and no disclosure sentence at all, so the absence of a marking stays a truthful
  claim.
  - Four chokepoints cover fourteen surfaces: PPTX core properties (all six decks), the shared
    template context (HTML meta tags plus Markdown front matter), a PyMuPDF post-pass on the
    Playwright PDF, and the dashboard plus JSON API.
  - `/api/articles` and `/api/modules/{module}` carry an `ai_generated` envelope and an
    `X-AI-Generated` header. Endpoints that return no model-written text stay unmarked.
  - Briefings and dashboard pages carry separate wording, both held in `config/disclosure.yaml`: a
    briefing names its executive summary, a dashboard names its per-row chips.
  - The model id travels in file metadata only, never in visible page text, behind an
    `include_model` switch for operators running a local endpoint.
- **`summary_source` provenance.** `run_for_dashboard` skips the LLM, so the summary field held
  either model text or scraped text depending on the path. The source is now recorded where the row
  is created, with a migration that backfills existing rows as `scraped` since they cannot be proven
  otherwise. Without it, uniform marking would have been false in both directions.
- `COMPLIANCE.md` with the role classification and the Annex III screening, and a `NOTICE` covering
  hosted providers and locally run open-weight models.
- First continuous integration workflow, running the test suite on push and pull request.
- Test suite covering the marking round trips: PDF metadata and XMP read back, PPTX core properties
  through python-pptx, the database migration, and a scraped article staying unmarked.

### Changed

- Contact address and project links moved to `aleks@automatiqa.io` and `www.automatiqa.io` after the
  domain migration.
