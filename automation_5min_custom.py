#!/usr/bin/env python3
import os, sys, json
sys.path.insert(0, "/tmp/copoilot-central")
from agent_monitor import AgentMonitor

def fire_callback(status="COMPLETED", error=None):
    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    if url:
        print(f"Callback: {status}")

def main():
    try:
        a = AgentMonitor()
        result = {
            "derive": a.derive_repo("ci_terraform"),
            "scan": a.check_all_files_in_broken_project("terraform", "main"),
            "deleted_skipped": a.skip_deleted([{"status":"deleted"}]),
            "pr": a.create_fix_pr_on_broken_branch("terraform", "main", [1]),
            "no_clone": True
        }
        print(json.dumps(result))
        fire_callback("COMPLETED")
    except Exception as e:
        fire_callback("FAILED", str(e))
        raise

if __name__ == "__main__":
    main()
