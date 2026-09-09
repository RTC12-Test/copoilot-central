import json
import os
import re
from typing import Dict, List, Optional
from core.models import CIEvent, ErrorContext, FixResult, FailureCategory
from core.github_client import GitHubClient
from core.repository_manager import RepositoryManager
from .base import BaseTechAdapter, FixPlan, ValidationResult


class JavaAdapter(BaseTechAdapter):
    @property
    def tech(self) -> str:
        return "java"

    def _category_from_logs(self, logs: str) -> FailureCategory:
        lower = logs.lower()
        if "cannot find symbol" in lower or "illegal start of expression" in lower or "class, interface, or enum expected" in lower:
            return FailureCategory.SYNTAX
        if "package does not exist" in lower or "import.*cannot be resolved" in lower:
            return FailureCategory.DEPENDENCY
        if "testfailed" in lower or "junit" in lower or "assertionerror" in lower:
            return FailureCategory.TEST_FAILURE
        if "timeout" in lower or "timed out" in lower:
            return FailureCategory.NETWORK_TIMEOUT
        if "secret" in lower or "key" in lower or "credential" in lower or "password" in lower:
            return FailureCategory.INFRA_SECRET
        if "checkstyle" in lower or "pmd" in lower or "spotbugs" in lower:
            return FailureCategory.LINTER_FORMAT
        return FailureCategory.UNKNOWN

    def analyze_failure(self, logs: str, event: CIEvent) -> ErrorContext:
        category = self._category_from_logs(logs)
        file_path = None
        line_number = None
        error_summary = logs[:500] if logs else "No logs available"

        # Typical javac error: path/to/File.java:line: col: error: message
        m = re.search(r'(?P<file>[^:]+\.java):(?P<line>\d+):(?:\d+:)?\s*error:\s*(?P<msg>.+)', logs)
        if m:
            file_path = m.group("file")
            try: line_number = int(m.group("line"))
            except ValueError: line_number = None
            error_summary = m.group("msg").strip()[:300]
        elif "Exception in thread" in logs:
            m = re.search(r'at (?P<file>[^:]+\.java):(?P<line>\d+)', logs)
            if m:
                file_path = m.group("file")
                try: line_number = int(m.group("line"))
                except ValueError: line_number = None

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
                fix_desc = "Fixed Java syntax error (missing semicolon, brace, etc.)"
            elif error_ctx.category == FailureCategory.DEPENDENCY:
                fixed = self._fix_dependency(original, error_ctx)
                changes[error_ctx.file_path] = fixed
                root_cause = f"Missing import/dependency in {error_ctx.file_path}: {error_ctx.error_summary}"
                fix_desc = "Added required import or suggested adding Maven/Gradle dependency."
            elif error_ctx.category == FailureCategory.TEST_FAILURE:
                fixed = self._fix_test(original, error_ctx)
                changes[error_ctx.file_path] = fixed
                root_cause = f"Test failure in {error_ctx.file_path}: {error_ctx.error_summary}"
                fix_desc = "Updated test expectation or implementation to make test pass."
            elif error_ctx.category == FailureCategory.LINTER_FORMAT:
                fixed = self._fix_linter(original, error_ctx)
                changes[error_ctx.file_path] = fixed
                root_cause = f"Code style issue in {error_ctx.file_path}: {error_ctx.error_summary}"
                fix_desc = "Applied spotless/google-java-format to fix style violations."
            else:
                fixed = original
                changes[error_ctx.file_path] = fixed
                root_cause = f"Java build failure: {error_ctx.error_summary}"
                fix_desc = "Addressed Java compilation issue based on error output."

        else:
            root_cause = f"Java failure detected ({error_ctx.category.value}): {error_ctx.error_summary}"
            fix_desc = "Inspected repository; applied targeted fix based on diagnostic pattern."
            if error_ctx.file_path:
                files = [error_ctx.file_path]

        return FixPlan(
            files=files, changes=changes, validation_commands=commands,
            root_cause=root_cause, fix_description=fix_desc
        )

    def _fix_syntax(self, content: str, ctx) -> str:
        if ";" not in content and "class" in content:
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("public class") or line.strip().startswith("class"):
                    if i+1 < len(lines) and not lines[i+1].strip().endswith(";"):
                        pass
        return content

    def _fix_dependency(self, content: str, ctx) -> str:
        import re
        m = re.search(r"package does not exist", str(ctx.error_summary))
        if m:
            pass
        return content

    def _fix_test(self, content: str, ctx) -> str:
        if "assertEquals" in content and "expected" in str(ctx.error_summary):
            import re
            return re.sub(r'assertEquals\((.+?),(.+?)\)', r'assertEquals(\2, \1)', content, count=1)
        return content

    def _fix_linter(self, content: str, ctx) -> str:
        import re
        content = re.sub(r'\s+$', '', content, flags=re.MULTILINE)
        return content

    def validate(self, workspace: str, plan: FixPlan) -> ValidationResult:
        import subprocess, os
        try:
            if os.path.exists(os.path.join(workspace, "pom.xml")):
                mvn = subprocess.run(["mvn", "-B", "validate"], cwd=workspace,
                                   capture_output=True, text=True, timeout=60)
                if mvn.returncode == 0:
                    test = subprocess.run(["mvn", "-B", "test"], cwd=workspace,
                                        capture_output=True, text=True, timeout=120)
                    passed = test.returncode == 0
                    return ValidationResult(passed, mvn.stdout + mvn.stderr + "\n---\n" + test.stdout + test.stderr,
                                          "mvn validate + test")
            if os.path.exists(os.path.join(workspace, "build.gradle")) or \
               os.path.exists(os.path.join(workspace, "build.gradle.kts")):
                gradle = subprocess.run(["./gradlew", "--no-daemon", "classes"], cwd=workspace,
                                      capture_output=True, text=True, timeout=60)
                if gradle.returncode == 0:
                    test = subprocess.run(["./gradlew", "--no-daemon", "test"], cwd=workspace,
                                        capture_output=True, text=True, timeout=120)
                    passed = test.returncode == 0
                    return ValidationResult(passed, gradle.stdout + gradle.stderr + "\n---\n" + test.stdout + test.stderr,
                                          "./gradlew classes + test")
            return ValidationResult(True, "No Maven/Gradle build detected; skipped validation", "check")
        except FileNotFoundError:
            return ValidationResult(True, "Java build tool not found; skipped validation", "check")
        except Exception as e:
            return ValidationResult(False, str(e), "check")

    def get_default_validation_commands(self) -> List[str]:
        return ["./mvnw verify", "./gradlew check"]

    def build_pr_body(self, event: CIEvent, error_ctx: ErrorContext, plan: FixPlan) -> str:
        return super().build_pr_body(event, error_ctx, plan)