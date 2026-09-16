import os
import re
import shutil
import subprocess
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .base import (AIModel, parse_repo_selection, parse_pr_content,
                   parse_run_selection)


class CopilotCLIModel(AIModel):
    """GitHub Copilot CLI backend.

    Wraps the `copilot` executable (or `gh copilot`) in non-interactive prompt
    mode (-p/--prompt) with all tools/paths allowed so it can analyze logs and
    rewrite files directly in a workspace.
    """

    name = "copilot"

    # Default recency window the model uses to qualify a repo as active.
    RECENT_PUSH_WINDOW_HOURS = 24

    def __init__(self, binary: str = "copilot", timeout: int = 180):
        self.binary = binary
        self.timeout = timeout

    def _run(self, prompt: str, cwd: Optional[str] = None) -> str:
        cmd = [self.binary, "-p", prompt,
               "--allow-all-tools", "--allow-all-paths", "--plain-diff"]
        env = dict(os.environ)
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
                cwd=cwd, env=env,
            )
        except FileNotFoundError:
            return f"[copilot] binary '{self.binary}' not found"
        except subprocess.TimeoutExpired:
            return f"[copilot] timed out after {self.timeout}s"
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        if not out and err:
            return err
        return out

    def analyze_logs(self, logs: str, context: Optional[Dict] = None) -> str:
        c = context or {}
        prompt = (
            "You are a CI remediation engineer. Analyze the following CI failure "
            f"logs for repo '{c.get('repo', '')}' branch '{c.get('branch', '')}' "
            f"workflow '{c.get('workflow', '')}' job '{c.get('job', '')}' "
            f"technology '{c.get('tech', '')}'.\n"
            "Return ONLY: (1) the root cause in 1-2 sentences, (2) the category "
            "(syntax|dependency|test_failure|compilation|linter_format|infra_secret|"
            "network_timeout|unknown), (3) the exact file(s) and line(s) affected.\n\n"
            "LOGS:\n" + (logs or "(no logs)")
        )
        return self._run(prompt)

    def suggest_fix(self, file_path: str, content: str, analysis: str,
                    context: Optional[Dict] = None) -> str:
        c = context or {}
        prompt = (
            f"File '{file_path}' in repo '{c.get('repo','')}' (tech {c.get('tech','')}) "
            "has a CI failure.\n"
            f"ANALYSIS: {analysis}\n\n"
            "Fix the root cause in this file. Return ONLY the corrected file content "
            "in full, with no explanation, no markdown fences, no diff.\n"
            "CURRENT CONTENT:\n```\n" + (content or "") + "\n```"
        )
        return self._run(prompt)

    def select_repos(self, candidates: List[Dict],
                     context: Optional[Dict] = None) -> List[str]:
        """Use Copilot to choose which candidate repos the agent should monitor."""
        if not candidates:
            return []
        now = datetime.now(timezone.utc)
        payload = []
        for c in candidates:
            pushed = ""
            recent = False
            try:
                pushed_at = str(c.get("pushed_at") or "").strip()
                if pushed_at:
                    pushed_dt = datetime.fromisoformat(
                        pushed_at.replace("Z", "+00:00"))
                    recent = abs((now - pushed_dt).total_seconds()) <= self.RECENT_PUSH_WINDOW_HOURS * 3600
                    pushed = pushed_at
            except (ValueError, TypeError):
                recent = False
            payload.append({
                "full_name": c.get("repo_key") or c.get("name"),
                "name": c.get("name"),
                "labels": c.get("labels", []),
                "pushed_at": pushed,
                "recent_push_within_24h": recent,
                "default_branch": c.get("default_branch", c.get("branch", "main")),
            })
        self_repo = (context or {}).get("self_repo") or ""
        skip_self = (f"Also skip the central agent repo itself "
                     f"({self_repo}) and " ) if self_repo else "Also "
        prompt = (
            "You are the repo-monitoring selector for a CI remediation agent. "
            "The agent automatically fixes failed GitHub Actions runs of the "
            "repos it monitors and opens PRs.\n"
            "SELECT only repos that are active candidates to monitor: a repo "
            "qualifies if it was PUSHED within the last 24 hours (the default "
            "recovery window) OR carries a ci_* label (ci_terraform, ci_python, "
            "ci_java, ci_go, ci_rust, ...).\n"
            "SKIP all other repos (no ci_* label and no push within 24h). "
            + skip_self +
            "archived/dead projects.\n"
            "Reply with ONLY a JSON array of full_names, e.g. "
            '["org/repo-a","org/repo-b"]. No prose, no markdown.\n\n'
            f"CANDIDATES:\n{json.dumps(payload, indent=1)}"
        )
        raw = self._run(prompt)
        return parse_repo_selection(raw)

    def select_run(self, candidates: List["CIEvent"],
                   context: Optional[Dict] = None) -> Optional[str]:
        """Ask Copilot which failed CI run the agent should remediate next.

        Candidates are the current failed runs (one newest per repo). Copilot
        picks ONE, returning its key ("org/repo" or "org/repo#run_id"), or None
        so the caller falls back to the run most recently updated.
        """
        if not candidates:
            return None
        payload = [{
            "repo": c.repo,
            "run_id": c.run_id,
            "workflow": c.workflow_name,
            "job": c.job_name,
            "branch": c.broken_branch,
            "labels": c.labels,
            "updated_at": c.updated_at,
        } for c in candidates]
        prompt = (
            "You are the run-selection step of a CI remediation agent. The "
            "agent auto-fixes ONE failed GitHub Actions run per invocation and "
            "opens exactly one pull request for it.\n"
            "The candidates below are each monitored repo's FIRST failed CI job "
            "(its most recent failing run) — one candidate per repo. All repos "
            "were already checked.\n"
            "SELECT exactly ONE repository's run to remediate now: prefer a "
            "run whose failure is a genuine code problem (syntax, compilation, "
            "broken tests, terraform validation) on a project with a ci_* label. "
            "Avoid already-remediated/duplicate runs and trivial environment "
            "failures.\n"
            "Reply with ONLY a JSON object, e.g. "
            '{"repo": "org/repo", "run_id": 123}. No prose, no markdown.\n\n'
            f"CANDIDATES:\n{json.dumps(payload, indent=1)}"
        )
        raw = self._run(prompt)
        return parse_run_selection(raw)

    def fix_workspace(self, workspace: str, logs: str, analysis: str,
                      context: Optional[Dict] = None) -> Dict[str, str]:
        """Let Copilot repair the broken repository in place.

        Copilot runs inside the cloned workspace (`--allow-all-tools --allow-all-
        paths`) with the failure logs and root-cause analysis; it edits files and
        may run validation itself. Afterwards we read back every changed file via
        `git status --porcelain` to produce {rel_path: new_content}.
        """
        c = context or {}
        prompt = (
            "You are a CI remediation engineer working in the Git repository at "
            "the CURRENT DIRECTORY. A GitHub Actions run failed in this "
            "repository. Diagnose and FIX ALL issues in the code so the project "
            f"builds/validates again (technology: {c.get('tech', 'unknown')}).\n"
            "Instructions:\n"
            "- Edit files directly with your tools. Make minimal, correct changes.\n"
            "- Fix every failing file/issue, not just the first one.\n"
            "- Do NOT run git commands (no commit/branch/PR/diff).\n"
            "- Do NOT modify unrelated files or add new files.\n"
            "- You may run the project's build/validate commands to confirm.\n"
            "ROOT CAUSE ANALYSIS:\n" + (analysis or "(none)")[:2500] + "\n\n"
            "FAILURE LOGS (tail):\n" + (logs or "(none)")[-8000:]
        )
        self._run(prompt, cwd=workspace)
        return self._read_changed_files(workspace)

    @staticmethod
    def _read_changed_files(workspace: str) -> Dict[str, str]:
        """Return {rel_path: content} for files changed by Copilot in place."""
        try:
            out = subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=workspace,
                stderr=subprocess.DEVNULL, text=True, timeout=15)
        except Exception:
            return {}
        changed = {}
        for line in out.splitlines():
            if len(line) < 4:
                continue
            code = line[:2].strip()
            if code.startswith(("D", "R")):
                continue
            path = line[3:].strip()
            if not path:
                continue
            if path.startswith('"'):
                try:
                    path = json.loads(path)
                except Exception:
                    continue
            full = os.path.join(workspace, path)
            if not os.path.isfile(full):
                continue
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as f:
                    changed[path] = f.read()
            except Exception:
                continue
        return changed

    def create_pr_content(self, plan, event, context: Optional[Dict] = None):
        """Draft the PR title + body with Copilot, then return them."""
        c = context or {}
        changed = "\n".join(f"- `{f}`" for f in (plan.files or []))
        prompt = (
            "You are drafting a pull request that fixes a failed CI run. "
            "Write a clear, professional PR title and a markdown body.\n"
            f"Repo: {event.repo}\nBranch: {event.broken_branch}\n"
            f"Run ID: {event.run_id}  Workflow: {event.workflow_name}  "
            f"Job: {event.job_name}\nTechnology: {c.get('tech','')}   "
            f"Category: {c.get('category','')}\n\n"
            f"ROOT CAUSE: {plan.root_cause}\n"
            f"FIX DESCRIPTION: {plan.fix_description}\n"
            f"FILES CHANGED:\n{changed or '(none)'}\n\n"
            "Return ONLY a JSON object of the form:\n"
            '{"title": "Fix: <short summary>", "body": "<markdown body with '
            '## Summary, ## Root Cause, ## Changes, # Validation sections>"}'
        )
        raw = self._run(prompt)
        parsed = parse_pr_content(raw)
        if parsed.get("title"):
            return parsed["title"], parsed.get("body", "")
        return "", {}

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None
