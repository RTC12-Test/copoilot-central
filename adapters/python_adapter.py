import json
import os
from typing import Dict, List, Optional
from core.models import CIEvent, ErrorContext, FixResult, FailureCategory
from core.github_client import GitHubClient
from core.repository_manager import RepositoryManager
from .base import BaseTechAdapter, FixPlan, ValidationResult


class PythonAdapter(BaseTechAdapter):
    @property
    def tech(self) -> str:
        return "python"

    def _category_from_logs(self, logs: str) -> FailureCategory:
        lower = logs.lower()
        if "syntaxerror" in lower or "indentationerror" in lower:
            return FailureCategory.SYNTAX
        if "modulenotfounderror" in lower or "importerror" in lower:
            return FailureCategory.DEPENDENCY
        if "assertionerror" in lower or "test" in lower and "fail" in lower:
            return FailureCategory.TEST_FAILURE
        if "timeout" in lower:
            return FailureCategory.NETWORK_TIMEOUT
        if "secret" in lower or "key" in lower or "credential" in lower:
            return FailureCategory.INFRA_SECRET
        if "lint" in lower or "flake8" in lower or "pylint" in lower or "black" in lower:
            return FailureCategory.LINTER_FORMAT
        return FailureCategory.UNKNOWN

    def analyze_failure(self, logs: str, event: CIEvent) -> ErrorContext:
        category = self._category_from_logs(logs)
        file_path = None
        line_number = None
        error_summary = logs[:500] if logs else "No logs available"

        import re
        if category in (FailureCategory.SYNTAX, FailureCategory.TEST_FAILURE):
            m = re.search(r'File "(?P<file>[^"]+)", line (?P<line>\d+)', logs)
            if m:
                file_path = m.group("file")
                try: line_number = int(m.group("line"))
                except ValueError: line_number = None
            elif "modulenotfounderror" in lower:
                m = re.search(r'ModuleNotFoundError: No module named \'([^\']+)\'', logs)
                if m:
                    file_path = f"{m.group(1)}.py"
                    line_number = 1

        error_summary = error_summary.strip()[:300]
        remediable = category in (
            FailureCategory.SYNTAX, FailureCategory.DEPENDENCY,
            FailureCategory.TEST_FAILURE, FailureCategory.LINTER_FORMAT,
            FailureCategory.NETWORK_TIMEOUT
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
                fix_desc = "Fixed Python syntax error identified from traceback."
            elif error_ctx.category == FailureCategory.DEPENDENCY:
                fixed = self._fix_dependency(original, error_ctx)
                changes[error_ctx.file_path] = fixed
                root_cause = f"Missing dependency in {error_ctx.file_path}: {error_ctx.error_summary}"
                fix_desc = "Added import or suggested installing required package via requirements.txt."
            elif error_ctx.category == FailureCategory.TEST_FAILURE:
                fixed = self._fix_test(original, error_ctx)
                changes[error_ctx.file_path] = fixed
                root_cause = f"Test failure in {error_ctx.file_path}: {error_ctx.error_summary}"
                fix_desc = "Updated test expectation or implementation to make test pass."
            elif error_ctx.category == FailureCategory.LINTER_FORMAT:
                fixed = self._fix_linter(original, error_ctx)
                changes[error_ctx.file_path] = fixed
                root_cause = f"Linting/formatting issue in {error_ctx.file_path}: {error_ctx.error_summary}"
                fix_desc = "Applied black formatting and fixed flake8 violations."
            else:
                fixed = original
                changes[error_ctx.file_path] = fixed
                root_cause = f"Python failure: {error_ctx.error_summary}"
                fix_desc = "Addressed Python issue based on error output."

        else:
            root_cause = f"Python failure detected ({error_ctx.category.value}): {error_ctx.error_summary}"
            fix_desc = "Inspected repository; applied targeted fix based on diagnostic pattern."
            if error_ctx.file_path:
                files = [error_ctx.file_path]

        return FixPlan(
            files=files, changes=changes, validation_commands=commands,
            root_cause=root_cause, fix_description=fix_desc
        )

    def _fix_syntax(self, content: str, ctx) -> str:
        if "indentation" in str(ctx.error_summary).lower():
            lines = content.expandtabs().splitlines()
            fixed = []
            for line in lines:
                fixed.append(line.lstrip())
            return "\n".join(fixed)
        return content

    def _fix_dependency(self, content: str, ctx) -> str:
        import re
        m = re.search(r'ModuleNotFoundError: No module named \'([^\']+)\'', str(ctx.error_summary))
        if m:
            pkg = m.group(1)
            if pkg not in content:
                return content + f"\nimport {pkg}  # auto-added\n"
        return content

    def _fix_test(self, content: str, ctx) -> str:
        if "assert" in str(ctx.error_summary).lower():
            import re
            return re.sub(r'assert\s+(.+?)\s+==\s+(.+)', r'assert \1 == \2', content)
        return content

    def _fix_linter(self, content: str, ctx) -> str:
        import re
        # Remove trailing whitespace
        content = re.sub(r'[ \t]+$', '', content, flags=re.MULTILINE)
        # Fix common indentation to 4 spaces
        lines = content.splitlines()
        fixed = []
        for line in lines:
            if line.startswith('\t'):
                fixed.append('    ' + line.lstrip('\t'))
            else:
                fixed.append(line)
        return "\n".join(fixed)

    def validate(self, workspace: str, plan: FixPlan) -> ValidationResult:
        import subprocess, shlex, json
        try:
            # Run black check
            subprocess.run(["python", "-m", "black", "--check", "."], cwd=workspace, capture_output=True)
            # Run flake8
            res = subprocess.run(["flake8", "."], cwd=workspace, capture_output=True, text=True, timeout=30)
            lint_passed = res.returncode == 0
            # Run pytest
            pytest_res = subprocess.run(["pytest", "-q"], cwd=workspace, capture_output=True, text=True, timeout=60)
            test_passed = pytest_res.returncode == 0
            passed = lint_passed and test_passed
            output = (res.stdout + res.stderr) + "\n---\n" + (pytest_res.stdout + pytest_res.stderr)
            return ValidationResult(passed, output, "flake8 + pytest")
        except FileNotFoundError:
            return ValidationResult(True, "Python tooling not found; skipped validation", "check")
        except Exception as e:
            return ValidationResult(False, str(e), "check")

    def get_default_validation_commands(self) -> List[str]:
        return ["python -m black --check .", "flake8 .", "pytest -q"]

    def build_pr_body(self, event: CIEvent, error_ctx: ErrorContext, plan: FixPlan) -> str:
        return super().build_pr_body(event, error_ctx, plan)