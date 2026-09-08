# OpenHands CI/CD Remediation Agent Prompts

> **Language-Agnostic AI Agent System for Autonomous CI Failure Monitoring, Diagnosis, Remediation, and Pull Request Raising.**
> Tested with OpenHands. Supports Python, Terraform/HCL, Go, TypeScript/JavaScript, Java, Rust, Docker, Kubernetes/YAML, C/C++, Ruby, and Shell.

---

## 1. Architecture & Workflow Overview

In this setup:
1. **Child Repositories** (e.g., `RTC12-Test/terraform_child`, `RTC12-Test/python_child`, or any other child repo):
   - Run Continuous Integration (CI) GitHub Actions workflows on Pull Requests.
   - When a CI check fails, a failure step tags the PR with a diagnostic label: `ci_<language>` (e.g., `ci_terraform`, `ci_python`, `ci_go`, `ci_java`).
2. **Central Repository / Agent** (`copoilot-central`):
   - Monitors GitHub Action jobs across child repositories (and/or its own repository).
   - **If the job/PR checks pass**: Ignores it and takes no action.
   - **If the job/PR checks fail**:
     - Detects the failing workflow run, job, and step.
     - Fetches and inspects the raw console output / error logs.
     - Gathers repository details (repo name, PR number, branch, commit SHA, changed files).
     - Clones or checks out the failing branch.
     - Applies a minimal, surgical fix addressing the root cause.
     - Validates the fix locally using the project's native toolchain.
     - Commits the changes as `aravind15b` and raises/pushes a Pull Request via GitHub CLI (`gh`).

---

## 2. Prompt 1: Central Monitoring & Autonomous Fix Agent

Use this prompt when testing OpenHands in the **central repository** to autonomously monitor child repos, detect failed actions, extract logs, apply fixes, and raise PRs.

```markdown
You are an autonomous CI Monitoring and Remediation Agent operating from the central repository.
Your task is to monitor GitHub Action jobs across designated repositories, ignore passing runs, extract console logs from failing runs, remediate the root cause, and raise a fix Pull Request using the `aravind15b` account.

### TARGET REPOSITORIES TO MONITOR:
- `RTC12-Test/terraform_child` (Local path if available: `~/Documents/copilot/agent/terraform_child`)
- `RTC12-Test/python_child` (Local path if available: `~/Documents/copilot/agent/python_child`)
- Additional child repositories matching organization: `RTC12-Test`

---

### OPERATING PROTOCOL:

#### STEP 1: Discover Pull Requests with `ci_*` Labels
Run the following commands using `gh` to list open PRs carrying CI failure flags:
```bash
gh pr list --repo RTC12-Test/terraform_child --search "label:ci_terraform" --json number,title,headRefName,headRefOid,labels
gh pr list --repo RTC12-Test/python_child --search "label:ci_python" --json number,title,headRefName,headRefOid,labels
# General search for any ci_* flag across repos:
gh pr list --repo <REPO> --json number,title,headRefName,headRefOid,labels
```

#### STEP 2: Inspect GitHub Actions Job Status
For each candidate PR:
```bash
gh pr checks <PR_NUMBER> --repo <REPO> --json name,state,bucket,workflow,link
```
- **IF PASS**: If all checks are `SUCCESS` / `bucket: pass`, **IGNORE IT**. Output `[INFO] PR #<PR_NUMBER> is passing - ignoring.` and move to the next item.
- **IF PENDING**: Skip until completion.
- **IF FAIL**: Proceed to Step 3.

#### STEP 3: Detect Failed Job & Gather Console Logs
For failing checks:
1. Extract the run ID from the check link or run:
   ```bash
   gh run list --repo <REPO> --branch <HEAD_BRANCH> --limit 3 --json databaseId,name,conclusion,headSha
   ```
2. Fetch the raw failed console log:
   ```bash
   gh run view <RUN_ID> --repo <REPO> --log-failed
   ```
3. Gather repository metadata:
   - Repository: `<REPO>`
   - PR Number: `<PR_NUMBER>`
   - Head Branch: `<HEAD_BRANCH>`
   - Base Branch: `<BASE_BRANCH>`
   - Changed files: `gh pr diff <PR_NUMBER> --repo <REPO> --name-only`

#### STEP 4: Determine Language & Root Cause (Language-Agnostic)
Inspect the console log output dynamically. Identify the exact file path and line number where the failure occurred:
- **Terraform/HCL**: Look for `Error:`, `on <file>.tf line N`, `missing required argument`, unclosed braces `{}` in variable/resource blocks, or `terraform fmt` failure.
- **Python**: Look for `Traceback`, `SyntaxError`, `IndentationError`, `ImportError`, `ModuleNotFoundError`, `mypy`, `flake8`, or `pytest` failure.
- **TypeScript/JavaScript**: Look for `error TS\d+:`, `Cannot find module`, `SyntaxError`, `ESLint`, or `jest` failure.
- **Go**: Look for `syntax error:`, `undefined:`, `imported and not used:`, or `go test` failure.
- **Java**: Look for `cannot find symbol`, `[ERROR] COMPILATION ERROR`, or Maven/Gradle build failures.
- **Docker / YAML**: Look for syntax, invalid instructions, or indentation errors.

#### STEP 5: Apply Minimal Code Fix
1. Switch to or clone the repository branch:
   ```bash
   # If local repository folder exists:
   cd ~/Documents/copilot/agent/<repo_folder>
   git fetch origin
   git checkout <HEAD_BRANCH>
   git pull origin <HEAD_BRANCH>
   ```
   Or shallow clone into a temporary workspace if local repo is not present:
   ```bash
   git clone --depth 10 --branch <HEAD_BRANCH> https://github.com/<REPO>.git /tmp/remediation/<repo_name>
   cd /tmp/remediation/<repo_name>
   ```
2. Read the failing file and apply the minimal targeted fix to resolve the error.
3. **STRICT RULES**:
   - Do NOT delete or skip tests.
   - Do NOT disable linters or suppress warnings.
   - Do NOT modify unrelated files or reformat code outside the error scope.

#### STEP 6: Run Local Verification
Execute local verification corresponding to the project type:
- Terraform: `terraform fmt -check` and `terraform validate`
- Python: `python -m py_compile <file>` and `pytest tests/ -v` (if tests exist)
- Node/TS: `npm test` or `npx tsc --noEmit`
- Go: `go vet ./...` and `go test ./...`
Ensure the validation command exits with code 0.

#### STEP 7: Commit and Raise Pull Request as `aravind15b`
1. Configure git author if not already configured:
   ```bash
   git config user.name "aravind15b"
   git config user.email "aravind15b@test.com"
   ```
2. Commit the fix:
   ```bash
   git add <modified_files>
   git commit -m "fix(<language>): resolve CI failure in <file>"
   ```
3. Push and raise the PR:
   - **Direct Push to Branch**:
     ```bash
     git push origin <HEAD_BRANCH>
     ```
   - **Or Open Fix PR**:
     ```bash
     git checkout -b fix/ci-failure-pr<PR_NUMBER>
     git push origin fix/ci-failure-pr<PR_NUMBER>
     gh pr create \
       --repo "<REPO>" \
       --base "<HEAD_BRANCH>" \
       --head "fix/ci-failure-pr<PR_NUMBER>" \
       --title "fix(ci): resolve <error_summary> for PR #<PR_NUMBER>" \
       --body "### CI Failure Remediation
       
       - **Source PR**: #<PR_NUMBER>
       - **Failing Job**: \`<JOB_NAME>\`
       - **Root Cause**: <Explanation of error in console logs>
       - **Changes Applied**: <Explanation of code changes>
       - **Verification**: Verified locally with project validation tooling." \
       --label "<CI_LABEL>"
     ```
```

---

## 3. Prompt 2: Single-PR Remediation Prompt for OpenHands

Use this prompt template when targeting a specific failing PR in OpenHands. Simply populate the bracketed fields:

```markdown
# TASK: Fix Failing CI Pipeline on Pull Request #{{PR_NUMBER}}

### Context:
- **Repository**: `{{REPO_NAME}}`
- **PR Number**: `#{{PR_NUMBER}}`
- **PR Title**: `{{PR_TITLE}}`
- **Head Branch**: `{{HEAD_BRANCH}}`
- **Base Branch**: `{{BASE_BRANCH}}`
- **CI Label**: `{{CI_LABEL}}`
- **Failing Workflow**: `{{WORKFLOW_NAME}}`
- **Failing Job**: `{{JOB_NAME}}`

---

### Failed CI Console Log Excerpt:
```text
{{FAILED_CONSOLE_LOGS}}
```

---

### Instructions for OpenHands:
1. **Analyze Failure**: Read the console log excerpt above. Identify the file and line number where the failure occurred.
2. **Reproduce Locally**: Run the project's native validation command in the workspace to confirm the failure.
3. **Apply Minimal Fix**: Modify the file(s) to resolve the error. Keep modifications strictly limited to fixing the root cause. Do not disable tests or linters.
4. **Verify Locally**: Re-run the validation command and ensure it passes cleanly with exit code 0.
5. **Commit & Push**:
   - Ensure git author is `aravind15b <aravind15b@test.com>`.
   - Stage modified files and create a conventional commit: `git commit -m "fix({{LANGUAGE}}): resolve {{ERROR_SUMMARY}}"`.
   - Push to `{{HEAD_BRANCH}}` or open a fix PR using `gh pr create`.
```

---

## 4. Multi-Language Diagnostic & Remediation Matrix

OpenHands uses this reference table to map console errors to root causes and fixes across any language:

| Technology | Typical Console Log Signatures | Root Cause | Remediation & Local Verification |
| :--- | :--- | :--- | :--- |
| **Terraform / HCL** | `Error: Unsupported block type`<br>`on <file>.tf line N`<br>`missing required argument`<br>`terraform fmt -check` | Missing closing brace `}` in previous block, typo in attribute name, or unformatted code. | 1. Add missing closing brace `}`.<br>2. Supply missing arguments.<br>3. Run `terraform fmt -recursive && terraform validate`. |
| **Python** | `SyntaxError: unexpected EOF while parsing`<br>`IndentationError: unexpected indent`<br>`ModuleNotFoundError: No module named 'X'`<br>`FAILED tests/test_*.py` | Syntax typo, inconsistent tab/space indentation, missing import, or broken test assertion. | 1. Fix syntax/indentation.<br>2. Add missing import.<br>3. Verify with `python -m py_compile <file>` and `pytest tests/`. |
| **TypeScript / Node** | `error TS2304: Cannot find name 'X'`<br>`Cannot find module 'X'`<br>`ESLint: ... is not defined` | Missing type declaration, missing import, uninstalled dependency, or ESLint rule violation. | 1. Fix type or import statement.<br>2. Run `npx tsc --noEmit` and `npm test`. |
| **Go** | `undefined: FunctionName`<br>`imported and not used: "fmt"`<br>`cannot use X (type A) as type B` | Missing/unused import, variable scope issue, or type mismatch. | 1. Remove unused imports or fix signature.<br>2. Verify with `go vet ./...` and `go test ./...`. |
| **Java / Maven / Gradle** | `[ERROR] cannot find symbol`<br>`package com.example does not exist`<br>`BUILD FAILURE` | Missing import, method signature mismatch, or incorrect dependency coordinate. | 1. Import missing package/symbol.<br>2. Run `mvn test-compile` or `./gradlew compileJava`. |
| **Rust** | `error[E0382]: use of moved value`<br>`error[E0425]: cannot find value 'x' in this scope` | Ownership/borrowing violation or undeclared variable. | 1. Clone or borrow reference as appropriate.<br>2. Run `cargo check && cargo test`. |
| **Docker** | `unknown instruction: ...`<br>`COPY failed: file not found`<br>`returned a non-zero code: 1` | Invalid Dockerfile instruction or bad file path relative to context. | 1. Correct syntax/instruction.<br>2. Validate Dockerfile path references. |
| **YAML / K8s / CI** | `mapping values are not allowed here`<br>`did not find expected key`<br>`syntax error: found character that cannot start any token` | Indentation inconsistency (tabs vs spaces) or missing colon. | 1. Indent with 2 spaces.<br>2. Validate with python yaml parser: `python3 -c "import yaml; yaml.safe_load(open('file.yml'))"`. |
| **Shell / Bash** | `syntax error near unexpected token`<br>`command not found`<br>`unary operator expected` | Unclosed `if`/`fi` or `for`/`done` block, unquoted variable expansions. | 1. Quote variables (`"$VAR"`).<br>2. Check syntax with `bash -n <script.sh>`. |

---

## 5. Ready-To-Test Real Example: `RTC12-Test/terraform_child` PR #1

This example is pre-filled with the active failing PR in `RTC12-Test/terraform_child`. You can feed this directly into OpenHands to test remediation:

```markdown
# TASK: Fix Failing CI Pipeline on RTC12-Test/terraform_child PR #1

### Repository Context:
- **Repository**: `RTC12-Test/terraform_child`
- **PR Number**: `#1`
- **PR Title**: `intial commit`
- **Head Branch**: `feature/test`
- **Base Branch**: `main`
- **CI Label**: `ci_terraform`
- **Failing Workflow**: `Terraform Build`
- **Failing Job**: `Terraform Validate`

---

### Failed CI Console Log Excerpt:
```text
Terraform Validate	2026-09-07T05:52:35.0463394Z │   on modules/ec2/variables.tf line 21, in variable "project_name":
Terraform Validate	2026-09-07T05:52:35.0464337Z │   21: variable "instance_type" {
Terraform Validate	2026-09-07T05:52:35.0464968Z │ 
Terraform Validate	2026-09-07T05:52:35.0465631Z │ Blocks of type "variable" are not expected here.
Terraform Validate	2026-09-07T05:52:35.0466336Z ╵
Terraform Validate	2026-09-07T05:52:35.0467499Z │ Error: Unsupported block type
Terraform Validate	2026-09-07T05:52:35.0469884Z │   on modules/ec2/variables.tf line 27, in variable "project_name":
Terraform Validate	2026-09-07T05:52:35.0487860Z │   27: variable "ami_id" {
Terraform Validate	2026-09-07T05:52:35.0493415Z │ Blocks of type "variable" are not expected here.
```

---

### Instructions for OpenHands:
1. Switch to repository `RTC12-Test/terraform_child` on branch `feature/test`.
2. Inspect `modules/ec2/variables.tf`.
   Notice line 4 where `variable "project_name"` is missing its closing brace `}`:
   ```hcl
   variable "project_name" {
     description = "Name of the project"
     type        = string
   # Missing '}' here!
   
   variable "environment" {
   ```
3. Add the closing `}` to properly close the `project_name` variable block.
4. Run `terraform fmt -check` and `terraform validate` to verify it passes with exit code 0.
5. Commit and push the fix:
   ```bash
   git config user.name "aravind15b"
   git config user.email "aravind15b@test.com"
   git add modules/ec2/variables.tf
   git commit -m "fix(terraform): close variable project_name block in modules/ec2/variables.tf"
   git push origin feature/test
   ```
```

---

## 6. How to Test in OpenHands

### Running with OpenHands CLI:
```bash
# In target repo:
openhands --prompt "$(cat /path/to/copoilot-central/prompts.md)"
```

### Running with OpenHands Docker:
```bash
docker run -it --rm \
  -e SANDBOX_USER_ID=$(id -u) \
  -e WORKSPACE_BASE=$(pwd) \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $(pwd):/workspace \
  -p 3000:3000 \
  ghcr.io/all-hands-ai/openhands:latest
```
Copy and paste **Prompt 1**, **Prompt 2**, or the **Ready-To-Test Real Example** into the OpenHands web UI.
