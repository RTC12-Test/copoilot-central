#!/usr/bin/env python3
import os, sys, re, json, random, string, time, urllib.request
class AgentMonitor:
    def __init__(self):
        self.scanned_repos=[]; self.pr_memory={}; self.broken_memory={}
    def derive_repo(self, label):
        if not re.search(r"^ci_[a-zA-Z0-9_-]+$", label): return None
        return f"RTC12-Test/{re.sub(r'^ci_', '', label)}"
    def run_auto_pr(self, repos=None):
        if not repos:
            repos = self.check_ci_pushed_repos_1hr("RTC12-Test")
        repo_map = self.get_latest_failed_per_repo([r.get("repo") if isinstance(r, dict) else r for r in repos])
        results = []
        for repo, latest in repo_map.items():
            # Target broken branch from latest failure
            broken = latest.get("branch") or "feature/tas"
            pr = self.create_fix_pr_on_broken_branch(repo, broken, [])
            results.append({"repo": repo, "pr": pr, "latest_job": latest.get("run_id")})
        return results
    def get_latest_failed_per_repo(self, repos):
        result = {}
        for repo in repos:
            repo_name = repo
            label = f"ci_{repo.split('/')[-1]}"
            derived = self.derive_repo(label)
            if not derived: continue
            failed = self.check_failed_ci_jobs_1hr(repo_name, "feature/tas")
            valid = [f for f in failed if isinstance(f, dict) and "error" not in f]
            if valid:
                latest = max(valid, key=lambda x: x.get("updated_at", ""))
                result[repo_name] = latest
        return result

    def check_failed_ci_jobs_1hr(self, repo_name, branch=None):
        token = os.environ.get("GITHUB_TOKEN", "")
        url = f"https://api.github.com/repos/RTC12-Test/{repo_name}/actions/runs?branch={branch}&status=failure&per_page=10"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                runs = json.load(resp).get("workflow_runs", [])
            failed = []
            for r in runs:
                if r.get("conclusion") == "failure" or (r.get("status") == "completed" and r.get("conclusion") == "failure"):
                    failed.append({"repo": repo_name, "branch": branch or "", "run_id": r.get("id"), "updated_at": r.get("updated_at")})
            return failed[:5]
        except Exception as e:
            return [{"repo": repo_name, "branch": branch or "", "error": str(e)}]
    def create_fix_pr_on_broken_branch(self, repo, broken_branch, fixed_files):
        import urllib.request, json, os
        if not repo or not broken_branch: return None
        failed = self.check_failed_ci_jobs_1hr(repo, broken_branch)
        if not isinstance(failed, list): failed = []
        valid = [x for x in failed if "error" not in x]
        if not valid: return None  # must have actual failure
        label = f"ci_{repo.split('/')[-1]}"
        derived = self.derive_repo(label)
        if not derived: return None
        fix_name = f"openhands_fix_{repo.split('/')[-1]}_{os.urandom(4).hex()}"
        repo_short = repo.split('/')[-1]
        api_url = f"https://api.github.com/repos/RTC12-Test/{repo_short}/pulls"
        payload = {"title": f"Auto fix PR for {repo} ({broken_branch})", "head": fix_name, "base": broken_branch, "body": f"Draft PR for failed CI within 1hr; target={broken_branch}", "draft": True}
        try:
            ref_url = f"https://api.github.com/repos/RTC12-Test/{repo_short}/git/refs"
            urllib.request.Request(ref_url, data=json.dumps({"ref": f"refs/heads/{fix_name}", "sha": "main"}).encode(), headers={"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN','')}", "Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(urllib.request.Request(ref_url, data=json.dumps({"ref": f"refs/heads/{fix_name}", "sha": "main"}).encode(), headers={"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN','')}", "Content-Type": "application/json"}, method="POST"), timeout=10)
        except: pass
        req = urllib.request.Request(api_url, data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN','')}", "Accept": "application/vnd.github.v3+json", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.load(resp)
                return {"pr_url": result.get("html_url"), "repo": repo, "fix_branch": fix_name, "base": broken_branch}
        except Exception as e:
            return {"pr_url": None, "repo": repo, "fix_branch": fix_name, "base": broken_branch, "error": str(e)}
