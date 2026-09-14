"""Monitoring analytics core.

The orchestrator uses a `FailureMonitor` to explain *why* the latest CI run
broke (failure category + root cause). Like the AI model layer, the monitor
backend is pluggable:
  - `heuristic`: deterministic log-pattern analysis via the tech adapters.
  - `ai`: the configured pluggable model (e.g. GitHub Copilot CLI), falling
          back to the heuristic monitor when the model is unavailable.
"""
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Dict

from core.models import CIEvent, FailureCategory


@dataclass
class FailureAnalysis:
    source: str                      # 'heuristic' | 'ai'
    tech: str                        # resolved technology
    category: FailureCategory
    root_cause: str
    confidence: float = 0.0
    detail: str = ""                 # raw analysis output (ai) or adapter error summary


class FailureMonitor(ABC):
    name = "base"

    @abstractmethod
    def analyze(self, event: CIEvent, logs: str, repo: Dict, tech: str) -> FailureAnalysis:
        """Produce a FailureAnalysis for the given failed run."""