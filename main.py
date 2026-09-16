#!/usr/bin/env python3
"""Central CI Remediation Agent entrypoint.

Run modes
---------
One-shot (default, unchanged):
    python main.py <github-token>
    Runs one full remediation pass (fixes every eligible failed CI run) and
    exits, exit code 0 = success, 1 = at least one eligible run was not fixed.

Continuous agent (keeps running 24/7 and fixes new failures as they appear):
    python main.py <github-token> --watch
    python main.py <github-token> --interval 300
    The agent re-discovers repositories and re-scans for failed runs every
    <interval> seconds, forever, until stopped with SIGTERM or SIGINT.

Logging
-------
All agent output (stdout + stderr) is tee'd to a log file in addition to the
console. Default: /var/log/agent.log. Override with --log <path>,
$CI_AGENT_LOG, or config/ci_remediation.yaml -> agent.log_file.

Process supervision
-------------------
`&` backgrounds the process but does not survive a logout/reboot. For a real
agent service use the provided systemd unit:
    sudo cp deploy/copoilot-central.service /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable --now copoilot-central
"""
import os
import sys
import json
import time
import signal
import argparse
import subprocess

from engine.orchestrator import CIOrchestrator

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG_FILE = "/var/log/agent.log"
DEFAULT_INTERVAL_SECONDS = 300
CONFIG_PATH = (os.environ.get("CI_REMEDIATION_CONFIG")
               or os.path.join(PROJECT_ROOT, "config", "ci_remediation.yaml"))


def load_agent_config() -> dict:
    """Read the optional `agent:` section from config/ci_remediation.yaml.

    Returns {} when the config file is missing/unreadable so the CLI/env
    defaults apply.
    """
    try:
        import yaml
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("agent") or {}
    except Exception:
        return {}


class Tee:
    """Duplicate stream writes to the console AND a log file.

    The codebase logs with print() throughout; redirecting stdout/stderr
    through a Tee captures every agent message in the log file while keeping
    live console output.
    """

    def __init__(self, stream, log_file):
        self.stream = stream
        self.log_file = log_file
        self.encoding = getattr(stream, "encoding", None) or "utf-8"

    def write(self, data):
        try:
            self.stream.write(data)
            self.stream.flush()
        except Exception:
            pass
        try:
            self.log_file.write(data)
            self.log_file.flush()
        except Exception:
            pass
        return len(data)

    def flush(self):
        try:
            self.stream.flush()
        except Exception:
            pass
        try:
            self.log_file.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def fileno(self):
        return self.stream.fileno()


def open_log_file(log_path: str):
    """Open the log file for appending, falling back to a local file.

    Returns the open handle, or None when no writable location exists.
    """
    candidates = [log_path,
                  os.path.join(PROJECT_ROOT, "agent.log")]
    for candidate in candidates:
        try:
            os.makedirs(os.path.dirname(candidate), exist_ok=True)
            return open(candidate, "a", buffering=1)
        except (OSError, PermissionError) as e:
            print(f"[WARN] cannot write log file {candidate} ({e})")
    return None


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


def run_once(agent: CIOrchestrator) -> bool:
    """One full remediation pass: discover monitored repos, then fix
    everything eligible. Returns True when the pass completed (whether or not
    failures were found); the continuous loop keeps going regardless."""
    repos = agent.discover_repos()
    if not repos:
        print("No repositories configured for monitoring.")
        return True
    print(json.dumps({"monitoring_repos": repos}, indent=2))
    return agent.run_full_remediation(repos)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Central CI Remediation Agent — one-shot by default, "
                    "--watch/--interval runs it continuously as an agent.")
    parser.add_argument("token", nargs="?", default=None,
                        help="GitHub token (falls back to $GITHUB_TOKEN, "
                             "then `gh auth token`)")
    parser.add_argument("--watch", "-w", action="store_true",
                        help="run continuously as an agent (poll forever)")
    parser.add_argument("--interval", "-i", type=int, default=None,
                        help="seconds between remediation passes "
                             "(implies --watch)")
    parser.add_argument("--log", default=None,
                        help=f"log file (default: {DEFAULT_LOG_FILE} or "
                             "$CI_AGENT_LOG)")
    args = parser.parse_args(argv)

    agent_cfg = load_agent_config()
    log_path = (args.log
                or os.environ.get("CI_AGENT_LOG")
                or agent_cfg.get("log_file")
                or DEFAULT_LOG_FILE)

    interval = (args.interval
                or _positive_int(os.environ.get("CI_POLL_INTERVAL_SECONDS"))
                or _positive_int(agent_cfg.get("poll_interval_seconds"))
                or DEFAULT_INTERVAL_SECONDS)
    continuous = args.watch or args.interval is not None

    log_file = open_log_file(log_path)
    if log_file:
        sys.stdout = Tee(sys.stdout, log_file)
        sys.stderr = Tee(sys.stderr, log_file)
        print(f"[AGENT] logging to {log_path}")
    else:
        print(f"[WARN] logging to console only (could not open {log_path})")

    token = resolve_token(args.token)
    if not token:
        print("[WARN] No GITHUB_TOKEN provided (argv, $GITHUB_TOKEN, or "
              "`gh auth token`). Private repositories will be unreachable "
              "and no runs will be found.")
    agent = CIOrchestrator(github_token=token)

    if not continuous:
        ok = run_once(agent)
        sys.exit(0 if ok else 1)

    # --- Continuous agent mode -------------------------------------------
    stop = {"flag": False}

    def _handle_stop(signum, frame):
        stop["flag"] = True
        print(f"\n[AGENT] received signal {signum}; stopping after this pass")

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    print(f"[AGENT] continuous mode: polling every {interval}s "
          "(stop with SIGTERM/SIGINT)")
    while not stop["flag"]:
        started = time.time()
        try:
            run_once(agent)
        except KeyboardInterrupt:
            stop["flag"] = True
            break
        except Exception as e:
            print(f"[AGENT] remediation pass failed ({e!r}); "
                  "will retry on the next interval")
        elapsed = time.time() - started
        # Sleep in short slices so SIGTERM/SIGINT stop us within ~1s.
        deadline = time.time() + max(0.0, interval - elapsed)
        while not stop["flag"] and time.time() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.time())))
    print("[AGENT] stopped")
    sys.exit(0)


def _positive_int(value) -> int:
    """Parse a positive int, or 0 for unset/invalid (caller falls back)."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    main()