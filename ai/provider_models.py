from typing import Dict, List, Optional
import os
import json

from .base import AIModel, parse_repo_selection, parse_pr_content


class OpenAIModel(AIModel):
    """OpenAI API backend (plug-and-play).

    Enable by setting OPENAI_API_KEY and selecting model=openai in
    config/ai_models.yaml. Uses the official `openai` package if installed.
    """

    name = "openai"

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o",
                 timeout: int = 120):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _client(self):
        try:
            import openai
        except ImportError as e:
            raise RuntimeError("openai package not installed (pip install openai)") from e
        return openai.OpenAI(api_key=self.api_key)

    def _complete(self, system: str, user: str) -> str:
        client = self._client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            timeout=self.timeout,
        )
        return (resp.choices[0].message.content or "").strip()

    def analyze_logs(self, logs: str, context: Optional[Dict] = None) -> str:
        c = context or {}
        sys = "You are a CI remediation engineer. Return root cause, category, affected files/lines."
        user = f"Repo={c.get('repo','')} branch={c.get('branch','')} tech={c.get('tech','')}\n\nLOGS:\n{logs or '(none)'}"
        return self._complete(sys, user)

    def suggest_fix(self, file_path: str, content: str, analysis: str,
                    context: Optional[Dict] = None) -> str:
        sys = "Fix the root cause in the file. Return only the full corrected file content, no fences/markdown."
        user = f"File={file_path}\nANALYSIS={analysis}\n\n{content or ''}"
        return self._complete(sys, user)

    def select_repos(self, candidates: List[Dict],
                     context: Optional[Dict] = None) -> List[str]:
        import json
        sys = ("You are a repo-monitoring selector for a CI remediation agent. "
               "Pick the active dev repos to monitor; exclude the central agent "
               "repo, templates, samples, and dead/archived repos.")
        user = ("Reply with ONLY a JSON array of full_names to keep, "
                f"e.g. [\"org/a\",\"org/b\"].\nCANDIDATES:\n"
                f"{json.dumps([{'full_name': c.get('repo_key') or c.get('name'), 'labels': c.get('labels', [])} for c in candidates])}")
        return parse_repo_selection(self._complete(sys, user))

    def fix_workspace(self, workspace: str, logs: str, analysis: str,
                      context: Optional[Dict] = None) -> Dict[str, str]:
        """Generic (non-agentic) fix: pick affected files from the inventory,
        then regenerate each one. API models can't run commands inside a
        workspace, so this is best-effort vs. the Copilot CLI in-place fix."""
        files = self._collect_files(workspace)
        if not files:
            return {}
        c = context or {}
        sys = ("You are a CI remediation engineer. Given CI failure logs, list "
               "the relative paths of the files that must change (or might) to "
               "fix the failure. Reply with ONLY a JSON array of paths.")
        user = (f"TECH: {c.get('tech','')}\n"
                f"ANALYSIS: {analysis or ''}\nLOGS(tail): {(logs or '')[-6000:]}\n"
                "REPO FILES (sample):\n" + json.dumps(list(files)[:300]))
        picked = parse_repo_selection(self._complete(sys, user))
        if not picked:
            picked = list(files)
        changes = {}
        for rel in picked:
            if rel in files:
                fixed = self.suggest_fix(rel, files[rel], analysis or "", c)
                if fixed.strip():
                    changes[rel] = fixed.strip()
        return changes

    @staticmethod
    def _collect_files(workspace: str) -> Dict[str, str]:
        out = {}
        for root, dirs, names in os.walk(workspace):
            if ".git" in dirs:
                dirs.remove(".git")
            for n in names:
                full = os.path.join(root, n)
                rel = os.path.relpath(full, workspace)
                try:
                    with open(full, "r", encoding="utf-8", errors="ignore") as f:
                        out[rel] = f.read()
                except Exception:
                    continue
        return out

    def create_pr_content(self, plan, event, context=None):
            c = context or {}
            changed = "\n".join(f"- `{f}`" for f in (plan.files or []))
            sys = ("You draft pull requests for automated CI fixes. Respond with "
                   'ONLY JSON: {"title": "...", "body": "..."} with markdown body '
                   "sections Summary, Root Cause, Changes, Validation.")
            user = (f"Repo {event.repo} branch {event.broken_branch} run "
                    f"{event.run_id} workflow {event.workflow_name} job "
                    f"{event.job_name} tech {c.get('tech','')} category "
                    f"{c.get('category','')}\nROOT CAUSE: {plan.root_cause}\n"
                    f"FIX: {plan.fix_description}\nFILES:\n{changed}")
            out = parse_pr_content(self._complete(sys, user))
            return out.get("title", ""), out.get("body", "")

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import openai  # noqa: F401
            return True
        except ImportError:
            return False


class AnthropicModel(AIModel):
    """Anthropic (Claude) API backend (plug-and-play).

    Enable by setting ANTHROPIC_API_KEY and selecting model=anthropic in
    config/ai_models.yaml. Uses the official `anthropic` package if installed.
    """

    name = "anthropic"

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-sonnet-4-5",
                 timeout: int = 120):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _client(self):
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("anthropic package not installed (pip install anthropic)") from e
        return anthropic.Anthropic(api_key=self.api_key)

    def _complete(self, system: str, user: str) -> str:
        c = self._client()
        resp = c.messages.create(
            model=self.model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    def analyze_logs(self, logs: str, context: Optional[Dict] = None) -> str:
        c = context or {}
        sys = "You are a CI remediation engineer. Return root cause, category, affected files/lines."
        user = f"Repo={c.get('repo','')} branch={c.get('branch','')} tech={c.get('tech','')}\n\nLOGS:\n{logs or '(none)'}"
        return self._complete(sys, user)

    def suggest_fix(self, file_path: str, content: str, analysis: str,
                    context: Optional[Dict] = None) -> str:
        sys = "Fix the root cause in the file. Return only the full corrected file content, no fences."
        user = f"File={file_path}\nANALYSIS={analysis}\n\n{content or ''}"
        return self._complete(sys, user)

    def select_repos(self, candidates: List[Dict],
                     context: Optional[Dict] = None) -> List[str]:
        import json
        sys = ("You are a repo-monitoring selector for a CI remediation agent. "
               "Pick the active dev repos to monitor; exclude the central agent "
               "repo, templates, samples, and dead/archived repos.")
        user = ("Reply with ONLY a JSON array of full_names to keep, "
                f"e.g. [\"org/a\",\"org/b\"].\nCANDIDATES:\n"
                f"{json.dumps([{'full_name': c.get('repo_key') or c.get('name'), 'labels': c.get('labels', [])} for c in candidates])}")
        return parse_repo_selection(self._complete(sys, user))

    def fix_workspace(self, workspace: str, logs: str, analysis: str,
                      context: Optional[Dict] = None) -> Dict[str, str]:
        files = OpenAIModel._collect_files(workspace)
        if not files:
            return {}
        c = context or {}
        sys = ("You are a CI remediation engineer. Given CI failure logs, list "
               "the relative paths of the files that must change (or might) to "
               "fix the failure. Reply with ONLY a JSON array of paths.")
        user = (f"TECH: {c.get('tech','')}\n"
                f"ANALYSIS: {analysis or ''}\nLOGS(tail): {(logs or '')[-6000:]}\n"
                "REPO FILES (sample):\n" + json.dumps(list(files)[:300]))
        picked = parse_repo_selection(self._complete(sys, user))
        if not picked:
            picked = list(files)
        changes = {}
        for rel in picked:
            if rel in files:
                fixed = self.suggest_fix(rel, files[rel], analysis or "", c)
                if fixed.strip():
                    changes[rel] = fixed.strip()
        return changes

    def create_pr_content(self, plan, event, context=None):
            c = context or {}
            changed = "\n".join(f"- `{f}`" for f in (plan.files or []))
            sys = ("You draft pull requests for automated CI fixes. Respond with "
                   'ONLY JSON: {"title": "...", "body": "..."} with markdown body '
                   "sections Summary, Root Cause, Changes, Validation.")
            user = (f"Repo {event.repo} branch {event.broken_branch} run "
                    f"{event.run_id} workflow {event.workflow_name} job "
                    f"{event.job_name} tech {c.get('tech','')} category "
                    f"{c.get('category','')}\nROOT CAUSE: {plan.root_cause}\n"
                    f"FIX: {plan.fix_description}\nFILES:\n{changed}")
            out = parse_pr_content(self._complete(sys, user))
            return out.get("title", ""), out.get("body", "")

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import anthropic  # noqa: F401
            return True
        except ImportError:
            return False
