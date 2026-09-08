# AGENTS.md — copoilot-central

Central repository for the OpenHands language-agnostic CI remediation agent.

## What this repo is
- `prompts.md` defines the autonomous monitoring / fix agent prompts for child repos (`RTC12-Test/terraform_child`, `python_child`, etc.).
- No package.json / Makefile / pyproject.toml / build script here; no local tests or linters defined.
- Agent uses `gh` CLI extensively (`gh pr list`, `gh pr checks`, `gh run view`, `gh pr create`).

## Key commands verified
- `git ls-tree -r --name-only HEAD` → only `prompts.md` tracked.
- No `npm`, `pytest`, `make` targets present.

## Workflow notes
- Fix commits are made as `aravind15b <aravind15b@test.com>` when remediating child repos.
- Child repos are tagged with `ci_<language>` labels on PR failure.
- Read `prompts.md` for full operating protocol (Steps 1–7) and multi-language diagnostic matrix.

## Constraints / gotchas
- This repo has no CI of its own defined in-repo.
- `origin` URL has no credentials embedded; use `GITHUB_PERSONAL_ACCESS_TOKEN` with `gh`.
