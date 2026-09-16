# core/github_client.py
import json
import os
import subprocess
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
from .models import CIEvent


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
            if "HTTPError 404" in str(e):
                print(f"[INFO] Repository {repo} has no failed runs or is not accessible (404)")
            else:
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
                    updated_at=run.get("updated_at", ""),
                    workflow_path=run.get("path", ""),
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

    def get_authenticated_user(self) -> Optional[str]:
        """Return the login of the token's authenticated user."""
        try:
            data = self._api_request("user")
            return data.get("login")
        except Exception:
            return None

    def list_user_orgs(self) -> List[str]:
        """Return organizations the authenticated user belongs to."""
        orgs = []
        page = 1
        while True:
            try:
                data = self._api_request(f"user/memberships/orgs?per_page=100&page={page}",
                                         headers={"Accept": "application/vnd.github.v3+json"})
            except Exception as e:
                print(f"[WARN] Failed to list user orgs: {e}")
                break
            if not isinstance(data, list) or not data:
                break
            for m in data:
                orgs.append(m.get("organization", {}).get("login") or m.get("login") or "")
            if len(data) < 100:
                break
            page += 1
        return [o for o in orgs if o]

    def resolve_orgs_from_token(self) -> List[str]:
        """Resolve which orgs to scan from the token.

        Prefers the authenticated user's org memberships; if the user has none,
        falls back to the user's own account (so user-owned repos are scanned).
        """
        user = self.get_authenticated_user()
        orgs = self.list_user_orgs()
        if not orgs and user:
            orgs = [user]
        return orgs

    def list_org_repos(self, org: str) -> List[Dict]:
        """List all repositories belonging to an organization."""
        org = org.strip("/").split("/")[-1]
        repos = []
        page = 1
        while True:
            endpoint = f"orgs/{org}/repos?per_page=100&page={page}"
            try:
                data = self._api_request(endpoint)
            except Exception as e:
                print(f"[WARN] Failed to list org repos for {org}: {e}")
                break
            if not isinstance(data, list) or not data:
                break
            for r in data:
                repos.append({
                    "name": r.get("name", ""),
                    "full_name": r.get("full_name", ""),
                    "url": r.get("html_url") or f"https://github.com/{r.get('full_name','')}",
                    "default_branch": r.get("default_branch", "main"),
                    "topics": r.get("topics", []),
                    "pushed_at": r.get("pushed_at", ""),
                })
            if len(data) < 100:
                break
            page += 1
        # The org listing often omits `topics`; fetch missing ones in parallel
        # instead of one sequential API call per repo.
        pending = [r for r in repos if not r.get("topics")]
        if pending:
            names = [r.get("full_name") or r.get("name", "") for r in pending]
            with ThreadPoolExecutor(max_workers=min(12, len(pending) or 1)) as pool:
                fetched = list(pool.map(self.get_repo_topics, names))
            for r, topics in zip(pending, fetched):
                r["topics"] = topics
        return repos

    def get_repo_topics(self, repo: str) -> List[str]:
        """Authoritative GitHub topics for a repository.

        The org-level repo listing does not always include the `topics` field
        (or returns it empty), so fetch them from the per-repo topics endpoint
        when a repo shows no topics.
        """
        repo_clean = repo.replace("https://github.com/", "").strip("/")
        try:
            data = self._api_request(
                f"repos/{repo_clean}/topics",
                headers={"Accept": "application/vnd.github.mercy-preview+json"},
            )
            return [str(t) for t in (data.get("names") or [])]
        except Exception as e:
            print(f"[WARN] Failed to list topics for {repo_clean}: {e}")
            return []

    def has_open_fix_pr(self, repo: str, base: str) -> bool:
        """Return True if an open PR already targets `base` from an ai-fix branch.

        Lets the agent skip repos that were already remediated so it does not
        re-fix the same failed run on every poll.
        """
        repo_clean = repo.replace("https://github.com/", "").strip("/")
        try:
            data = self._api_request(f"repos/{repo_clean}/pulls?state=open&base={base}")
            return any(
                str(p.get("head", {}).get("ref", "")).startswith("ai-fix/")
                for p in data
            )
        except Exception as e:
            print(f"[WARN] Failed to list open PRs for {repo_clean} ({base}): {e}")
            return False

    def list_open_fix_pr_bases(self, repo: str) -> set:
        """Return base refs that already have an open ai-fix/ PR.

        One API call per repo instead of one per failed run; used when scanning
        several failed runs of the same repo.
        """
        repo_clean = repo.replace("https://github.com/", "").strip("/")
        try:
            data = self._api_request(
                f"repos/{repo_clean}/pulls?state=open&per_page=100")
            return {str(p.get("base", {}).get("ref", ""))
                    for p in data
                    if str(p.get("head", {}).get("ref", ""))
                    .startswith("ai-fix/")}
        except Exception as e:
            print(f"[WARN] Failed to list open PRs for {repo_clean}: {e}")
            return set()

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
            if "already exists" in str(e).lower():
                try:
                    pulls = self._api_request(f"repos/{repo_clean}/pulls?state=open")
                    for p in pulls:
                        if p.get("head", {}).get("ref") == head:
                            return p.get("html_url")
                except Exception:
                    pass
            print(f"[ERROR] Failed to create PR for {repo_clean} ({head} -> {base}): {e}")
            return None