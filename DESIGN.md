# Central CI Remediation Agent — Technology-agnostic design

## Architecture
- Central repo: control plane
- core/ — GitHub API, models, git ops, LLM abstraction
- adapters/ — Pluggable technology adapters registered by ci_* label
- engine/ — Orchestrator: monitor → diagnose → fix → validate → PR
- config/ — Repo list, thresholds
- prompts/ — LLM templates

## Label → Adapter Mapping
ci_terraform → adapters.terraform_adapter
ci_python    → adapters.python_adapter
ci_java     → adapters.java_adapter
ci_go       → adapters.go_adapter
ci_rust     → adapters.rust_adapter

Multiple labels supported per repo; failed run determines affected tech from workflow/job context + repo labels.

## Branch Handling
Fix branch: `ai-fix/<tech>-ci-failure-<run-id>` created FROM broken branch (not main). PR targets broken branch.
