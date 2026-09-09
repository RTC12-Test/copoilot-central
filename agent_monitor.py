#!/usr/bin/env python3
"""Legacy AgentMonitor — kept for reference. Use main.py for current implementation."""
import os, sys, json, re, time, urllib.request

class AgentMonitor:
    LABEL_PATTERN = re.compile(r"^ci_(.+)$")

    def derive_repo(self, label):
        if not self.LABEL_PATTERN.search(label): return None
        return f"RTC12-Test/{self.LABEL_PATTERN.search(label).group(1)}"

    def check_failed_ci_jobs_1hr(self, repo_name, branch=None):
        token = os.environ.get("GITHUB_TOKEN","")
        url = f"https://api.github.com/repos/RTC12-Test/{repo_name}/actions/runs?branch={branch or 'feature/tas'}&status=failure&per_page=10"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {}, method="GET")
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.load(resp)
            failed = []
            for r in data.get("workflow_runs", []):
                if r.get("conclusion") == "failure":
                    failed.append({"repo": repo_name, "branch": branch or "feature/tas", "run_id": r.get("id"), "updated_at": r.get("updated_at")})
            return failed[:5]
        except Exception as e:
            return [{"repo": repo_name, "branch": branch or "feature/tas", "error": str(e)}]

    def get_latest_failed_per_repo(self, repos):
        result = {}
        for repo in repos:
            if isinstance(repo, dict): repo = repo.get("repo")
            if not repo: continue
            label = f"ci_{repo.split('/')[-1]}"
            if not self.LABEL_PATTERN.search(label): continue
            failed = self.check_failed_ci_jobs_1hr(repo, "feature/tas")
            valid = [f for f in failed if isinstance(f, dict) and "error" not in f]
            if valid:
                latest = max(valid, key=lambda x: x.get("updated_at", ""))
                result[repo] = latest
        return result

    def run_auto_pr(self, repos=None):
        if not repos: repos = ["terraform_child"]
        result = self.get_latest_failed_per_repo(repos)
        return [{"repo": repo, "latest_job": latest.get("run_id"), "branch": latest.get("branch")} for repo, latest in result.items()]