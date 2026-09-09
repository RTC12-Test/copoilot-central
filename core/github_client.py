# core/github_client.py
import json
import os
import subprocess
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional
from .models import CIEvent, ErrorContext


class GitHubClient:
    """GitHub API and CLI interface for discovering runs, fetching logs, and PR management."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("GITHUB_TOKEN", "")

    def _api_request(self, endpoint: str, method: str = "GET", data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Any:
        url = endpoint if endpoint.startswith("http") else f"https://api.github.com/{endpoint.lstrip('/')}"
        req_headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Autonomous-CI-Remediation-Agent"
        }
        if self.token:
            req_headers["Authorization"] = f"Bearer {self.token}"
        if headers:
            req_headers.update(headers)

        req_data = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status == 204:
                    return {}
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else ""
            raise RuntimeError(f"GitHub API HTTPError {e.code} for {url}: {err_body}")
        except Exception as e:
            raise RuntimeError(f"GitHub API Error for {url}: {str(e)}")

    def list_recent_failed_runs(self, repo: str, branch: Optional[str] = None, limit: int = 10) -> List[CIEvent]:
        """Fetch failed workflow runs for a repository."""
        repo_clean = repo.replace("https://github.com/", "").strip("/")
        endpoint = f"repos/{repo_clean}/actions/runs?status=failure&per_page={limit}"
        if branch:
            endpoint += f"&branch={branch}"

        try:
            data = self._api_request(endpoint)
        except Exception as e:
            print(f"[WARN] Failed to fetch workflow runs for {repo}: {e}")
            return []

        events = []
        for run in data.get("workflow_runs", []):
            if run.get("conclusion") == "failure":
                events.append(CIEvent(
                    repo=repo_clean,
                    run_id=run.get("id"),
                    workflow_name=run.get("name", "Unknown Workflow"),
                    job_name=run.get("display_title", "Unknown Job"),
                    broken_branch=run.get("head_branch", "main"),
                    head_sha=run.get("head_sha", ""),
                    html_url=run.get("html_url", ""),
                    updated_at=run.get("updated_at", "")
                ))
        return events

    def get_failed_job_logs(self, repo: str, run_id: int) -> str:
        """Fetch logs from failed workflow jobs using API or gh CLI fallback."""
        repo_clean = repo.replace("https://github.com/", "").strip("/")

        # Try `gh` cli if installed
        try:
            result = subprocess.run(
                ["gh", "run", "view", str(run_id), "--repo", repo_clean, "--log-failed"],
                capture_output=True,
                text=True,
                timeout=20
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        except Exception:
            pass

        # Fallback to GitHub REST API jobs endpoint
        try:
            jobs_data = self._api_request(f"repos/{repo_clean}/actions/runs/{run_id}/jobs")
            failed_job_ids = [
                j["id"] for j in jobs_data.get("jobs", [])
                if j.get("conclusion") == "failure"
            ]
            logs = []
            for jid in failed_job_ids:
                try:
                    # Job log endpoint returns 302 redirect to plaintext log
                    log_url = f"https://api.github.com/repos/{repo_clean}/actions/jobs/{jid}/logs"
                    req = urllib.request.Request(log_url, headers={"Authorization": f"Bearer {self.token}"} if self.token else {})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        logs.append(resp.read().decode("utf-8", errors="ignore"))
                except Exception as ex:
                    logs.append(f"[Job {jid} Log fetch error: {ex}]")
            return "\n".join(logs)
        except Exception as e:
            return f"[Error fetching logs via REST API: {e}]"

    def get_repo_labels(self, repo: str, pull_number: Optional[int] = None) -> List[str]:
        """Fetch PR or repository topic/issue labels."""
        repo_clean = repo.replace("https://github.com/", "").strip("/")
        labels = []
        if pull_number:
            try:
                pr_data = self._api_request(f"repos/{repo_clean}/pulls/{pull_number}", headers={"Accept": "application/vnd.github.v3+json"})
                labels.extend([l["name"] for l in pr_data.get("labels", [])])
            except Exception:
                pass
        try:
            repo_info = self._api_request(f"repos/{repo_clean}/topics", headers={"Accept": "application/vnd.github.mercy-preview+json"})
            labels.extend(repo_info.get("names", []))
        except Exception:
            pass
        return list(set(labels))

    def get_changed_files(self, repo: str, base_or_branch: str) -> List[str]:
        """Get changed files on branch/PR."""
        repo_clean = repo.replace("https://github.com/", "").strip("/")
        try:
            compare_data = self._api_request(f"repos/{repo_clean}/compare/main...{base_or_branch}")
            return [f["filename"] for f in compare_data.get("files", [])]
        except Exception:
            return []

    def create_pull_request(
        self,
        repo: str,
        base: str,
        head: str,
        title: str,
        body: str,
        draft: bool = False,
        labels: Optional[List[str]] = None
    ) -> Optional[str]:
        """Create a Pull Request targeting base from head."""
        repo_clean = repo.replace("https://github.com/", "").strip("/")
        payload = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft
        }
        try:
            resp = self._api_request(f"repos/{repo_clean}/pulls", method="POST", data=payload)
            pr_url = resp.get("html_url")
            pr_number = resp.get("number")

            if pr_number and labels:
                try:
                    self._api_request(
                        f"repos/{repo_clean}/issues/{pr_number}/labels",
                        method="POST",
                        data={"labels": labels}
                    )
                except Exception as le:
                    print(f"[WARN] Failed to apply labels {labels} to PR #{pr_number}: {le}")

            return pr_url
        except Exception as e:
            print(f"[ERROR] Failed to create PR for {repo_clean} ({head} -> {base}): {e}")
            return None

    def post_workflow_diagnostic_comment(self, event: CIEvent, error_ctx: ErrorContext):
        """Post a diagnostic summary when an issue cannot be safely auto-remediated."""
        print(f"[DIAGNOSTIC] Repo: {event.repo} | Branch: {event.broken_branch} | Tech: {error_ctx.tech}")
        print(f"[DIAGNOSTIC] Category: {error_ctx.category.value} | Summary: {error_ctx.error_summary}")