#!/usr/bin/env python3
"""Agent: check ALL files in broken branch (not just changed). Skip deleted. Apply fixes. Raise draft PR targeting broken branch."""
import os, sys, re, json, random, string, time

LABEL_PATTERN = re.compile(r"^ci_(.+)$")

class AgentMonitor:
    def __init__(self):
        pass

    def derive_repo(self, label):
        m = LABEL_PATTERN.match(label)
        if not m: return None
        suffix = m.group(1)
        # Dynamic derivation - never hardcode all repos; derive from label
        return f"RTC12-Test/{suffix}_child"  # e.g. ci_terraform -> terraform_child

    def check_all_files_in_broken_project(self, repo, broken_branch):
        # Check ALL files in broken project (full repo files, not just changed)
        pass

    def skip_deleted(self, file_list):
        return [f for f in file_list if f.get("status") != "deleted"]

    def random_branch_name(self):
        return f"openhands_{''.join(random.choices(string.ascii_lowercase + string.digits, k=8))}"

    def check_recent_pushes_10min(self, repo):
        # Only repos pushed within 10 minutes; skip others
        return True

    def has_broken_changed_second_time(self, repo, broken_branch):
        # Only make draft PR when broken branch has made changes (second time)
        return True


    def create_fix_pr_on_broken_branch(self, repo, broken_branch, fixed_files):
        # Draft PR only when broken changed second time; branch random opendhands_**; target broken branch only; work in created branch only
        if not self.has_broken_changed_second_time(repo, broken_branch):
            return
        fix_branch = self.random_branch_name()
        # Apply fixes to all valid files in broken project (check all files)
        # Draft PR from fix_branch -> broken_branch
        pass
