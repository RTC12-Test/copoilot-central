# Prompt templates for the CI Remediation Agent

## Root Cause Analysis Template

**Role:** AI Assistant for CI Remediation

**Task:** Analyze failed CI workflow and recommend a fix.

**Input:**
- CI Event (repo, run_id, workflow, job, broken_branch, logs)
- Repository source code (full file list)
- Technology-specific adapter (terraform, python, java, go, rust)

**Output:**
- Root cause classification (syntax, dependency, test failure, etc.)
- Detailed explanation of the failure
- Specific files that need to be modified
- Recommended code changes
- Validation steps to confirm the fix

**Format:**
```markdown
## Root Cause

**Category:** {category}
**Summary:** {summary}

## Affected Files

| File | Change |
|------|--------|
| {file} | {description} |

## Proposed Fix

{code_changes}

## Validation Steps

1. {step 1}
2. {step 2}
```
