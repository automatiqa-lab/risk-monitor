# config/

The freight pipeline's settings, as data. Per-agent configuration lives beside each agent in
`agents/<name>/` instead.

| File | Holds |
|---|---|
| `settings.yaml` | model and token budgets, output directory and filename pattern, scraping behaviour, the disabled email stub |
| `sources.yaml` | the feeds and sites the freight agent watches |
| `regions.yaml` | the region watchlist and its matching terms |
| `disclosure.yaml` | EU AI Act Article 50 wording and schema version |

`disclosure.yaml` is the one to treat carefully: it carries a legal claim rather than a preference.
It pins the marking schema, holds the separate sentences a briefing and a dashboard each use, and
switches whether the model id travels in file metadata. Model identity is on here because the
operator chooses the model and it helps when investigating a bad briefing - but it never appears in
visible page text. Turn `include_model` off if you point the model at a local endpoint and would
rather not publish your setup.

Secrets are not here. API keys come from the environment; see `.env.example`.
