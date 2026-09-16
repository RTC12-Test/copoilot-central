import re
from typing import Dict, Optional

from core.models import CIEvent, FailureCategory
from ai import get_ai_model, AIModel
from .base import FailureMonitor, FailureAnalysis
from .heuristic_monitor import HeuristicFailureMonitor

_CATEGORY_KEYWORDS = {
    FailureCategory.SYNTAX: ["syntax", "parse error", "unexpected", "invalid syntax",
                             "missing block", "misplaced"],
    FailureCategory.COMPILATION: ["compilation", "compile error", "cannot be resolved",
                                   "undefined symbol", "type error"],
    FailureCategory.TEST_FAILURE: ["test failure", "assertion", "assert failed",
                                    "pytest", "go test", "FAIL:"],
    FailureCategory.DEPENDENCY: ["dependency", "cannot find module", "no module named",
                                  "import error", "failed to download", "could not resolve"],
    FailureCategory.LINTER_FORMAT: ["lint", "linting", "format", "fmt", "gofmt",
                                     "black", "eslint", "flake8", "unused variable"],
    FailureCategory.INFRA_SECRET: ["secret", "credential", "unauthorized", "permission denied",
                                    "authentication", "access denied", "not authorized"],
    FailureCategory.NETWORK_TIMEOUT: ["timeout", "timed out", "connection", "network",
                                       "could not connect", "resolve host"],
}

_CATEGORY_LABELS = {c.value: c for c in FailureCategory}


class AIFailureMonitor(FailureMonitor):
    """Pluggable-model monitoring backend.

    Uses the configured AI model (default: GitHub Copilot CLI) to classify the
    failure and extract the root cause. If the model is unavailable or the
    response cannot be parsed, it transparently falls back to the heuristic
    monitor so remedition never blocks on the model.
    """

    name = "ai"

    def __init__(self, ai_model: Optional[AIModel] = None):
        self.ai_model = ai_model or get_ai_model()
        self._fallback = HeuristicFailureMonitor()

    @staticmethod
    def _match_keywords(text: str) -> FailureCategory:
        low = text.lower()
        for cat, words in _CATEGORY_KEYWORDS.items():
            if any(w in low for w in words):
                return cat
        return FailureCategory.UNKNOWN

    @staticmethod
    def _parse_category(text: str) -> FailureCategory:
        low = text.lower()
        for label in _CATEGORY_LABELS:
            # Prefer explicit "category: X" mentions, then bare keyword match.
            if re.search(r"category[\s:#:\-]*(" + re.escape(label) + ")", low):
                return _CATEGORY_LABELS[label]
        return AIFailureMonitor._match_keywords(text)

    @staticmethod
    def _extract_root_cause(text: str) -> str:
        for marker in ("root cause:", "**root cause**", "root cause: "):
            if marker in text.lower():
                idx = text.lower().index(marker) + len(marker)
                rest = text[idx:].strip()
                lines = [l for l in rest.splitlines() if l.strip()]
                if lines:
                    return lines[0].lstrip("*#- \t")[:400]
        first = text.strip().splitlines()
        return first[0].lstrip("*#- \t")[:400] if first else ""

    def analyze(self, event: CIEvent, logs: str, repo: Dict, tech: str) -> FailureAnalysis:
        try:
            if not self.ai_model.is_available():
                print(f"[MONITOR:{self.name}] model '{self.ai_model.name}' unavailable; "
                      "falling back to heuristic monitor")
                return self._fallback.analyze(event, logs, repo, tech)
            print(f"[MONITOR:{self.name}] classifying failure via '{self.ai_model.name}'...")
            raw = self.ai_model.analyze_logs(logs, {
                "repo": event.repo,
                "branch": event.broken_branch,
                "workflow": event.workflow_name,
                "job": event.job_name,
                "tech": tech,
            }).strip()
            if not raw:
                raise ValueError("model returned empty analysis")
            return FailureAnalysis(
                source="ai",
                tech=tech,
                category=self._parse_category(raw),
                root_cause=self._extract_root_cause(raw) or raw[:400],
                confidence=0.8,
                detail=raw,
            )
        except Exception as e:
            print(f"[MONITOR:{self.name}] model analysis failed ({e}); "
                  "falling back to heuristic monitor")
            return self._fallback.analyze(event, logs, repo, tech)