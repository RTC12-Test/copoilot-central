import os
import shutil
import subprocess
from typing import Tuple, List, Optional


class RepositoryManager:
    """Manages local git operations, cloning, branch isolation, and commits."""

    def __init__(self, base_workspace: str = "/tmp/remediation_workspaces"):
        self.base_workspace = os.path.abspath(base_workspace)
        os.makedirs(self.base_workspace, exist_ok=True)

    def prepare_workspace(
        self,
        repo: str,
        broken_branch: str,
        run_id: int,
        tech: str,
        branch_prefix: str = "ai-fix"
    ) -> Tuple[str, str]:
        """
        Creates an isolated workspace for the failing repository.
        Checks out the broken_branch, then creates a new fix branch from it:
        f"{branch_prefix}/{tech}-ci-failure-{run_id}"
        """
        repo_clean = repo.replace("https://github.com/", "").strip("/")
        repo_short = repo_clean.split("/")[-1]
        workspace = os.path.join(self.base_workspace, f"{repo_short}_{run_id}")

        if os.path.exists(workspace):
            shutil.rmtree(workspace, ignore_errors=True)

        token = os.environ.get("GITHUB_TOKEN", "")
        if token:
            clone_url = f"https://x-access-token:{token}@github.com/{repo_clean}.git"
        else:
            clone_url = f"https://github.com/{repo_clean}.git"

        # Clone broken branch
        clone_cmd = [
            "git", "clone",
            "--depth", "50",
            "--branch", broken_branch,
            clone_url,
            workspace
        ]
        res = subprocess.run(clone_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # Fallback to cloning default and checking out broken branch
            fallback_clone = ["git", "clone", "--depth", "50", clone_url, workspace]
            subprocess.run(fallback_clone, capture_output=True, text=True, check=True)
            subprocess.run(["git", "checkout", broken_branch], cwd=workspace, capture_output=True, text=True, check=True)

        fix_branch = f"{branch_prefix}/{tech}-ci-failure-{run_id}"
        # Create new branch targeting the exact broken branch commit
        subprocess.run(["git", "checkout", "-b", fix_branch], cwd=workspace, capture_output=True, text=True, check=True)

        return workspace, fix_branch

    def commit_and_push(
        self,
        workspace: str,
        fix_branch: str,
        commit_message: str,
        author_name: str = "aravind15b",
        author_email: str = "aravind15b@test.com",
        files: Optional[List[str]] = None
    ) -> bool:
        """Configures commit authorship, stages changes, and pushes the new branch."""
        try:
            subprocess.run(["git", "config", "user.name", author_name], cwd=workspace, check=True)
            subprocess.run(["git", "config", "user.email", author_email], cwd=workspace, check=True)

            if files:
                for f in files:
                    subprocess.run(["git", "add", f], cwd=workspace, check=True)
            else:
                subprocess.run(["git", "add", "."], cwd=workspace, check=True)

            # Check if there are staged changes
            status = subprocess.run(["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True)
            if not status.stdout.strip():
                print(f"[INFO] No changes to commit in {workspace}")
                return False

            subprocess.run(["git", "commit", "-m", commit_message], cwd=workspace, check=True)
            push_res = subprocess.run(
                ["git", "push", "origin", fix_branch, "--force"],
                cwd=workspace,
                capture_output=True,
                text=True
            )
            if push_res.returncode != 0:
                raise RuntimeError(f"Git push failed: {push_res.stderr}")
            return True
        except Exception as e:
            print(f"[ERROR] commit_and_push failed: {e}")
            raise

    def cleanup_workspace(self, workspace: str):
        """Removes temporary workspace folder."""
        if os.path.exists(workspace):
            shutil.rmtree(workspace, ignore_errors=True)
