# Dynamic Agent Prompt — CI Monitor & Fix Agent

## Dynamic Label Matching
- Watch all PR labels matching ci_*** (e.g. ci_terraform, ci_python, ci_go).
- Derive target child repo from label suffix: ci_terraform -> RTC12-Test/terraform-child.
- Use repos_config.json / label mapping; never hardcode all repos.

## Monitor & Fix Flow
1. Monitor child repo Actions (derived from ci_* label on PR).
2. Pass -> ignore.
3. Fail -> detect broken branch from failed run; gather changed files (skip deleted); create fix branch from broken base; push draft PR with label review; track changes.
