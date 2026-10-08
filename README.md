# Central CI Remediation Agent (`copoilot-central`)

A continuously-running agent that **automatically fixes failing GitHub Actions CI
runs** in monitored repositories and opens pull requests with the fixes.

The agent discovers repositories **dynamically** — no org, repo, or branch names
are hardcoded anywhere. Orgs are derived from the GitHub token, repos are
enumerated through the API, and default branches are resolved at runtime from
what the API reports (or from config fallbacks) — never assumed to be `main`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  GitHub (API + Actions)                                                  │
│  • orgs / repos / topics / branches   • failed runs + job logs           │
│  • git remotes (clone/push)           • PRs (created with the fix)      │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Copilot Central Agent (this repo)                                       │
│                                                                          │
│  main.py ──► engine/orchestrator.py   (control plane, one pass = 1+ PRs) │
│                 │  │  │  │                                              │
│                 ▼  ▼  ▼  ▼                                              │
│   core/            adapters/          ai/            monitoring/         │
│   GitHub client    tech adapters      pluggable      failure monitors    │
│   git operations   (terraform/python/ model backends (heuristic / ai)    │
│   data models      java/go/rust)      (copilot/openai/                  │
│                                       anthropic)                         │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    ai-fix/<tech>-ci-failure-<run-id> branch
                    PR  →  base = the broken (non-default) branch
```

---

## Table of contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [How it works — the remediation pipeline](#how-it-works--the-remediation-pipeline)
- [Safety guarantees](#safety-guarantees)
- [Dynamic discovery (zero hardcoded values)](#dynamic-discovery-zero-hardcoded-values)
- [Configuration](#configuration)
- [Environment variables](#environment-variables)
- [Usage](#usage)
- [Running as a service (systemd)](#running-as-a-service-systemd)
- [Logging](#logging)
- [Pluggable backends](#pluggable-backends)
- [Technology adapters](#technology-adapters)
- [Development & testing](#development--testing)
- [Exit codes](#exit-codes)

---

## What it does

For every monitored repository the agent:

1. **Finds** the most recent failed GitHub Actions run (within a recency window).
2. **Diagnoses** *why* it failed (failure category + root cause) via a pluggable
   monitor — deterministic log patterns by default, optionally AI.
3. **Fixes** the code on a dedicated branch, either with an AI model (default)
   or with deterministic technology adapters.
4. **Validates** the fix by running the project's build/test commands.
5. **Pushes** the fix branch and **opens a pull request** targeting the broken
   (non-default) branch — never `main`/the default branch.

It can run **once** per invocation or **continuously** (poll every N seconds)
as a 24/7 service, the recommended operating mode.

---

## Architecture

The agent is deliberately layered so every decision point is pluggable:

| Layer | Module(s) | Responsibility |
|---|---|---|
| **Entrypoint** | `main.py` | CLI, one-shot vs `--watch` continuous mode, tee logging to file + console, token resolution, graceful shutdown on SIGTERM/SIGINT |
| **Engine / control plane** | `engine/orchestrator.py` (`CIOrchestrator`) | The pipeline: discovery → repo selection → failed-run selection → guarded fix → validation → PR. This is the only place that ties everything together |
| **GitHub client** | `core/github_client.py` (`GitHubClient`) | All GitHub REST API calls: orgs, repos, topics, runs, logs, branches, compare/merge state, open PRs, PR creation. All branch/default-branch values come from the API at runtime |
| **Git operations** | `core/repository_manager.py` (`RepositoryManager`) | Clone the broken branch into an isolated `/tmp/remediation_workspaces/<repo>_<run_id>` worktree, create the `ai-fix/*` branch *from the broken branch*, stage, commit, and push (`--force`) |
| **Data models** | `core/models.py` | `CIEvent` (a failed run), `ErrorContext`, `FailureCategory`, `FixPlan`, `ValidationResult` |
| **AI backends** | `ai/` | Pluggable `AIModel` interface. Backends: GitHub Copilot CLI (default), OpenAI, Anthropic. Used for repo selection, run selection, log analysis, fix generation, and PR drafting |
| **Failure monitors** | `monitoring/` | Pluggable `FailureMonitor` interface. `heuristic` (deterministic log-pattern analysis through the adapters, default) and `ai` (model-driven with heuristic fallback) |
| **Tech adapters** | `adapters/` | Per-technology failure parsing and fix planning/validation: `terraform`, `python`, `java`, `go`, `rust`. Registered under `ci_*` labels |
| **Config** | `config/` | `ci_remediation.yaml` (monitoring rules) and `ai_models.yaml` (AI backend selection) |

### Key design idea: everything is pluggable

The orchestrator only depends on **abstract interfaces**, so each stage can be
swapped without touching the core pipeline:

- `AIModel` (`ai/base.py`) — any model backend that can *analyze logs*, *suggest
  fixes*, *select repos/runs*, *fix a workspace in place*, and *draft PRs*.
- `FailureMonitor` — how the agent *explains* a failure.
- `BaseTechAdapter` — how a specific *technology* is parsed, fixed, and validated.

---

## Project structure

```
.
├── main.py                      # entrypoint (one-shot / --watch agent)
├── engine/
│   └── orchestrator.py          # CIOrchestrator: full remediation pipeline
├── core/
│   ├── github_client.py         # GitHub REST client (runs, logs, branches, PRs)
│   ├── repository_manager.py    # clone / branch / commit / push
│   └── models.py                # CIEvent, ErrorContext, FailureCategory, FixPlan
├── ai/
│   ├── base.py                  # AIModel ABC + response parsers
│   ├── copilot_model.py         # GitHub Copilot CLI backend (default)
│   └── provider_models.py       # OpenAI / Anthropic HTTP backends
├── monitoring/
│   ├── base.py                  # FailureMonitor ABC, FailureAnalysis
│   ├── heuristic_monitor.py     # deterministic log-pattern monitoring (default)
│   └── ai_monitor.py            # AI monitoring with heuristic fallback
├── adapters/
│   ├── base.py                  # BaseTechAdapter ABC, FixPlan, ValidationResult
│   ├── terraform_adapter.py     # ci_terraform
│   ├── python_adapter.py        # ci_python
│   ├── java_adapter.py          # ci_java
│   ├── go_adapter.py            # ci_go
│   └── rust_adapter.py          # ci_rust
├── config/
│   ├── ci_remediation.yaml      # monitoring / fixing / PR / agent config
│   └── ai_models.yaml           # AI model backend selection
├── deploy/
│   └── copoilot-central.service # systemd unit for continuous operation
└── tests/                       # unittest suites (89 tests)
```

---

## How it works — the remediation pipeline

### 1. Repository discovery (fully dynamic)

`CIOrchestrator.discover_repos()` (`engine/orchestrator.py`):

- If an explicit `repositories:` list is configured, it is used as-is
  (normalized by `_normalize_repos`).
- Otherwise the orgs to scan come from:
  1. the optional `organizations:` config override, or
  2. **the token itself** — `resolve_orgs_from_token()` returns the
     authenticated user's org memberships, or their own account if they belong
     to no org.
- Every repo in those orgs is enumerated via the GitHub API (in parallel, up to
  8 workers). The agent's own repo (resolved from config → `CI_SELF_REPO` → git
  remote) is excluded.
- For each repo the code records: `name`, `url`, `labels` (only `ci_*` topics),
  `pushed_at` (from the API), and the **default branch** — the API's
  `default_branch` wins; config `monitoring.default_branch` or a per-org
  `branch:` override only applies when the API omits it. **No branch name is
  ever invented.**
- The candidate list is then narrowed by **AI selection** (stage 2).

### 2. AI repo selection

`_apply_repo_selection()` with `monitoring.repo_selection: ai` (default):

- The pluggable model receives every enumerated repo with its `ci_*` labels and
  last-push time and keeps only **active** repos: those **pushed within the last
  24 h** or carrying a **`ci_*` label**. Everything else is skipped.
- The model's answer is **cached** (`<cache>/copoilot-central/repo_selection.json`,
  TTL `CI_DISCOVERY_TTL`, default 600 s) so periodic passes don't re-pay the
  model call.
- Safe fallbacks: if the model is unavailable, returns garbage, or matches
  nothing, **all candidates are kept** rather than nothing.

### 3. Finding the first failed run (per repo)

`_first_failed_run(repo)` fetches the repo's recent failed runs (limit 10),
keeps only `conclusion == "failure"`, then **only runs updated within the
recency window** (`CI_RUN_WINDOW_HOURS` > `monitoring.run_window_hours` > 24 h)
— stale historical failures are never dug up.

Every candidate run passes through **branch-health gates** (all values resolved
at runtime, never guessed):

| Gate | Skips the run when… |
|---|---|
| **Branch exists** | `branch_exists()` says the target branch no longer exists (closed/deleted) |
| **Default branch** | `get_default_branch()` resolves and the run is **on** the default branch — the agent never raises a fix PR against `main`/default |
| **Merged** | `branch_merged_into_base()` says the target branch was already merged into the default branch |
| **Stale** | `branch_has_passing_run_after()` says the branch already has a **newer successful run** (the failure is stale; the branch is green now) |
| **Open ai-fix PR** | an open `ai-fix/*` PR already targets that branch (batch-queried once per repo when supported) |

A run with an **unknown/empty head branch** is *not* dropped: the branch gates
are skipped and the target is resolved at fix time from the repo config.

### 4. Run selection (one run per pass)

`get_latest_failed(repos)` scans all monitored repos in parallel and collects at
most one candidate per repo.

- **Exactly one candidate** → taken directly, no model call.
- **Multiple candidates** → `_ai_select_run()` asks the model to pick one
  (policy: *latest failure first*), with the newest `updated_at` as the code
  fallback when the model is unavailable or unhelpful.

### 5. Fix generation

`fix_single_run(...)` is the end-to-end step for a chosen run:

1. **Match the repo config** and resolve the target branch:
   `event.broken_branch` or the repo's configured `branch`.
2. **Re-run the branch gates** at fix time (they can change between scan and fix).
3. **Fetch the failed job logs.**
4. **Resolve the technology** for this run — the `ci_<tech>` label is generated
   *per run*, first from the **workflow file name**
   (`_infer_tech_from_workflow`, e.g. `terraform-ci.yaml` → `terraform`,
   `ci-golang-terraform.yaml` → `go` + `terraform`), then from **log
   signatures** (`_tech_from_logs`) when the workflow name is generic. Runs with
   no supported tech in either are skipped (never remediated).
5. **Diagnose** via the `FailureMonitor` (heuristic by default, AI optional).
6. **Create the fix branch**:
   ```
   ai-fix/<tech>-ci-failure-<run-id>
   ```
   `RepositoryManager.prepare_workspace()` clones the repo at the **broken
   branch** (`git clone --depth 50 --branch <broken_branch>`) and creates the
   fix branch **from the broken branch** — so the resulting PR naturally bases
   on the broken branch, never on `main`/default.
7. **Generate the fix** — two engines (`CI_FIX_ENGINE` / `fixing.engine`):
   - **`ai` (default)**: `AIModel.fix_workspace()` — the model works *inside the
     cloned workspace* with full tool access, edits files and may run
     validation itself. Changed files are read back via
     `git status --porcelain`.
   - **`adapter`**: the technology adapter plans the fix from the error context;
     a per-file AI `suggest_fix()` pass then rewrites each planned file.
8. **Write the changes** to the workspace.
9. **Validate** — `validate_fix()` runs each technology's default validation
   commands (e.g. `pytest`, `go test`, `terraform validate`). Multi-tech
   workflows validate in parallel. **Validation failure blocks the push** — a
   broken fix is never pushed.
10. **Commit & push** the fix branch (`commit_and_push`, force-push to the
    ephemeral fix branch).
11. **Open the PR** — `create_pr()` with `base = the broken branch`, head =
    the fix branch, label `review`. PR title/body come from a deterministic
    template by default, or are AI-drafted with `CI_PR_CONTENT=ai`.

### 6. Remediate everything eligible

`run_full_remediation()` calls `fix_next()` in a loop until no eligible failure
remains. Each fix opens an ai-fix PR that covers that branch, so the next scan
skips it and the loop terminates naturally. Per-pass work can be capped with
`CI_MAX_FIXES_PER_RUN` / `fixing.max_per_run`.

---

## Safety guarantees

The agent contains several defense-in-depth guards, each unit-tested:

- **Never targets the default branch.** Runs on the default branch are skipped
  at selection *and* at fix time; fix PRs always base on the existing broken
  (non-main) branch.
- **No hardcoded names.** Orgs, repos, and default branches are all resolved at
  runtime (token → orgs → API repos; API `default_branch` → config fallback).
  There is no `"main"`, org, or repo literal anywhere in the code.
- **No fix-PR loops.** Branches already covered by an open `ai-fix/*` PR are
  skipped every pass; within one invocation each run is fixed at most once.
- **No stale rewrites.** A run whose branch already has a newer successful run
  is considered stale and left alone.
- **No fixes for dead branches.** Deleted/closed branches and branches merged
  into the default branch are skipped.
- **Recency window.** Only failures updated within the last
  `run_window_hours` (default 24 h) are remediated — history is never dug up.
- **Validation gates the push.** If the fix does not validate, the branch is
  not pushed and no PR is opened.

---

## Dynamic discovery (zero hardcoded values)

| What | How it's resolved |
|---|---|
| **Orgs to scan** | Config `organizations:` override, else derived from the GitHub token (org memberships, or own account) |
| **Repos** | Enumerated via the API from the resolved orgs; the agent's own repo excluded (`CI_SELF_REPO` / config / git remote) |
| **Default branch** | API `default_branch` per repo → config `monitoring.default_branch` → per-org `branch:` override → empty (never a hardcoded name like `main`) |
| **Tech per run** | Workflow file name, else log signatures (generates `ci_<tech>` per run) |
| **Which repos to monitor** | AI selection (≤24 h push *or* `ci_*` label), fallback = all |

Because of this, the agent works against orgs and repos it has never seen,
including old repos whose default branch is `master` or a custom name.

---

## Configuration

All config lives in `config/ci_remediation.yaml` (override the path with
`CI_REMEDIATION_CONFIG`).

```yaml
agent:
  poll_interval_seconds: 300   # continuous mode: seconds between passes
  log_file: /var/log/agent.log # where every agent message is appended

creating:
  pr_content: template         # template | ai  (PR title/body authorship)

fixing:
  engine: ai                   # ai | adapter  (who edits the code)
  # max_per_run: 3             # cap fixes per invocation (unset = all)

monitoring:
  provider: heuristic          # heuristic | ai  (who diagnoses failures)
  repo_selection: ai           # ai | heuristic   (who picks monitored repos)
  # default_branch: main       # ONLY used when the API omits default_branch
  # run_window_hours: 24       # only fix failures updated w/in this window

# organizations:               # optional: pin orgs instead of token-derived
#   - name: RTC12-Test
#     branch: feature/tas

# repositories:                # optional: fixed list instead of discovery
#   - name: terraform_child
#     url: https://github.com/RTC12-Test/terraform_child
#     labels: [ci_terraform]
```

AI backend selection lives in `config/ai_models.yaml`:

```yaml
ai:
  model:
    name: copilot              # copilot | openai | anthropic
    binary: copilot
    timeout: 180
```

---

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `GITHUB_TOKEN` | GitHub auth (fallback: CLI arg, then `gh auth token`) | — |
| `CI_REMEDIATION_CONFIG` | Path to `ci_remediation.yaml` | `config/ci_remediation.yaml` |
| `CI_AGENT_LOG` | Log file path | `/var/log/agent.log` |
| `CI_POLL_INTERVAL_SECONDS` | Continuous-mode poll interval | `300` |
| `CI_RUN_WINDOW_HOURS` | Recency window for fixable failures | `24` |
| `CI_MONITOR_PROVIDER` | `heuristic` / `ai` failure diagnosis | `heuristic` |
| `CI_MONITOR_REPO_SELECTION` | `ai` / `heuristic` repo selection | `ai` |
| `CI_FIX_ENGINE` | `ai` / `adapter` fix generation | `ai` |
| `CI_PR_CONTENT` | `template` / `ai` PR drafting | `template` |
| `CI_AI_MODEL` | Model backend: `copilot` / `openai` / `anthropic` | `copilot` |
| `CI_MODEL_CONFIG` | Path to `ai_models.yaml` | `config/ai_models.yaml` |
| `CI_MAX_FIXES_PER_RUN` | Cap fixes per invocation (0 = unlimited) | unlimited |
| `CI_DISCOVERY_TTL` | Cache TTL (s) for AI repo-selection results | `600` |
| `CI_CACHE_DIR` | Directory for the selection cache | `<tmpdir>/copoilot-central` |
| `CI_SELF_REPO` | The agent's own repo name (excluded from monitoring) | git remote |

---

## Usage

```bash
# One-shot: fix every eligible failed CI run and exit
python3 main.py <github-token>

# One-shot using $GITHUB_TOKEN or the token already stored in the gh keyring
python3 main.py

# Continuous agent: rediscover + re-scan + fix forever
python3 main.py <github-token> --watch
python3 main.py <github-token> --interval 300

# Custom log file
python3 main.py <token> --watch --log /var/log/agent.log
```

Exit code is `0` on success and `1` when at least one eligible run could not be
fixed. In continuous mode the process keeps running until stopped with
`SIGTERM`/`SIGINT`, finishing its current pass before exiting gracefully.

---

## Running as a service (systemd)

The recommended way to run the agent 24/7:

```bash
sudo cp deploy/copoilot-central.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now copoilot-central
```

The unit runs `main.py --watch` (polling every 300 s by default) as user
`ghost` with `Restart=always`, logging to `/var/log/agent.log`. Manage it like
any service:

```bash
sudo systemctl status copoilot-central
sudo systemctl restart copoilot-central       # picks up new code after a git pull
journalctl -u copoilot-central -f              # live logs
```

---

## Logging

Every agent message (stdout + stderr) is tee'd to **both** the console and a log
file (default `/var/log/agent.log`). Override via `--log`, `$CI_AGENT_LOG`, or
`agent.log_file` in the config. The log shows every stage of each pass:

```
[DISCOVER] enumerated 34 repos across 2 org(s); selection delegated to the AI model
[SELECT]   Copilot selected 3/34 repos to monitor
[SCAN]     python_child: first failed run 37731841671 (updated 2026-10-08T05:21:55Z)
[SCAN]     feature/test run is stale / already covered — skipping
[MONITOR:heuristic] category=test_failure confidence=0.9
[FIX:ai]   AI-generated fix via model 'copilot'
[VALIDATE] python: passed
PR created: https://github.com/org/repo/pull/21
[LOOP]     no more failed CI runs within the window; fixed 1 run(s)
```

---

## Pluggable backends

### AI models (`ai/`)

`AIModel` (`ai/base.py`) defines the contract. Implementations:

| Backend | Where | Notes |
|---|---|---|
| **Copilot CLI** (default) | `ai/copilot_model.py` | Wraps the `copilot` binary in `-p` (prompt) mode with all tools/paths allowed, so it can analyze logs, select repos/runs, edit files in a workspace, and draft PRs |
| **OpenAI** | `ai/provider_models.py` | `OPENAI_API_KEY`, model `gpt-4o`; uses the `openai` package |
| **Anthropic** | `ai/provider_models.py` | `ANTHROPIC_API_KEY`, model `claude-sonnet-4-5` |

Response parsing is robust (`ai/base.py`): `parse_repo_selection`,
`parse_run_selection`, `parse_pr_content` tolerate prose and markdown fences.
Every AI step has a deterministic code fallback, so **the agent never stalls on
the model**.

### Failure monitors (`monitoring/`)

| Monitor | When | Behavior |
|---|---|---|
| `heuristic` (default) | always | Deterministic log-pattern analysis via the tech adapters (`analyze_failure`) — fully offline |
| `ai` | `CI_MONITOR_PROVIDER=ai` | Model classifies the failure + root cause; transparently falls back to heuristic on parse/unavailability |

### Tech adapters (`adapters/`)

Registered via `ci_<tech>` labels. One adapter parses failures, plans fixes, and
provides validation commands for its technology. Adapters are **always** used
for validation even when the AI engine generates the fix.

---

## Technology adapters

| Label | Adapter | Validation commands (typical) |
|---|---|---|
| `ci_terraform` | `adapters/terraform_adapter.py` | `terraform validate`, `terraform fmt -check` |
| `ci_python` | `adapters/python_adapter.py` | `python -m pytest`, `python -m py_compile` |
| `ci_java` | `adapters/java_adapter.py` | `mvn test` / `gradle test` |
| `ci_go` | `adapters/go_adapter.py` | `go build ./...`, `go vet`, `go test ./...` |
| `ci_rust` | `adapters/rust_adapter.py` | `cargo build`, `cargo test` |

A repo can carry **multiple** `ci_*` labels; a failed run determines its tech
from its own workflow file first, then its logs — so `ci-golang-terraform.yaml`
with Go+TF logs resolves to both technologies.

---

## Development & testing

```bash
# Run the whole suite (unittest — pytest is not required)
python3 -m unittest discover -s tests -q
```

The 89 tests cover:

- Adapter failure parsing, fix planning, validation, and PR body generation for
  every technology.
- Dynamic discovery: orgs derived from the token, `ci_*` topic filtering,
  repo-selection caching.
- Run-selection policy: single-candidate shortcut, AI selection + fallbacks,
  open-PR coverage, deleted/merged branches, **default-branch refusal**, stale
  run skipping, unknown-branch runs staying candidates.
- Branch-health client semantics and the no-hardcoded-default-branch behavior.
- `fix_next` / `run_full_remediation` loops, `max_per_run` caps, and
  no-failure termination.
- AI model response parsing and monitor fallbacks.

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | One-shot pass completed (no failures, or all eligible runs fixed) |
| `1` | At least one eligible run was not fixed (validation blocked, missing PR, etc.) |
| `130` (continuous) | Stopped by `SIGINT`/`SIGTERM` after finishing the current pass |

---

*This agent is fully dynamic: it never hardcodes an org, repo, or default-branch
name. It learns orgs from your token, enumerates repos via the API, defers
monitoring decisions to an AI model, and always raises fix PRs against the real
broken branch — even when that branch is `master` or has a custom name.*