
import urllib.request, json, os, re, time

class AgentMonitor:
    LABEL_PATTERN = re.compile(r"^ci_(.+)$")
    
    def derive_repo(self, label):
        if not self.LABEL_PATTERN.search(label): return None
        return f"RTC12-Test/{self.LABEL_PATTERN.search(label).group(1)}"
    
    # 3. Check ALL files in broken project
    def list_broken_repo_files(self, repo, broken_branch):
        try:
            token = os.environ.get("GITHUB_TOKEN","")
            url = f"https://api.github.com/repos/RTC12-Test/{repo.split('/')[-1]}/git/trees/{broken_branch}?recursive=1"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.load(resp)
            files = []
            for item in data.get("tree", []):
                if item.get("type") == "blob" and not item.get("path", "").startswith(".git/"):
                    files.append(item.get("path"))
            return files
        except Exception as e:
            return [{"error": str(e)}]
    
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
    
    def create_fix_pr_on_broken_branch(self, repo, broken_branch, fixed_files):
        import urllib.request, json, os
        if not repo or not broken_branch: return None
        failed = self.check_failed_ci_jobs_1hr(repo, broken_branch)
        if not isinstance(failed, list): failed = []
        valid = [x for x in failed if "error" not in x]
        if not valid: return None
        label = f"ci_{repo.split('/')[-1]}"
        derived = self.derive_repo(label)
        if not derived: return None
        fix_name = f"openhands_fix_{repo.split('/')[-1]}_{os.urandom(4).hex()}"
        repo_short = repo.split('/')[-1]
        # 7. Create branch from broken branch (not main) with correct SHA
        api_url = f"https://api.github.com/repos/RTC12-Test/{repo_short}/pulls"
        payload = {"title": f"Auto fix PR for {repo}", "head": fix_name, "base": broken_branch, "body": f"Draft PR; fix branch={fix_name}", "draft": True}
        try:
            # Get broken branch SHA for branch creation
            ref_req = urllib.request.Request(f"https://api.github.com/repos/RTC12-Test/{repo_short}/git/refs/heads/{broken_branch}", headers={"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN','')}"}, method="GET")
            broken_sha = json.load(urllib.request.urlopen(ref_req, timeout=10))["object"]["sha"]
            # Create fix branch from broken branch SHA
            ref_url = f"https://api.github.com/repos/RTC12-Test/{repo_short}/git/refs"
            urllib.request.urlopen(urllib.request.Request(ref_url, data=json.dumps({"ref": f"refs/heads/{fix_name}", "sha": broken_sha}).encode(), headers={"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN','')}", "Content-Type":"application/json"}, method="POST"), timeout=10)
        except: pass
        req = urllib.request.Request(api_url, data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN','')}", "Accept":"application/vnd.github.v3+json", "Content-Type":"application/json"}, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.load(resp)
            return {"pr_url": result.get("html_url"), "repo": repo, "fix_branch": fix_name, "base": broken_branch, "label":"review"}
        except Exception as e:
            return {"pr_url": None, "repo": repo, "fix_branch": fix_name, "base": broken_branch, "error": str(e), "label":"review"}
    
    def run_auto_pr(self, repos=None):
        if not repos: repos = ["terraform_child"]
        result = self.get_latest_failed_per_repo(repos)
        out = []
        for repo, latest in result.items():
            broken = latest.get("branch") or "feature/tas"
            # 5. Check all broken repo files
            all_files = self.list_broken_repo_files(repo, broken)
            # Skip if no new change since last fix (simplified: always create unique branch)
            pr = self.create_fix_pr_on_broken_branch(repo, broken, all_files)
            out.append({"repo": repo, "pr": pr, "latest_job": latest.get("run_id"), "broken_files": all_files})
        return out
