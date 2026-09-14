import os
from typing import Dict, Optional

from .base import FailureMonitor, FailureAnalysis
from .heuristic_monitor import HeuristicFailureMonitor
from .ai_monitor import AIFailureMonitor


def get_failure_monitor(config: Optional[Dict] = None,
                        ai_model=None) -> FailureMonitor:
    """Build the configured FailureMonitor (plug-and-play).

    Selection order:
      1. config['monitoring']['provider'] (heuristic | ai)
      2. $CI_MONITOR_PROVIDER env var
      3. default: heuristic (offline, deterministic)
    """
    cfg = config or {}
    # Env var CI_MONITOR_PROVIDER takes precedence over config `provider`.
    provider = (os.environ.get("CI_MONITOR_PROVIDER")
                or (cfg.get("monitoring") or {}).get("provider")
                or "heuristic")
    provider = str(provider).lower()

    if provider == "ai":
        return AIFailureMonitor(ai_model=ai_model)
    return HeuristicFailureMonitor()


__all__ = ["FailureMonitor", "FailureAnalysis",
           "HeuristicFailureMonitor", "AIFailureMonitor", "get_failure_monitor"]