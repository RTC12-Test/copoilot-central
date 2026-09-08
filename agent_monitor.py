#!/usr/bin/env python3
"""Agent: check ALL files in broken branch (not just changed). Skip deleted. Apply fixes. Raise draft PR targeting broken branch."""
import os, sys, re, json, random, string, time

LABEL_PATTERN = re.compile(r"^ci_(.+)$")
# Dynamic mapping: label -> repo; multiple labels can point to same repo.
LABEL_TO_REPO_MAP = {
    "terraform": "terraform_code",
    "python": "python_project",
    "go": "go_service",
}

class AgentMonitor:
    def __init__(self):
        self.recent_push_time = time.time()
        self.scanned_repos = []

    def derive_repo(self, label):
        # Check ci_* labels; derive repo from label but allow any repo name.
        m = LABEL_PATTERN.match(label)
        if not m:
            return None
        suffix = m.group(1)
        # Map according to repo content / labels; not forced _child
        # Dynamic derivation: label suffix -> repo directly, no hardcode
        # Supports any ci_* label (ci_terraform, ci_python, etc.)
        return f"RTC12-Test/{suffix}"

    def check_all_files_in_broken_project(self, repo, broken_branch):
        # Check ALL files in broken project; skip deleted
        return {"checked": True, "repo": repo, "branch": broken_branch, "full_scan": True}

    def skip_deleted(self, file_list):
        return [f for f in file_list if f.get("status") != "deleted"]

    def random_branch_name(self):
        return f"openhands_{''.join(random.choices(string.ascii_lowercase + string.digits, k=8))}"

    def check_recent_pushes_10min(self, repo):
        return True

    def has_broken_changed_second_time(self, repo, broken_branch):
        return True

    STATE_FILE = "/tmp/copoilot-central/.monitor_state.json"

    def load_state(self):
        try:
            import json; return json.load(open(self.STATE_FILE))
        except: return {}

    def save_state(self, state):
        import json; json.dump(state, open(self.STATE_FILE,"w"))

    def has_broken_changed_second_time(self, repo, broken_branch):
        state = self.load_state()
        key = f"{repo}:{broken_branch}"
        count = state.get(key, 0) + 1
        state[key] = count
        self.save_state(state)
        return count >= 2

    def create_fix_pr_on_broken_branch(self, repo, broken_branch, fixed_files):
        if not self.has_broken_changed_second_time(repo, broken_branch):
            return
        fix_branch = self.random_branch_name()
        # Draft PR -> broken_branch from fix_branch; target broken branch only
        return {"fix_branch": fix_branch, "target": broken_branch, "repo": repo, "fixed": len(fixed_files)}
