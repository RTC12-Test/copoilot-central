#!/usr/bin/env python3
"""Central CI Remediation Agent entrypoint."""
import os
import sys
import json
import subprocess

from engine.orchestrator import CIOrchestrator


def resolve_token(argv_token=None):
    """Resolve the GitHub token in this priority order:
       1. argv/param token
       2. $GITHUB_TOKEN env var
       3. `gh auth token` (token already stored in the gh keyring)
    """
    token = argv_token or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        out = subprocess.check_output(["gh", "auth", "token"],
                                      stderr=subprocess.DEVNULL, text=True)
        token = out.strip()
        if token:
            print("[INFO] Using token from `gh auth token`.")
            return token
    except Exception:
        pass
    return None


def main():
    token = resolve_token(sys.argv[1] if len(sys.argv) > 1 else None)
    if not token:
        print("[WARN] No GITHUB_TOKEN provided (argv[1], $GITHUB_TOKEN, or `gh auth token`). "
              "Private repositories will be unreachable and no runs will be found.")
    agent = CIOrchestrator(github_token=token)
    repos = agent.discover_repos()
    if not repos:
        print("No repositories configured for monitoring.")
        return

    print(json.dumps({"monitoring_repos": repos}, indent=2))
    success = agent.run_full_remediation(repos)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()