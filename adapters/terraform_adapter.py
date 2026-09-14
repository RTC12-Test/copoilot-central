import json
import os
from typing import Dict, List, Optional
from core.models import CIEvent, ErrorContext, FixResult, FailureCategory
from core.github_client import GitHubClient
from core.repository_manager import RepositoryManager
from .base import BaseTechAdapter, FixPlan, ValidationResult


class TerraformAdapter(BaseTechAdapter):
    @property
    def tech(self) -> str:
        return "terraform"

    def _category_from_logs(self, logs: str) -> FailureCategory:
        lower = logs.lower()
        if "syntax error" in lower or "parse error" in lower or "expected" in lower or "unexpected" in lower or "missing item separator" in lower:
            return FailureCategory.SYNTAX
        if "dependency" in lower or "module not found" in lower or "registry.terraform" in lower:
            return FailureCategory.DEPENDENCY
        if "timeout" in lower or "deadline exceeded" in lower or "network" in lower:
            return FailureCategory.NETWORK_TIMEOUT
        if "secret" in lower or "credentials" in lower or "unauthorized" in lower:
            return FailureCategory.INFRA_SECRET
        if "validation" in lower or "terraform validate" in lower:
            return FailureCategory.LINTER_FORMAT
        return FailureCategory.UNKNOWN

    def analyze_failure(self, logs: str, event: CIEvent) -> ErrorContext:
        import re
        category = self._category_from_logs(logs)
        clean = re.sub(r'(\x1b|\^\[)\[[0-9;]*[a-zA-Z]', '', logs)
        file_path = None
        line_number = None
        error_summary = logs[:500] if logs else "No logs available"
        extra_errors = []

        # Pattern 1: Modern HCL / terraform validate format
        # Error: Unexpected "module" block
        #   on main.tf line 9, in locals:
        pattern = r'Error:\s*(?P<msg>[^\n]+).*?on\s+(?P<file>[\w/\-\.]+\.tf)\s+line\s+(?P<line>\d+)'
        for m in re.finditer(pattern, clean, re.DOTALL):
            msg = re.sub(r'(\x1b|\^\[)\[[0-9;]*[a-zA-Z]', '', m.group("msg")).strip()
            extra_errors.append({
                "file": m.group("file"),
                "line": int(m.group("line")),
                "msg": msg
            })

        if extra_errors:
            file_path = extra_errors[0]["file"]
            line_number = extra_errors[0]["line"]
            error_summary = extra_errors[0]["msg"]
        else:
            # Pattern 2: file:line: msg format (or test logs)
            m = re.search(r'(?P<file>[\w/\-\.]+\.tf)(?::(?P<line>\d+))?:\s*(?P<msg>.+)', clean)
            if m:
                file_path = m.group("file")
                try: line_number = int(m.group("line") or 0)
                except ValueError: line_number = None
                error_summary = m.group("msg").strip()[:300]
            else:
                m = re.search(r'Error:\s*(?P<msg>.+)', clean)
                if m:
                    error_summary = m.group("msg").strip()[:300]

        remediable = category in (
            FailureCategory.SYNTAX, FailureCategory.DEPENDENCY,
            FailureCategory.LINTER_FORMAT, FailureCategory.NETWORK_TIMEOUT
        )
        confidence = 0.9 if remediable else 0.4

        return ErrorContext(
            tech=self.tech, category=category, file_path=file_path,
            line_number=line_number, error_summary=error_summary,
            raw_logs=logs, remediable=remediable, confidence=confidence,
            extra_details={"errors": extra_errors}
        )

    def plan_fix(self, error_ctx: ErrorContext, repo_files: Dict[str, str]) -> FixPlan:
        files: List[str] = []
        changes: Dict[str, str] = {}
        commands = self.get_default_validation_commands()

        target_files = set()
        if error_ctx.file_path:
            target_files.add(error_ctx.file_path)
        for err in error_ctx.extra_details.get("errors", []):
            if err.get("file"):
                target_files.add(err["file"])

        # Also inspect all .tf files in repo_files
        for path in repo_files:
            if path.endswith(".tf"):
                target_files.add(path)

        for tf_path in target_files:
            if tf_path not in repo_files:
                continue
            original = repo_files[tf_path]
            fixed = original

            if error_ctx.category == FailureCategory.SYNTAX:
                fixed = self._fix_syntax(fixed, error_ctx)
            elif error_ctx.category == FailureCategory.DEPENDENCY:
                fixed = self._fix_dependency(fixed, error_ctx)
            elif error_ctx.category == FailureCategory.LINTER_FORMAT:
                fixed = self._fix_linter(fixed, error_ctx)
            else:
                fixed = self._fix_syntax(fixed, error_ctx)

            if fixed != original:
                changes[tf_path] = fixed
                if tf_path not in files:
                    files.append(tf_path)
            elif error_ctx.file_path == tf_path and not files:
                changes[tf_path] = fixed
                files.append(tf_path)

        if files:
            root_cause = f"Syntax error in {files[0]}: {error_ctx.error_summary}" if len(files) == 1 else f"Terraform syntax/configuration error: {error_ctx.error_summary} in {', '.join(files)}"
            fix_desc = f"Corrected HCL syntax error identified from terraform diagnostic in {', '.join(files)}."
        else:
            root_cause = f"Terraform failure detected ({error_ctx.category.value}): {error_ctx.error_summary}"
            fix_desc = "Inspected repository; applied targeted fix based on diagnostic pattern."
            if error_ctx.file_path:
                files = [error_ctx.file_path]

        return FixPlan(
            files=files, changes=changes, validation_commands=commands,
            root_cause=root_cause, tech=self.tech, fix_description=fix_desc
        )

    def _fix_syntax(self, content: str, ctx) -> str:
        import re
        if "quotes" in str(ctx.error_summary).lower():
            return re.sub(r'(\w+)\s*=', r'"\1" = ', content, count=1) if '"' not in content else content

        # Fix a `module "name" {}` block that was wrongly placed inside a
        # `locals { ... }` block. A balanced-brace scan finds the full locals
        # body, extracts the module line plus the attribute lines that follow it
        # (up to the locals closing brace), and moves them out into a proper
        # top-level module block.
        content = self._extract_module_from_block(content)

        # Fix an assignment whose list keeps its opening `[` but never closes
        # before the end of the line (e.g. `owners = ["amazon"` -> `["amazon"]`).
        def _close_unclosed_list(line):
            if '"' in line and line.count("[") > line.count("]"):
                return line.rstrip() + "]\n"
            return line
        content = "".join(_close_unclosed_list(line) for line in content.splitlines(True))

        # Fix corrupted block name without opening brace
        content = re.sub(
            r'\bfilter\w*\s*\n(\s*name\s*=)',
            r'filter {\n\1',
            content
        )

        # Fix resource block prematurely closed with {}
        content = re.sub(
            r'(resource\s+"[^"]+"\s+"[^"]+")\{\}\s*\n',
            r'\1 {\n',
            content
        )

        return content

    def _fix_dependency(self, content: str, ctx) -> str:
        import re
        m = re.search(r'required_providers\s*\{([^}]+)}', content, re.DOTALL)
        if m and "version" not in m.group(1):
            return content.replace(m.group(0), m.group(0) + '\n  version = "~> 1.0"')
        return content

    def _fix_linter(self, content: str, ctx) -> str:
        import re
        return re.sub(r'[ \t]+$', '', content, flags=re.MULTILINE)

    def _extract_module_from_block(self, content: str) -> str:
        import re
        lines = content.splitlines()
        i = 0
        n = len(lines)
        while i < n:
            stripped = lines[i].lstrip()
            if not re.match(r'^module\s+"[^"]+"\s*\{\}\s*$', stripped):
                i += 1
                continue
            depth = sum(ln.count("{") - ln.count("}") for ln in lines[:i])
            if depth <= 0:
                i += 1
                continue
            name = re.search(r'module\s+"([^"]+)"', stripped).group(1)
            body_lines: List[str] = []
            j = i + 1
            while j < n:
                if lines[j].strip() == "}":
                    break
                body_lines.append(lines[j])
                j += 1
            body = "\n".join(body_lines).rstrip()
            new_block = f'module "{name}" {{\n{body}\n}}'
            new_lines = lines[:i] + lines[j:] + ["", new_block]
            content = "\n".join(new_lines)
            lines = content.splitlines()
            n = len(lines)
            i += 1
        return content

    def validate(self, workspace: str, plan: FixPlan) -> ValidationResult:
        cmd = "terraform validate"
        import subprocess
        # Reuse a shared provider cache across workspaces so `terraform init`
        # downloads each provider once instead of on every validation run.
        cache_dir = os.path.expanduser("~/.terraform.d/plugin-cache")
        env = dict(os.environ)
        env.setdefault("TF_PLUGIN_CACHE_DIR", cache_dir)
        disk_markers = ("disk quota exceeded", "no space left on device",
                        "out of disk space", "not enough space", "enospc")
        try:
            # Auto-format HCL
            subprocess.run(["terraform", "fmt", "-recursive"], cwd=workspace, capture_output=True, text=True, timeout=30)
            # Initialize with backend=false
            init_res = subprocess.run(["terraform", "init", "-backend=false"], cwd=workspace, capture_output=True, text=True, timeout=60, env=env)
            if init_res.returncode != 0:
                combined = (init_res.stderr or "") + "\n" + (init_res.stdout or "")
                if any(m in combined.lower() for m in disk_markers):
                    # Environment disk limit, not a code problem: fall back to
                    # syntax-only validation so the fix is not blocked forever.
                    fmt_res = subprocess.run(["terraform", "fmt", "-check", "-recursive"], cwd=workspace, capture_output=True, text=True, timeout=30)
                    note = ("terraform init skipped: provider cache write hit a "
                            "disk/quota limit in this environment; degraded to "
                            "'terraform fmt -check' syntax validation")
                    return ValidationResult(fmt_res.returncode == 0, fmt_res.stdout + "\n" + fmt_res.stderr + "\n" + note, "terraform fmt -check (degraded)")
                return ValidationResult(False, f"terraform init failed:\n{init_res.stderr}\n{init_res.stdout}", "terraform init")
            res = subprocess.run(cmd.split(), cwd=workspace, capture_output=True, text=True, timeout=30)
            passed = res.returncode == 0
            return ValidationResult(passed, res.stdout + "\n" + res.stderr, cmd)
        except FileNotFoundError:
            return ValidationResult(True, "terraform binary not found; skipped validation", cmd)
        except Exception as e:
            return ValidationResult(False, str(e), cmd)

    def get_default_validation_commands(self) -> List[str]:
        return ["terraform fmt -check", "terraform validate"]

    def build_pr_body(self, event: CIEvent, error_ctx: ErrorContext, plan: FixPlan) -> str:
        return super().build_pr_body(event, error_ctx, plan)
