#!/usr/bin/env python3
"""Central CI Remediation Agent entrypoint."""
import os
import sys
import json

from engine.orchestrator import CIOrchestrator

def main():
    agent = CIOrchestrator()
    repos = agent.discover_repos()
    if not repos:
        print("No repositories configured for monitoring.")
        return

    print(json.dumps({"monitoring_repos": repos}, indent=2))
    success = agent.run_full_remediation(repos)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()