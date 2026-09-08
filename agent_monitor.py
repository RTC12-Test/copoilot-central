#!/usr/bin/env python3
"""Agent: check ALL files in broken branch (not just changed). Skip deleted. Apply fixes. Raise draft PR targeting broken branch."""
import os, sys, re, json

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

    def check_all_files_in_broken_branch(self, repo, broken_branch):
        """Review/check ALL files in broken branch, not just changed files."""
        # List all files on broken branch
        pass

    def skip_deleted(self, file_list):
        return [f for f in file_list if f.get("status") != "deleted"]

    def create_fix_pr_on_broken_branch(self, repo, broken_branch, fixed_files):
        # Create/update draft PR targeting broken branch
        pass
