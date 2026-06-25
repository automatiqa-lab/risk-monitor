"""
Agent Registry - maps agent names to their implementation classes.

Usage:
    from agents.registry import AGENTS, get_agent
    agent = get_agent("oil", date_str="2026-03-23")
    result = await agent.run()
"""
from __future__ import annotations

from typing import Dict, Type

from agents.base import BaseAgent


def _load_agents() -> Dict[str, Type[BaseAgent]]:
    """Lazy import to avoid circular dependencies."""
    from agents.freight_agent import FreightNewsAgent
    from agents.oil_agent import OilNewsAgent
    from agents.diesel_agent import DieselRisksAgent
    from agents.strikes_agent import StrikesAgent
    from agents.weather_agent import WeatherAgent
    from agents.geopolitical_agent import GeopoliticalAgent
    from agents.congestion_agent import CongestionAgent
    from agents.cost_agent import CostAnalysisAgent

    return {
        "freight":      FreightNewsAgent,
        "oil":          OilNewsAgent,
        "diesel":       DieselRisksAgent,
        "strikes":      StrikesAgent,
        "weather":      WeatherAgent,
        "geopolitical": GeopoliticalAgent,
        "congestion":   CongestionAgent,
        "cost":         CostAnalysisAgent,
    }


# Available agent names (for CLI choices)
AGENT_NAMES = ["freight", "oil", "diesel", "strikes", "weather", "geopolitical", "congestion", "cost"]


def get_agent(name: str, **kwargs) -> BaseAgent:
    """Instantiate an agent by name. Passes kwargs to the constructor."""
    agents = _load_agents()
    if name not in agents:
        available = ", ".join(agents.keys())
        raise ValueError(f"Unknown agent '{name}'. Available: {available}")
    return agents[name](**kwargs)


def list_agents() -> list[tuple[str, str]]:
    """Return (name, description) pairs for all registered agents."""
    agents = _load_agents()
    return [(name, cls.description) for name, cls in agents.items()]
