import os
import json
import subprocess
from typing import List, Dict, Optional, Tuple

from core.github_client import GitHubClient
from core.models import CIEvent, ErrorContext, FixResult, FailureCategory
from core.repository_manager import RepositoryManager
from adapters import get_adapter, resolve_tech_from_label, SUPPORTED_TECHS
from adapters.base import FixPlan, ValidationResult
from engine.label_resolver import resolve_primary_technology, resolve_technology_from_labels, get_adapter_for_label
from ai import get_ai_model, load_model_config
from monitoring import get_failure_monitor


class CIOrchestrator:
    """Central CI remediation orchestrator.

    Responsibilities:
    1. Discover child repositories to monitor (from config or env)
    2. Poll for failed CI runs
    3. Identify the latest failed run
    4. Determine affected repo, branch, workflow, job, and tech
    5. Fetch and analyze failed logs
    6. Clone broken branch and isolate workspace
    7. Plan fix using technology-specific adapter
    8. Validate fix
    9. Commit, push, and create PR targeting the broken branch
    """

    def __init__(self, github_token: Optional[str] = None):
        self.token = github_token or os.environ.get("GITHUB_TOKEN") or self._gh_auth_token()
        if self.token:
            os.environ["GITHUB_TOKEN"] = self.token
        self.client = GitHubClient(token=self.token)
        self.repo_manager = RepositoryManager(token=self.token)
        self.config = self._load_config()
        self.ai_model = get_ai_model(load_model_config())
        self.failure_monitor = get_failure_monitor(self.config, self.ai_model)

    @staticmethod
    def _gh_auth_token() -> Optional[str]:
        """Fall back to the token already stored in the gh keyring."""
        try:
            import subprocess
            out = subprocess.check_output(["gh", "auth", "token"],
                                          stderr=subprocess.DEVNULL, text=True)
            t = (out or "").strip()
            return t or None
        except Exception:
            return None

    def _load_config(self) -> dict:
        """Load CI remediation configuration."""
        config_path = "/home/ghost/Documents/copilot/agent/copoilot-central/config/ci_remediation.yaml"
        if os.path.exists(config_path):
            with open(config_path) as f:
                import yaml; return yaml.safe_load(f) or {}
        return {}

    def _org_repos(self) -> List[str]:
        """Resolve the organizations to scan.

        Priority:
        1. Config `organizations` (explicit override, if present).
        2. Otherwise, derive from the GitHub token: the authenticated user's
           org memberships, or the user's own account if they belong to no org.
        """
        orgs = self.config.get("organizations", [])
        if orgs:
            if isinstance(orgs, dict):
                orgs = [orgs]
            result = []
            for org in orgs:
                if isinstance(org, dict):
                    result.append(org.get("name") or "")
                else:
                    result.append(str(org))
            return [o for o in result if o]
        # No org in config -> derive from token
        return self.client.resolve_orgs_from_token()

    def discover_repos(self) -> List[Dict]:
        """Dynamically discover monitored repositories.

        Unless an explicit `repositories` list is configured, this enumerates
        every repo in the configured/derived organizations (code only lists
        them via the API) and then lets the pluggable AI model decide which
        orgs/repos to actually monitor (see _apply_repo_selection). No hardcoded
        name/topic/push-window filters are applied by code.

        Returns a list of dicts: {name, url, branch, labels, repo_key}.
        """
        explicit = self.config.get("repositories") or []
        if isinstance(explicit, dict):
            explicit = [{"name": k, **v} for k, v in explicit.items()]
        if explicit:
            return self._normalize_repos(explicit)

        orgs = self._org_repos()
        default_branch = "main"
        discovered = []
        org_configs = self.config.get("organizations", [])
        if isinstance(org_configs, dict):
            org_configs = [org_configs]
        default_branches = {
            (o.get("name") if isinstance(o, dict) else o): (o.get("branch") if isinstance(o, dict) else None)
            for o in org_configs
        }
        self_repo = "copoilot-central"
        for org in orgs:
            org_repos = self.client.list_org_repos(org)
            default_branch = default_branches.get(org) or default_branch
            for r in org_repos:
                if r.get("name") == self_repo:
                    continue
                seen_key = r.get("full_name") or r.get("name")
                if any(d.get("repo_key") == seen_key for d in discovered):
                    continue
                discovered.append({
                    "name": r.get("name"),
                    "url": r.get("url"),
                    "branch": default_branch,
                    "labels": [t for t in r.get("topics", []) if t.startswith("ci_")],
                    "pushed_at": r.get("pushed_at", ""),
                    "repo_key": seen_key,
                    "default_branch": r.get("default_branch", default_branch),
                })
        print(f"[DISCOVER] enumerated {len(discovered)} repos across {len(orgs)} org(s); "
              "selection delegated to the AI model")
        return self._apply_repo_selection(discovered)

    def _tech_from_run(self, event: CIEvent) -> List[str]:
        """Technologies for a failed run, derived from its own workflow file.

        This is the ci_<tech> 'label' set generated for that run. A single
        workflow can cover several stacks (e.g. ci-golang-terraform.yaml ->
        ["go", "terraform"]). Returns [] when the workflow maps to no supported
        technology, in which case the run is skipped.
        """
        path = (event.workflow_path or "").strip()
        if not path:
            return []
        techs = self._infer_tech_from_workflow(path)
        return [t for t in techs if t in SUPPORTED_TECHS]

    def _tech_from_logs(self, logs: str) -> List[str]:
        """Technologies inferred from the failing run's own logs.

        Fallback when the workflow file name is generic (e.g. ci.yaml). Returns
        every supported technology with a matching signature (most-signalled
        first), or [] when none is found.
        """
        lower = (logs or "").lower()
        sig = {
            "terraform": ["terraform", "hcl", ".tf", "provider", "registry.terraform"],
            "python": ["python", "pytest", "flake8", "syntaxerror", "modulenotfounderror", "traceback", "pip"],
            "java": ["java", "maven", "gradle", "pom.xml", "build.gradle", "junit", "javac", "exception in thread"],
            "go": ["go build", "go test", "golang", "go: "],
            "rust": ["cargo", "rustc", "rust"],
        }
        scored = {t: sum(1 for k in sig.get(t, []) if k in lower) for t in sig}
        return [t for t, n in sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
                if n > 0]

    def _repo_selection_provider(self) -> str:
        """Pluggable repo-selection backend: heuristic | ai.

        Priority: $CI_MONITOR_REPO_SELECTION > config monitoring.repo_selection
        > config monitoring.provider > default ai. With ai the model decides
        which orgs/repos to monitor; code does not filter repos heuristically.
        """
        mon = self.config.get("monitoring") or {}
        return (os.environ.get("CI_MONITOR_REPO_SELECTION")
                or mon.get("repo_selection")
                or mon.get("provider")
                or "ai").lower()

    def _apply_repo_selection(self, candidates: List[Dict]) -> List[Dict]:
        """Narrow the candidate repos to monitor using the pluggable model.

        With provider=ai this asks the configured model (e.g. GitHub Copilot
        CLI) which repos are active dev projects to monitor, then filters the
        candidate list. Falls back to the full list if the model is
        unavailable, returns nothing, or cannot be parsed.
        """
        if not candidates:
            return candidates
        provider = self._repo_selection_provider()
        if provider != "ai":
            return candidates
        try:
            if not self.ai_model.is_available():
                print("[SELECT] model unavailable; keeping heuristic candidates")
                return candidates
            print(f"[SELECT] choosing monitor repos via '{self.ai_model.name}' "
                  f"({len(candidates)} candidates)...")
            names = self.ai_model.select_repos(candidates, {
                "orgs": self._org_repos(),
            })
            if not names:
                print("[SELECT] model returned no selection; keeping all candidates")
                return candidates
            keep = [c for c in candidates
                    if (c.get("repo_key") or c.get("name")) in names
                    or c.get("name") in names]
            if keep:
                print(f"[SELECT] Copilot selected {len(keep)}/{len(candidates)} repos to monitor")
                return keep
            print("[SELECT] selection did not match candidates; keeping all")
        except Exception as e:
            print(f"[SELECT] selection failed ({e}); keeping all candidates")
        return candidates

    def _infer_tech_from_workflow(self, filename: str) -> List[str]:
        """Infer every technology referenced by a workflow file name.

        A single workflow can cover several stacks (ci-golang-terraform.yaml ->
        ["go", "terraform"]). Order follows the mapping list; results are
        de-duplicated.
        """
        low = str(filename).lower()
        mapping = [
            ("terraform", "terraform"), ("hcl", "terraform"),
            ("python", "python"), ("pip", "python"), ("py-", "python"),
            ("java", "java"), ("maven", "java"), ("gradle", "java"), ("spring", "java"),
            ("go", "go"), ("golang", "go"), ("cargo", "rust"), ("rust", "rust"),
        ]
        found: List[str] = []
        for needle, tech in mapping:
            if needle in low and tech not in found:
                found.append(tech)
        return found

    def _normalize_repos(self, repos_data: List[dict]) -> List[Dict]:
        out = []
        for repo_info in repos_data:
            name = repo_info.get("name", repo_info.get("repo_key", "unknown"))
            url = repo_info.get("url", f"https://github.com/{name}")
            branch = repo_info.get("branch")
            labels = repo_info.get("labels", [])
            labels = [l for l in labels if l.startswith("ci_")] or labels
            out.append({
                "name": name,
                "url": url,
                "branch": branch,
                "labels": labels,
                "repo_key": name,
            })
        return out

    def get_latest_failed(self, repos: List[Dict]) -> Optional[CIEvent]:
        """Find the most recent failed CI run across monitored repos."""
        latest = None
        for repo in repos:
            events = self.client.list_recent_failed_runs(repo["url"], limit=10)
            for ev in events:
                if ev.conclusion == "failure":
                    if latest is None or ev.updated_at > latest.updated_at:
                        latest = ev
        return latest

    def get_latest_failed_events(self, repos: List[Dict]) -> List[CIEvent]:
        """Find all recent failed CI runs across monitored repos, newest first."""
        events: List[CIEvent] = []
        for repo in repos:
            for ev in self.client.list_recent_failed_runs(repo["url"], limit=10):
                if ev.conclusion == "failure":
                    events.append(ev)
        events.sort(key=lambda ev: ev.updated_at, reverse=True)
        return events

    def get_failed_job_logs(self, repo: str, run_id: int) -> str:
        """Retrieve logs from a failed workflow run."""
        return self.client.get_failed_job_logs(repo, run_id)

    def get_repo_source_files(self, repo: str, base_or_branch: str) -> Dict[str, str]:
        """Get list of changed files in the broken branch."""
        return self.client.get_changed_files(repo, base_or_branch)

    def create_fix_branch(self, repo: str, broken_branch: str, run_id: int, tech: str) -> Tuple[str, str]:
        """Create a new branch from the broken branch (preserving base)."""
        return self.repo_manager.prepare_workspace(
            repo=repo,
            broken_branch=broken_branch,
            run_id=run_id,
            tech=tech
        )

    def _resolve_tech(self, repo: Dict, logs: str = "") -> str:
        """Pick the technology adapter for a repo.

        When a repo has multiple ci_* labels (e.g. ci_terraform + ci_java), infer
        which technology actually failed from the logs; otherwise fall back to
        label order. The agent is name-agnostic so this never depends on the
        repository name or a `_child` suffix.
        """
        labels = repo.get("labels", [])
        techs = [resolve_tech_from_label(l) for l in labels if l.startswith("ci_")]
        if not techs:
            return "terraform"

        lower = (logs or "").lower()
        if len(techs) > 1 and lower:
            sig = {
                "terraform": ["terraform", "hcl", ".tf", "provider", "registry.terraform"],
                "python": ["python", "pytest", "flake8", "syntaxerror", "modulenotfounderror", "traceback"],
                "java": ["java", "maven", "gradle", "pom.xml", "build.gradle", "junit", "javac", "exception in thread"],
                "go": ["go build", "go test", "golang", "go: "],
                "rust": ["cargo", "rustc", "rust"],
            }
            scored = {t: sum(1 for k in sig.get(t, []) if k in lower) for t in techs}
            best = max(scored.items(), key=lambda kv: kv[1])
            if best[1] > 0:
                return best[0]
        return techs[0]

    def plan_fix(self, event: CIEvent, repo: Dict, logs: str = "", repo_files: Optional[Dict[str, str]] = None) -> FixPlan:
        """Use the technology adapter (inferred from labels/logs) to plan the fix."""
        tech = self._resolve_tech(repo, logs)

        try:
            adapter = get_adapter(tech)
        except KeyError:
            print(f"[PLAN] no adapter for technology '{tech}'; "
                  "returning empty plan (AI engine handles the fix)")
            return FixPlan(tech=tech, root_cause="")
        error_ctx = adapter.analyze_failure(logs, event)
        plan = adapter.plan_fix(error_ctx, repo_files or {})
        plan.tech = tech
        return plan

    def validate_fix(self, workspace: str, plan: FixPlan, tech: str) -> ValidationResult:
        """Run technology-specific validation on the fix branch.

        If no adapter is registered for the technology (e.g. 'unknown'),
        adapter-based validation is skipped so AI-only fixes are not blocked.
        """
        try:
            adapter = get_adapter(tech)
        except KeyError:
            print(f"[VALIDATE] no adapter for technology '{tech}'; "
                  "skipping adapter validation (AI-only fix)")
            return ValidationResult(passed=True,
                                    output="Skipped: no adapter registered",
                                    command="")
        return adapter.validate(workspace, plan)

    def commit_and_push(self, workspace: str, fix_branch: str, commit_msg: str, files: Optional[List[str]] = None) -> bool:
        """Commit changes, stage, and push the fix branch."""
        return self.repo_manager.commit_and_push(workspace, fix_branch, commit_msg, files=files)

    def create_pr(self, repo: Dict, event: CIEvent, run_id: int, fix_branch: str, base: str, commit_msg: str, plan: FixPlan) -> Optional[str]:
        """Create a PR targeting the broken branch.

        The PR title + body are drafted/corrected by the pluggable model
        (e.g. GitHub Copilot CLI) when available; otherwise the adapter builds
        them heuristically.
        """
        try:
            adapter = get_adapter(plan.tech)
        except KeyError:
            adapter = None
        title = f"Auto-fix: CI failure {run_id} in {repo['name']}"
        if adapter is None:
            body = (
                f"## Automated CI Remediation\n\n"
                f"**CI Failure**\n"
                f"- Repository: `{event.repo}`\n"
                f"- Branch: `{event.broken_branch}`\n"
                f"- Run ID: `{event.run_id}`\n"
                f"- Workflow: {event.workflow_name}\n"
                f"- Job: {event.job_name}\n"
                f"- Technology: `{plan.tech}`\n\n"
                f"**Root Cause**\n{plan.root_cause}\n\n"
                f"**Fix Applied**\n{plan.fix_description or plan.root_cause}\n\n"
                f"**Files Changed**\n"
                + "\n".join(f"- `{f}`" for f in plan.files)
                + f"\n\n---\n*This PR was automatically generated by the CI Remediation Agent.*"
            )
        else:
            body = adapter.build_pr_body(event, None, plan)
        try:
            if self.ai_model.is_available():
                print(f"[PR] drafting PR content with '{self.ai_model.name}'...")
                ai_title, ai_body = self.ai_model.create_pr_content(
                    plan, event, {
                        "repo": event.repo,
                        "branch": event.broken_branch,
                        "tech": plan.tech,
                        "category": getattr(plan, "error_category", ""),
                    })
                if ai_title:
                    title = ai_title
                if ai_body:
                    body = ai_body
            else:
                print("[PR] model unavailable; using adapter-built PR content")
        except Exception as e:
            print(f"[PR] AI drafting failed; using adapter PR content: {e}")
        pr_url = self.client.create_pull_request(
            repo=repo["url"],
            base=base,
            head=fix_branch,
            title=title,
            body=body,
            draft=False,
            labels=["review"]
        )
        return pr_url

    def _fix_engine(self) -> str:
        """Pluggable fix engine: ai | adapter.

        Priority: $CI_FIX_ENGINE > config fixing.engine > default 'ai'.
        With 'adapter' the legacy heuristic adapters generate the changes.
        """
        fixing = self.config.get("fixing") or {}
        return (os.environ.get("CI_FIX_ENGINE")
                or fixing.get("engine")
                or "ai").lower()

    def _ai_fix_workspace(self, workspace: str, logs: str, analysis: str,
                          repo: Dict, event: CIEvent, techs: List[str]) -> FixPlan:
        """AI-only fix: let the pluggable model repair the workspace in place.

        No adapter heuristics are involved — the model handles syntax and any
        other failure category. Validation commands still come from the adapter
        (they are just the project's build/test commands, not a fixer).
        techs is the list of technologies derived from the run's own labels
        (e.g. ["go", "terraform"] for a golang+terraform workflow).
        """
        context = {
            "repo": event.repo,
            "branch": event.broken_branch,
            "workflow": event.workflow_name,
            "job": event.job_name,
            "tech": ", ".join(techs) if techs else "",
        }
        tech = techs[0] if techs else ""
        changed = self.ai_model.fix_workspace(workspace, logs, analysis, context)
        if not changed:
            print("[FIX:ai] model made no changes")
            return FixPlan(tech=tech, root_cause=analysis or "")
        try:
            adapter = get_adapter(tech) if self._has_adapter(tech) else None
            validation_commands = (adapter.get_default_validation_commands()
                                   if adapter else [])
        except Exception:
            validation_commands = []
        return FixPlan(
            files=list(changed.keys()),
            changes=changed,
            validation_commands=validation_commands,
            root_cause=analysis or "AI-driven remediation",
            tech=tech,
            fix_description=f"AI-generated fix via model '{self.ai_model.name}'",
        )

    def _has_adapter(self, tech: str) -> bool:
        try:
            from adapters import SUPPORTED_TECHS
            return tech in SUPPORTED_TECHS
        except Exception:
            return True

    def _ai_analyze(self, logs: str, repo: Dict, event: CIEvent, tech: str) -> Optional[str]:
        """Run the pluggable AI model to analyze the failure logs.

        Returns a root-cause analysis string, or None if the model is
        unavailable/offline so remediation can fall back to heuristics.
        """
        try:
            if not self.ai_model.is_available():
                print(f"[AI] model '{self.ai_model.name}' unavailable; using heuristics")
                return None
            print(f"[AI] analyzing logs with '{self.ai_model.name}'...")
            analysis = self.ai_model.analyze_logs(logs, {
                "repo": event.repo,
                "branch": event.broken_branch,
                "workflow": event.workflow_name,
                "job": event.job_name,
                "tech": tech,
            })
            return analysis
        except Exception as e:
            print(f"[AI] analysis failed, falling back to heuristics: {e}")
            return None

    def _ai_suggest_fix(self, plan: FixPlan, repo: Dict, event: CIEvent, tech: str,
                        repo_files: Dict[str, str]) -> FixPlan:
        """Use the pluggable AI model to refine fix content for each file."""
        if not self.ai_model.is_available():
            return plan
        try:
            for file_path in list(plan.files):
                if file_path not in repo_files:
                    continue
                print(f"[AI] generating fix for {file_path}...")
                suggestion = self.ai_model.suggest_fix(
                    file_path, repo_files[file_path], plan.root_cause,
                    {"repo": event.repo, "branch": event.broken_branch, "tech": tech},
                )
                if suggestion and suggestion.strip():
                    plan.changes[file_path] = suggestion.strip()
        except Exception as e:
            print(f"[AI] fix generation failed, keeping heuristic changes: {e}")
        return plan

    def _fix_single_run(self, latest: CIEvent, repos: List[Dict]) -> bool:
        """Remediate a single failed CI run. Returns True on success."""
        print(f"\nProcessing failed run: {latest}")
        run_id = latest.run_id

        def match_repo(r, latest_repo):
            r_name = r.get("name", "")
            l_name = latest_repo.split("/")[-1] if "/" in latest_repo else latest_repo
            return r_name == l_name or r_name in latest_repo or latest_repo in r.get("url", "")
        repo = next((r for r in repos if match_repo(r, latest.repo)), None)
        if not repo:
            print(f"No matching repo config for {latest.repo}")
            return False

        broken_branch = latest.broken_branch or repo.get("branch") or "main"

        if self.client.has_open_fix_pr(repo["url"], broken_branch):
            print(f"[SKIP] {repo['name']} already has an open ai-fix PR targeting "
                  f"{broken_branch}; skipping until the broken branch changes")
            return True

        logs = self.client.get_failed_job_logs(repo["url"], run_id)
        print(f"Logs length: {len(logs)}")

        techs = self._tech_from_run(latest)
        if not techs:
            techs = self._tech_from_logs(logs)
        if not techs:
            print(f"[SKIP] run {run_id} in {repo['name']}: no ci_<tech> label for "
                  f"this run (workflow '{latest.workflow_path or latest.workflow_name}' "
                  f"and logs do not resolve to a supported technology)")
            return True
        print(f"Resolved technologies: {techs} "
              f"(labels ci_{'/ci_'.join(techs)} generated by run)")
        tech = techs[0]
        techs_str = ", ".join(techs)

        monitor_analysis = self.failure_monitor.analyze(latest, logs, repo, tech)
        print(f"[MONITOR:{monitor_analysis.source}] category={monitor_analysis.category.value} "
              f"confidence={monitor_analysis.confidence}")
        if monitor_analysis.root_cause:
            print(f"[MONITOR] root cause: {monitor_analysis.root_cause[:300]}")

        ai_analysis = self._ai_analyze(logs, repo, latest, techs_str)
        if ai_analysis:
            print(f"[AI] Root cause: {ai_analysis[:300]}")
        fix_analysis = monitor_analysis.root_cause or ai_analysis or "Unknown CI failure"

        print("Creating fix branch...")
        workspace, fix_branch = self.create_fix_branch(repo["url"], broken_branch, run_id, tech)
        print(f"Fix branch: {fix_branch}")

        print("Generating fix...")
        fix_engine = self._fix_engine()
        ai_ready = (fix_engine == "ai" and self.ai_model.is_available())
        if ai_ready:
            print(f"[FIX] AI engine active ({self.ai_model.name}) — "
                  "fixing all issues without adapters")
            plan = self._ai_fix_workspace(workspace, logs, fix_analysis, repo,
                                          latest, techs)
        else:
            if fix_engine != "ai":
                print(f"[FIX] adapter engine configured ({fix_engine}); "
                      "using heuristic adapters")
            else:
                print(f"[FIX] model '{self.ai_model.name}' unavailable; "
                      "falling back to heuristic adapters")
            repo_files = {}
            for root, dirs, files in os.walk(workspace):
                if ".git" in dirs:
                    dirs.remove(".git")
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, workspace)
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            repo_files[rel_path] = f.read()
                    except Exception:
                        pass
            plan = self.plan_fix(latest, repo, logs=logs, repo_files=repo_files)
            if not plan.root_cause and monitor_analysis.root_cause:
                plan.root_cause = monitor_analysis.root_cause
            if plan.files:
                plan = self._ai_suggest_fix(plan, repo, latest, tech, repo_files)

        print(f"Root cause: {plan.root_cause}")
        print(f"Files to change: {plan.files}")

        for file_path, new_content in plan.changes.items():
            dest_path = os.path.join(workspace, file_path)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "w", encoding="utf-8") as f:
                f.write(new_content)

        print("Validating fix...")
        validations = []
        for t in dict.fromkeys(techs):
            validation = self.validate_fix(workspace, plan, t)
            validations.append((t, validation))
            print(f"[VALIDATE] {t}: {'passed' if validation.passed else 'FAILED'}")
            if not validation.passed:
                print(f"  {validation.output}")
        if any(not v.passed for _, v in validations):
            print("Validation failed; fix branch not pushed")
            return False
        print("Validation passed!")

        print("Committing and pushing...")
        commit_msg = f"Fix CI failure {run_id} in {repo['name']}"
        if not self.commit_and_push(workspace, fix_branch, commit_msg, files=plan.files):
            print("Failed to commit/push fix branch")
            return False

        print("Creating PR...")
        pr_url = self.create_pr(repo, latest, run_id, fix_branch, broken_branch, commit_msg, plan)
        if pr_url:
            print(f"PR created: {pr_url}")
        else:
            print("Could not create PR")
        return True

    def run_full_remediation(self, repos: List[Dict]) -> bool:
        """Main remediation workflow — iterates all repos with failed runs."""
        failed_events = self.get_latest_failed_events(repos)
        if not failed_events:
            print("No failed CI runs found.")
            print("Tip: ensure GITHUB_TOKEN is set and valid, and that the child"
                  " repositories are accessible to the token. Repositories are"
                  " discovered from config/ci_remediation.yaml and may use any"
                  " name (ci_* labels determine the technology, not the repo name).")
            return False

        print(f"Found {len(failed_events)} failed run(s) across {len(repos)} repo(s)")
        any_success = False
        for event in failed_events:
            try:
                success = self._fix_single_run(event, repos)
                if success:
                    any_success = True
            except Exception as e:
                print(f"[ERROR] Failed to remediate run {event.run_id}: {e}")
        if not any_success:
            print("No failed runs were successfully remediated.")
        return any_success
