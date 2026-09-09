#!/usr/bin/env python3
"""Legacy deterministic 5-min automation entrypoint — kept for reference."""
import os, sys, json, time

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
    print("Legacy automation placeholder — see main.py for current implementation.")
    fire_callback("COMPLETED")
except Exception as e:
    fire_callback("FAILED", str(e))
    raise