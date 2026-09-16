from typing import Dict, List, Optional
from abc import ABC, abstractmethod
import re
import json


def parse_repo_selection(text: str) -> List[str]:
    """Extract a JSON array of repo names from a model response.

    The model is asked to return strictly an array like
    ["org/repo-a", "org/repo-b"]. Robust to markdown fences and prose around it.
    """
    if not text:
        return []
    m = re.search(r"\[[^\]]*\]", text, re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    names = []
    for it in items:
        if isinstance(it, str):
            names.append(it)
        elif isinstance(it, dict):
            candidate = it.get("full_name") or it.get("repo") or it.get("name")
            if candidate:
                names.append(str(candidate))
    return [n for n in names if n]


def parse_pr_content(text: str) -> Dict:
    """Extract {"title": ..., "body": ...} from a loose model response."""
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "title": str(data.get("title", "")).strip(),
        "body": str(data.get("body", "")).strip(),
    }


def parse_run_selection(text: str) -> Optional[str]:
    """Extract a candidate identifier from the model's run-selection reply.

    Accepts a JSON object like {"repo": "org/repo", "run_id": 123} or a bare
    string "org/repo" (or "org/repo#123"). Returns a normalized key of the form
    "org/repo" or "org/repo#123", or None when nothing useful was returned.
    """
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:
            data = None
        if isinstance(data, dict):
            repo = data.get("repo") or data.get("full_name") or data.get("name")
            run_id = data.get("run_id") or data.get("id") or data.get("run")
            if repo:
                repo = str(repo).strip("/")
                return f"{repo}#{int(run_id)}" if run_id else repo
    m = re.search(r'["\']?([^"\'\s,]+)["\']?', text)
    if m:
        key = m.group(1).strip("/")
        if key and key.lower() not in ("none", "null", "none."):
            return key
    return None


class AIModel(ABC):
    """Pluggable interface for AI-driven CI remediation.

    Implementations may wrap GitHub Copilot CLI, an LLM provider API, or any
    other model. The orchestrator depends only on this interface so the model
    backend is swappable without changing the core monitoring logic.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Model backend identifier, e.g. 'copilot', 'openai', 'anthropic'."""

    @abstractmethod
    def analyze_logs(self, logs: str, context: Optional[Dict] = None) -> str:
        """Return a concise root-cause analysis for the given CI logs.

        context may include repo, branch, workflow, job, tech hints.
        """

    @abstractmethod
    def suggest_fix(self, file_path: str, content: str, analysis: str,
                    context: Optional[Dict] = None) -> str:
        """Return the fixed content for a single file given the analysis."""

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this model backend can actually run right now."""

    def select_repos(self, candidates: List[Dict],
                     context: Optional[Dict] = None) -> List[str]:
        """Pick which candidate repos to monitor (default: all of them).

        Overridden by model backends that can reason about the candidate list.
        Returns repo keys (full_names like 'org/repo') to keep.
        """
        return [str(c.get("repo_key") or c.get("name") or "")
                for c in candidates]

    def select_run(self, candidates: List["CIEvent"],
                   context: Optional[Dict] = None) -> Optional[str]:
        """Pick which failed CI run to remediate next.

        `candidates` is the list of current failed runs (one per repo, newest
        per repo). Returns a candidate key like "org/repo" or "org/repo#123",
        or None to let the caller fall back to its own heuristic (the latest
        failed run by updated_at). Implementations that cannot reason about
        runs should just return None.
        """
        return None

    def fix_workspace(self, workspace: str, logs: str, analysis: str,
                      context: Optional[Dict] = None) -> Dict[str, str]:
        """Fix ALL failing issues directly inside a cloned workspace.

        The workspace holds a checkout of the broken branch. Implementations use
        the model to repair the code in place (e.g. Copilot CLI editing files
        and running commands). Returns {rel_path: new_file_content} for every
        file the model changed, or {} if nothing changed / unavailable.
        """
        return {}

    def create_pr_content(self, plan, event, context: Optional[Dict] = None):
        """Draft (title, body) for the remediation PR via the model.

        Returns ("", {}) to signal the caller should fall back to the
        adapter-built PR body. `plan` is an adapters.base.FixPlan, `event` a
        core.models.CIEvent.
        """
        return "", {}
