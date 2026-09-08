# Dynamic Agent Prompt — CI Monitor & Fix Agent

## Dynamic Label Matching
- Watch PR labels `ci_***` (e.g. `ci_terraform`, `ci_python`, `ci_go`).
- Derive repo: `ci_terraform` -> `RTC12-Test/terraform_child`.
- Never hardcode repos; derive from label suffix.

## Filter: Recent Pushes Only
- Only check repos pushed within last 10 minutes. Skip repos not pushed in 10 min.

## Monitor & Fix Flow (Broken Project Full Review)
1. Monitor child repo Actions (derived from `ci_*` PR label).
2. Pass -> ignore.
3. Fail -> detect broken branch from failed run.
4. Check ALL files in broken project (full repo files, not just changed).
5. Skip deleted files.
6. Only create/update DRAFT PR when broken branch has made changes (second time / new change since last fix).
7. Create fix branch with random name `opendhands_**`.
8. Apply fixes in created branch only. Draft PR targets broken branch from `opendhands_**` branch.
9. Label PR `review`. If draft PR already exists for same broken branch and broken branch hasn't changed, skip.
