# EU AI Act compliance - Operations Risk Monitor

Not legal advice. This file records how Operations Risk Monitor meets its transparency
obligations, and why. It is the project's defence file: every entry is dated, and its history is
in git.

Contact for compliance and incident reports: aleks@automatiqa.io

## Role classification

**Operations Risk Monitor is a provider.** Automatiqa Lab publishes the system under its own name
at github.com/automatiqa-lab/risk-monitor, and it is a complete running system rather than a
template: clone it, add a provider key, and it collects, summarises and publishes without further
assembly. That makes the lab the provider under Art. 3(3), and Art. 50(1) and 50(2) apply. The
Art. 2(12) free-and-open-source exemption is not relied on - it is formal only and dies on
commercialisation, so the marking is built in rather than argued around. Whoever deploys the
dashboard or forwards a report is a separate deployer with their own Art. 50(4) obligations; see
"What remains the deployer's job".

The lab is not a GPAI provider. It does not train, fine-tune or distribute models. Chapter V
obligations sit with Anthropic, OpenAI, or whoever supplies the model the operator configured -
see [NOTICE](NOTICE).

Recorded 2026-08-03.

## Obligations that apply

| Obligation | Applies | How this project meets it |
|---|---|---|
| Art. 50(1) interaction disclosure | no - no direct interaction | Nothing here holds a conversation with a natural person. Reports and the dashboard are artefacts, covered by 50(2), not a session with an agent. There is no chat surface to disclose on. |
| Art. 50(2) machine-readable marking | yes | HTML `<meta>` tags, Markdown YAML front matter, PDF info dictionary plus XMP packet, PPTX core properties, JSON `ai_generated` envelope plus `X-AI-Generated` header. All from one envelope in `shared/disclosure.py`. |
| Art. 50(4) deployer disclosure | deployer-side | See "What remains the deployer's job" |
| Art. 4 AI literacy | yes | This file, the README section, and `config/disclosure.yaml` where the wording lives |
| Art. 5 prohibited practices | screened | No biometrics, no emotion inference, no social scoring, no manipulation of individuals. The system reads published trade press and public authority feeds about ports, vessels and commodities. It has no natural-person subjects at all. |
| Annex III high-risk | no | See screening below |

## Annex III screening

Screened against all eight Annex III areas. The two that need more than a one-word answer:

**Critical infrastructure - the near miss, and why it misses.** Point 2 covers AI systems intended
to be used as *safety components* in the management and operation of critical digital
infrastructure, road traffic, and the supply of water, gas, heating and electricity. Operations
Risk Monitor watches ports, ocean freight, marine fuel and inland diesel, which sounds adjacent
enough to deserve a real answer rather than an assertion. It falls outside on two independent
grounds. First, commercial logistics and container shipping are not in the enumerated list; the
entry is about utilities and traffic management, not supply chains. Second, and this is the
stronger ground, the system is not a safety component of anything. It reads news and public feeds
and writes a briefing. Nothing it produces actuates, controls, gates or feeds a control loop. A
human reads the report and decides. Remove the system and operations continue unchanged, degraded
only in awareness. If a future version were wired into a routing or berth-allocation system that
acts without a human in the path, this screening is void and must be re-run before that ships.

**Employment.** Named here because it is the near-miss to watch across this portfolio: anything
that screens, ranks or evaluates people for work decisions lands in Annex III even when it looks
like a productivity tool. The `strikes` agent tracks labour action - port strikes, union ballots,
escalation timelines - as macro events affecting cargo movement. It does not identify, score,
evaluate or make decisions about any individual worker, and it is not used in hiring, task
allocation, promotion or termination. Reporting that a strike is happening is not worker
management.

The remaining six - biometrics, education and vocational training, essential private and public
services including credit and insurance, law enforcement, migration and border control,
administration of justice and democratic processes - have no contact with what this system does.

Screened 2026-08-03. Re-run on any change of purpose or new modality.

## What this project does out of the box

The model boundary is one function, `agent/summarizer.py::_complete`. Exactly two kinds of text
cross it: the per-article summary and the executive briefing. Everything else in a report -
headlines, dates, source names, links, prices, vessel counts - is reproduced unmodified from the
source and is never marked.

`shared/disclosure.py` builds one envelope from the article list and emits it per format. Marking
is applied at the point each artefact is created: `create_presentation()` for every slide deck,
`build_template_context()` for HTML and Markdown, a PyMuPDF post-pass inside `render_pdf()` because
Playwright's `page.pdf()` exposes no metadata API, and `mark_json()` plus the `X-AI-Generated`
header on the two JSON endpoints that carry summaries. The envelope is `None` when nothing was
model-written, and every emitter returns empty on `None`, so an unmarked artefact is a truthful
claim rather than a missed call.

Marking schema: `automatiqa-disclosure/1`, wording from `config/disclosure.yaml` version 1.

## Carve-outs, and why

**Scraped text is not marked as AI-generated.** This is the one that took real work. `Article.summary`
and the `summary` column in SQLite hold either model output or raw scraped text, depending on which
path wrote them: `run()` calls the model, `run_for_dashboard()` deliberately skips it for speed and
cost. One column, two provenances. Marking it uniformly would have been false in both directions -
claiming AI authorship for scraped press text, and staying silent about it where the model really
wrote it. The fix is a `summary_source` field ('model' | 'scraped') on the `Article` dataclass and
on the `articles` table, written at the point of creation, never inferred later. Every marking
decision reads that field. Items that came off the source page carry a "source text" chip instead:
provenance is still stated, just stated correctly.

**Rows that predate the column are treated as scraped.** The migration in `web/database._migrate`
backfills existing rows to 'scraped'. They were written by both paths and there is no record of
which. Of the two possible errors, claiming model authorship we cannot prove is the worse one.

**Hand-written briefings are not marked.** `input/weekly_briefing` and the conflict brief's
situation assessment are the author's own prose. When one is present it replaces the generated
briefing, and `exec_summary_source` records that, so the scope in the envelope drops
`executive_summary` and the count drops by one. The canned fallback strings that `generate_
executive_summary` returns when the provider is down are also hand-written English, and are
likewise not marked.

**The model is never named in visible output.** Art. 50 requires disclosing that content is
AI-generated, not which system produced it. Model identity is on in this project's *metadata* -
PDF keywords, XMP, PPTX category, HTML meta, JSON envelope - because the operator picks the model
themselves in `config/settings.yaml`, so it discloses nothing they did not choose, and it makes a
bad summary traceable during incident investigation. Set `include_model: false` in
`config/disclosure.yaml` when pointing at a local endpoint. The visible footer names no model, and
a test asserts it.

**The dashboard shows chips, not the report footer.** The approved footer sentence talks about an
executive summary and article summaries, which is accurate on a briefing and wrong on a dashboard
page. Dashboard pages carry the meta tags plus a per-row chip; the footer sentence is suppressed
there on purpose.

## What remains the deployer's job

**Authentication.** The dashboard and the JSON API ship unauthenticated, by the author's explicit
choice for a local and lab tool. That is a deployer responsibility, not a defect to be argued away:
if you expose it beyond localhost you are putting an AI system into service for other people, and
securing that surface - auth, network policy, rate limiting - is yours. The AI Act does not
mandate authentication, but Art. 50(4) obligations attach to whoever publishes the output, and you
cannot meet them on a surface you do not control.

**Keep the marking intact.** If you reformat, re-publish or forward a report, carry the disclosure
with it. A workflow that rebuilds the payload and drops the `ai_generated` field breaks the chain,
and the obligation then sits with you. The marking travels as metadata precisely so it survives
format conversion.

**Disclose to your own recipients.** If you republish these summaries to inform the public on
matters of public interest, Art. 50(4) applies to you directly, regardless of what this tool does.

**Re-run the screening if you change the purpose.** The Annex III conclusion above rests on this
being a read-and-report system with a human in the decision path. Wire it into something that acts
and it is a different classification.

## Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-08-03 | Dashboard pages carry their own disclosure sentence, not the briefing footer. | The briefing sentence names an executive summary and article summaries, which is false on a dashboard. Both strings live in `config/disclosure.yaml` so the difference is a reviewed decision rather than a branch in code, and neither surface ends up silent. |
| 2026-08-03 | Classified as provider, not template publisher | Ships as a complete running system under the lab's name, not a workflow someone assembles |
| 2026-08-03 | Art. 50(1) recorded as not applicable | No conversational surface; every output is an artefact, which is 50(2) territory |
| 2026-08-03 | Added `summary_source` to `Article` and to the `articles` table | One column held both model and scraped text; uniform marking would have been false in both directions |
| 2026-08-03 | Legacy rows backfilled as 'scraped' | Provenance cannot be recovered from the text, and over-claiming is the worse error |
| 2026-08-03 | Model identity ON, in metadata only | The operator chooses the model, so naming it discloses nothing they did not choose, and it aids incident investigation. Never in visible text. |
| 2026-08-03 | Report footer suppressed on dashboard pages | The approved wording describes a briefing; per-row chips carry the human-readable channel there |
| 2026-08-03 | PDF marked by PyMuPDF post-pass rather than at render time | Playwright's `page.pdf()` has no metadata API; PyMuPDF was already a dependency, so no new one was added |
| 2026-08-03 | Dashboard authentication left out of scope | Author's explicit decision for a lab tool; recorded as a deployer responsibility rather than silently omitted |
