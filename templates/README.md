# templates/

Jinja2 templates for the freight reports. The dashboard's own templates live in `web/templates/`,
and the slide decks are built in code by `shared/pptx_builder.py` rather than templated here.

| Template | Output |
|---|---|
| `newsletter.html.j2` | the weekly HTML briefing, and the source Playwright prints to PDF |
| `newsletter.md.j2` | the same briefing as Markdown |
| `conflict.html.j2`, `conflict.md.j2` | the standalone conflict brief |

Each pair carries the EU AI Act Article 50 marking in two channels: meta tags in the HTML head and
YAML front matter at the top of the Markdown for machines, and a footer sentence plus per-item chips
for the reader. All of it is conditional - a briefing with no model-written summaries renders
without any of it.

The wording is never written inline. It comes from `config/disclosure.yaml` through the template
context, so a sentence that carries a legal claim changes in one reviewed place.
