# agents/

The harness, and the seven agents that run on it. The pipeline modules they call live in `agent/`.

`base.py` defines `BaseAgent`: load config, collect, filter, summarize, compose, save. Each agent
implements the parts that differ and inherits the rest, so a new risk domain is a subclass plus a
config folder rather than a new pipeline.

`registry.py` maps a name to a class, which is what `main.py` resolves when you run one by name.

Each agent owns a directory beside it holding `config.yaml` (sources, output filename pattern, model
settings) and `regions.yaml` (its watchlist), so the code stays generic and the domain knowledge is
data. Freight is the exception: it predates the harness and reads the global `config/` files.

`BaseAgent.run_for_dashboard` deliberately skips the model - the dashboard stores raw scraped text
and refreshes far too often to justify the cost. That is why every row records a `summary_source`:
the same column holds model-written and scraped text depending on which path produced it, and
marking it uniformly would be false in both directions.
