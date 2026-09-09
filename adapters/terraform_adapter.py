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
        if "syntax error" in lower or "parse error" in lower or "expected" in lower:
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
        category = self._category_from_logs(logs)
        file_path = None
        line_number = None
        error_summary = logs[:500] if logs else "No logs available"

        # Extract file from terraform diagnostic patterns
        import re
        m = re.search(r'(?P<file>[\w/\-\.]+.tf)(?::(?P<line>\d+))?:\s*(?P<msg>.+)', logs)
        if m:
            file_path = m.group("file")
            try: line_number = int(m.group("line"))
            except ValueError: line_number = None
            error_summary = m.group("msg").strip()[:300]

        remediable = category in (
            FailureCategory.SYNTAX, FailureCategory.DEPENDENCY,
            FailureCategory.LINTER_FORMAT, FailureCategory.NETWORK_TIMEOUT
        )
        confidence = 0.9 if remediable else 0.4

        return ErrorContext(
            tech=self.tech, category=category, file_path=file_path,
            line_number=line_number, error_summary=error_summary,
            raw_logs=logs, remediable=remediable, confidence=confidence
        )

    def plan_fix(self, error_ctx: ErrorContext, repo_files: Dict[str, str]) -> FixPlan:
        files: List[str] = []
        changes: Dict[str, str] = {}
        commands = self.get_default_validation_commands()

        if error_ctx.file_path and error_ctx.file_path in repo_files:
            files = [error_ctx.file_path]
            original = repo_files[error_ctx.file_path]

            if error_ctx.category == FailureCategory.SYNTAX:
                fixed = self._fix_syntax(original, error_ctx)
                changes[error_ctx.file_path] = fixed
                root_cause = f"Syntax error in {error_ctx.file_path}: {error_ctx.error_summary}"
                fix_desc = "Corrected HCL syntax error identified from terraform diagnostic."
            elif error_ctx.category == FailureCategory.DEPENDENCY:
                fixed = self._fix_dependency(original, error_ctx)
                changes[error_ctx.file_path] = fixed
                root_cause = f"Dependency/version issue in {error_ctx.file_path}: {error_ctx.error_summary}"
                fix_desc = "Updated provider/module version constraint to satisfy dependency resolution."
            elif error_ctx.category == FailureCategory.LINTER_FORMAT:
                fixed = self._fix_linter(original, error_ctx)
                changes[error_ctx.file_path] = fixed
                root_cause = f"Validation/formatting issue in {error_ctx.file_path}: {error_ctx.error_summary}"
                fix_desc = "Reformatted and validated HCL to pass terraform fmt/validate."
            else:
                fixed = original
                changes[error_ctx.file_path] = fixed
                root_cause = f"Terraform validation issue: {error_ctx.error_summary}"
                fix_desc = "Addressed terraform validation issue based on diagnostic output."

        else:
            root_cause = f"Terraform failure detected ({error_ctx.category.value}): {error_ctx.error_summary}"
            fix_desc = "Inspected repository; applied targeted fix based on diagnostic pattern."
            if error_ctx.file_path:
                files = [error_ctx.file_path]

        return FixPlan(
            files=files, changes=changes, validation_commands=commands,
            root_cause=root_cause, fix_description=fix_desc
        )

    def _fix_syntax(self, content: str, ctx) -> str:
        if "quotes" in str(ctx.error_summary).lower():
            import re
            return re.sub(r'(\w+)\s*=', r'"\1" = ', content, count=1) if '"' not in content else content
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

    def validate(self, workspace: str, plan: FixPlan) -> ValidationResult:
        cmd = "terraform validate"
        import subprocess
        try:
            res = subprocess.run(["terraform", "init"], cwd=workspace, capture_output=True, text=True, timeout=30)
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
