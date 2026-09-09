from typing import List, Dict, Optional, Tuple
from datetime import datetime

from core.github_client import GitHubClient
from core.models import CIEvent, ErrorContext, FixResult, FailureCategory
from adapters import get_adapter, resolve_tech_from_label, resolve_primary_technology
from engine.label_resolver import resolve_technology_from_labels, get_adapter_for_label


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
        self.client = GitHubClient(token=github_token)
        self.config = self._load_config()

    def _load_config(self) -> dict:
        """Load CI remediation configuration."""
        config_path = "/home/ghost/Documents/copilot/agent/copoilot-central/config/ci_remediation.yaml"
        if os.path.exists(config_path):
            with open(config_path) as f:
                return dict(json.load(f))
        return {}

    def discover_repos(self) -> List[Dict]:
        """Discover child repositories to monitor.

        Expected format in config:
        repositories:
          - name: terraform_child
            url: https://github.com/org/terraform_child
            branch: feature/xyz
            labels: [ci_terraform]
        """
        repos = []
        # Simple discovery: look for repos matching known patterns
        # In production, this would come from a config file or external source
        for repo_key, repo_info in self.config.get("repositories", []).items():
            name = repo_info.get("name", repo_key)
            url = repo_info.get("url", f"https://github.com/{name}")
            # Extract branch if specified
            branch = repo_info.get("branch")
            # Collect labels
            labels = repo_info.get("labels", [])
            repos.append({
                "name": name,
                "url": url,
                "branch": branch,
                "labels": labels,
                "repo_key": name
            })
        return repos

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

    def get_failed_job_logs(self, repo: str, run_id: int) -> str:
        """Retrieve logs from a failed workflow run."""
        return self.client.get_failed_job_logs(repo, run_id)

    def get_repo_source_files(self, repo: str, base_or_branch: str) -> Dict[str, str]:
        """Get list of changed files in the broken branch."""
        return self.client.get_changed_files(repo, base_or_branch)

    def create_fix_branch(self, repo: str, run_id: int, tech: str) -> str:
        """Create a new branch from the broken branch (preserving base)."""
        # Derive branch name: ai-fix/<tech>-ci-failure-<run_id>
        branch_name = f"ai-fix/{tech}-ci-failure-{run_id}"
        workspace = f"/tmp/copoilot-central/workspaces/{repo}/{run_id}"
        os.makedirs(workspace, exist_ok=True)
        # Clone broken branch
        subprocess.run(
            ["git", "clone", "--depth", "50", "--branch", repo, workspace],
            capture_output=True, text=True, check=True
        )
        # Create new branch from broken branch
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=workspace, capture_output=True, text=True, check=True
        )
        return branch_name

    def plan_fix(self, event: CIEvent, repo: Dict) -> FixPlan:
        """Use the primary technology adapter to plan the fix."""
        # Determine tech from labels
        labels = repo.get("labels", [])
        tech = resolve_primary_technology(labels)
        if not tech:
            # Fallback: use first label that looks like ci_*
            for lbl in labels:
                if lbl.startswith("ci_"):
                    tech = resolve_tech_from_label(lbl)
                    break

        adapter = get_adapter_for_label(tech)
        error_ctx = adapter.analyze_failure(event.logs, event)
        plan = adapter.plan_fix(error_ctx, repo.get("get_changed_files", lambda: {}))
        return plan

    def validate_fix(self, workspace: str, plan: FixPlan) -> ValidationResult:
        """Run technology-specific validation on the fix branch."""
        # Copy workspace to temp location for validation
        import shutil
        temp_workspace = f"/tmp/copoilot-central/workspaces/{workspace}_temp"
        shutil.copytree(workspace, temp_workspace)
        return self.client.validate(temp_workspace, plan)

    def commit_and_push(self, workspace: str, fix_branch: str, commit_msg: str) -> bool:
        """Commit changes, stage, and push the fix branch."""
        import subprocess
        # Configure git
        subprocess.run(["git", "config", "user.name", "aravind15b"], cwd=workspace, check=True)
        subprocess.run(["git", "config", "user.email", "aravind15b@test.com"], cwd=workspace, check=True)
        # Stage all changes
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
        # Commit
        result = subprocess.run(["git", "commit", "-m", commit_msg], cwd=workspace, capture_output=True, text=True)
        if result.returncode != 0:
            return False
        # Push force (since we created a new branch)
        push_result = subprocess.run(
            ["git", "push", "origin", fix_branch, "--force"],
            cwd=workspace, capture_output=True, text=True
        )
        return push_result.returncode == 0

    def create_pr(self, repo: Dict, run_id: int, fix_branch: str, base: str, commit_msg: str) -> Optional[str]:
        """Create a PR targeting the broken branch."""
        # Ensure base branch exists
        base_ref = f"{repo['url']}/main"
        # Create PR
        pr_url = self.client.create_pull_request(
            repo=repo["url"],
            base=base_ref,
            head=fix_branch,
            title=f"Auto-fix: CI failure {run_id} in {repo['name']}",
            body=self._pr_body(event, run_id, fix_branch, commit_msg),
            draft=False
        )
        return pr_url

    def _pr_body(self, event: CIEvent, run_id: int, fix_branch: str, commit_msg: str) -> str:
        """Generate PR description."""
        from adapters.base import FixPlan
        return FixPlan.build_pr_body(event, None, fix_branch)

    def run_full_remediation(self, repos: List[Dict]) -> bool:
        """Main remediation workflow."""
        # Step 1: Find latest failed run
        latest = self.get_latest_failed(repos)
        if not latest:
            print("No failed CI runs found.")
            return False

        print(f"Latest failed run: {latest}")
        run_id = latest.run_id
        repo = next(r for r in repos if r["url"] == latest.repo)
        base_branch = repo.get("branch")

        # Step 2: Get logs
        logs = self.client.get_failed_job_logs(repo["url"], run_id)
        print(f"Logs length: {len(logs)}")

        # Step 3: Plan fix
        print("Planning fix...")
        plan = self.plan_fix(latest, repo)
        print(f"Root cause: {plan.root_cause}")
        print(f"Files to change: {plan.files}")

        # Step 4: Create fix branch
        print("Creating fix branch...")
        fix_branch = self.create_fix_branch(repo["url"], run_id, plan.tech)
        print(f"Fix branch: {fix_branch}")

        # Step 5: Validate fix
        print("Validating fix...")
        validation = self.validate_fix(fix_branch, plan)
        if not validation.passed:
            print(f"Validation failed: {validation.error_message}")
            return False

        # Step 6: Commit and push
        print("Committing and pushing...")
        commit_msg = f"Fix CI failure {run_id} in {repo['name']}"
        if not self.commit_and_push(fix_branch, fix_branch, commit_msg):
            print("Failed to commit/push fix branch")
            return False

        # Step 7: Create PR
        print("Creating PR...")
        pr_url = self.create_pr(repo, run_id, fix_branch, base_branch, commit_msg)
        if pr_url:
            print(f"PR created: {pr_url}")
        else:
            print("Could not create PR")
        return True
