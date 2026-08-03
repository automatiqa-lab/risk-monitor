# shared/

Cross-cutting helpers that are not part of any one agent's pipeline.

`pptx_builder.py` builds the slide decks: one `create_presentation()` factory plus the shape helpers
every deck uses. All six PPTX agents call it, so the deck format is defined once - including the
document properties that carry the AI disclosure marking.

`disclosure.py` is the EU AI Act Article 50 layer. It loads `config/disclosure.yaml`, decides
whether a given report contains model-written text at all, and expresses that one decision in
whatever each output format allows: HTML meta tags, Markdown front matter, PDF metadata and XMP,
PPTX core properties, a JSON envelope and an HTTP header.

Its central rule is that marking is conditional. A report with no model-written summaries gets no
marking and no disclosure sentence, so the absence of a marking stays a truthful claim - which is
what lets a reader trust the presence of one. See [COMPLIANCE.md](../COMPLIANCE.md).
