"""
main.py - Operations Risk Monitor orchestrator.

Routes to the appropriate agent based on --agent flag.
Backwards-compatible: default is freight, --conflict still works.

Usage:
    python main.py                          # Default: freight agent
    python main.py --agent oil              # Global Oil News
    python main.py --agent diesel           # European Diesel Risks
    python main.py --agent strikes          # Strikes & Disruptions
    python main.py --agent all              # Run freight + oil + diesel + strikes
    python main.py --conflict               # Freight conflict sub-agent (backwards compat)
    python main.py --agent freight --dry-run --no-scrape
    python main.py --list                   # Show available agents
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

# ── Load environment variables from .env ─────────────────────────────────────
load_dotenv(override=True)

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sc_control_tower")


def main() -> None:
    from agents.registry import AGENT_NAMES, get_agent, list_agents

    parser = argparse.ArgumentParser(
        description="Operations Risk Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "agents:\n"
            "  freight       Global Freight News (HTML + MD + PDF)\n"
            "  oil           Global Oil News (PPTX)\n"
            "  diesel        European Diesel Risks (PPTX)\n"
            "  strikes       Strikes & Disruptions (PPTX)\n"
            "  weather       Weather & Natural Disasters (PPTX)\n"
            "  geopolitical  Geopolitical & Sanctions (PPTX)\n"
            "  congestion    Port Congestion Index (PPTX)\n"
            "  cost          Freight Cost Analysis (stub - future)\n"
            "  all           Run all agents (excl. cost)\n"
        ),
    )
    parser.add_argument(
        "--agent",
        choices=AGENT_NAMES + ["all"],
        default="freight",
        help="Which agent to run (default: freight)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available agents and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and summarize without saving output files",
    )
    parser.add_argument(
        "--no-scrape",
        action="store_true",
        help="Skip direct website scraping (Google News + manual only)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Override the report date label (default: today)",
    )
    # Backwards-compat flags for freight agent
    parser.add_argument(
        "--conflict",
        action="store_true",
        help="Run the Middle East conflict sub-agent (freight only)",
    )
    parser.add_argument(
        "--exclude-region",
        action="append",
        default=[],
        metavar="REGION_KEY",
        help="Exclude a region (freight only, repeatable)",
    )
    args = parser.parse_args()

    # ── List mode ────────────────────────────────────────────────────────────
    if args.list:
        print("Operations Risk Monitor - Available Agents:\n")
        for name, desc in list_agents():
            status = "(stub)" if name == "cost" else ""
            print(f"  {name:<12} {desc} {status}")
        print(f"\nUsage: python main.py --agent <name>")
        return

    # ── Determine which agents to run ────────────────────────────────────────
    if args.agent == "all":
        agents_to_run = ["freight", "oil", "diesel", "strikes", "weather", "geopolitical", "congestion"]
    else:
        agents_to_run = [args.agent]

    logger.info(
        "Operations Risk Monitor - running: %s",
        ", ".join(agents_to_run),
    )

    # ── Run agents ───────────────────────────────────────────────────────────
    try:
        for name in agents_to_run:
            # Build kwargs based on agent type
            kwargs = {
                "date_str": args.date,
                "dry_run": args.dry_run,
                "no_scrape": args.no_scrape,
            }

            # Freight-specific options
            if name == "freight":
                kwargs["exclude_regions"] = args.exclude_region
                kwargs["conflict_mode"] = args.conflict

            agent = get_agent(name, **kwargs)
            result = asyncio.run(agent.run())

            # Print results
            print(f"\n{'=' * 60}")
            print(f"  {result.summary}")
            print(f"{'=' * 60}")
            for f in result.output_files:
                print(f"  {f}")

    except KeyboardInterrupt:
        logger.info("Interrupted.")
        sys.exit(0)
    except NotImplementedError as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
