from typing import Dict

from core.models import CIEvent, FailureCategory
from adapters import get_adapter
from .base import FailureMonitor, FailureAnalysis


class HeuristicFailureMonitor(FailureMonitor):
    """Deterministic log-pattern monitoring via the tech adapters.

    This is the default backend: no model required, runs fully offline.
    """

    name = "heuristic"

    def analyze(self, event: CIEvent, logs: str, repo: Dict, tech: str) -> FailureAnalysis:
        try:
            adapter = get_adapter(tech)
            ctx = adapter.analyze_failure(logs, event)
            return FailureAnalysis(
                source="heuristic",
                tech=ctx.tech or tech,
                category=ctx.category,
                root_cause=ctx.error_summary,
                confidence=ctx.confidence,
                detail=logs[:2000],
            )
        except Exception as e:
            return FailureAnalysis(
                source="heuristic",
                tech=tech,
                category=FailureCategory.UNKNOWN,
                root_cause=f"Unable to match a known failure pattern ({e})",
                confidence=0.0,
                detail=logs[:2000],
            )