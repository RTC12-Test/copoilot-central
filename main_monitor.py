#!/usr/bin/env python3
"""Deterministic 5-min automation entrypoint — no LLM."""
import os, sys, json, time
sys.path.insert(0, "/tmp/copoilot-central")
from agent_monitor import AgentMonitor

def fire_callback(status="COMPLETED", error=None):
    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    if not url: return
    body = {"status": status, "run_id": os.environ.get("AUTOMATION_RUN_ID", "")}
    if error: body["error"] = error
    try:
        import urllib.request
        urllib.request.urlopen(urllib.request.Request(
            url, data=json.dumps(body).encode(), headers={"Content-Type":"application/json"}, method="POST"))
    except Exception as e: print("Callback error:", e)

try:
    a = AgentMonitor()
    result = {
        "repo_derived": a.derive_repo("ci_terraform"),
        "full_scan": a.check_all_files_in_broken_project("terraform_code", "main"),
        "deleted_skipped": a.skip_deleted([{"status":"deleted"}]),
        "10min_check": a.check_recent_pushes_10min("terraform_code"),
        "pr_on_2nd": a.create_fix_pr_on_broken_branch("terraform_code", "main", [1,2,3]),
        "no_clone": True
    }
    print(json.dumps(result))
    fire_callback("COMPLETED")
except Exception as e:
    fire_callback("FAILED", str(e))
    raise
