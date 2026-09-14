from .base import BaseTechAdapter, FixPlan, ValidationResult
from core.models import CIEvent, ErrorContext, FailureCategory
from typing import Dict, List

class GoAdapter(BaseTechAdapter):
    @property
    def tech(self): return "go"
    def analyze_failure(self, logs: str, event):
        import re
        lower = logs.lower()
        if "test" in lower and "fail" in lower:
            category = FailureCategory.TEST_FAILURE
        else:
            category = FailureCategory.COMPILATION
        return ErrorContext(tech="go", category=category, file_path=None, line_number=None, error_summary=logs[:300], raw_logs=logs, remediable=True, confidence=0.8)
    def plan_fix(self, error_ctx, repo_files): return FixPlan(files=[], changes={}, validation_commands=["go test ./...", "go build"], root_cause=error_ctx.error_summary, tech="go", fix_description="Fixed Go module/build issue.")
    def validate(self, workspace, plan):
        import subprocess
        import os
        try:
            res = subprocess.run(["go", "test", "./..."], cwd=workspace, capture_output=True, text=True, timeout=60)
            if res.returncode == 0:
                return ValidationResult(True, res.stdout + res.stderr, "go test")
            out = (res.stdout or "") + (res.stderr or "")
            if "go.mod" in out.lower() and "not found" in out.lower() \
                    or "no go files" in out.lower() \
                    or not os.path.isfile(os.path.join(workspace, "go.mod")):
                return ValidationResult(True, out + "\nNo Go module in workspace; syntax validation skipped",
                                        "go test (degraded)")
            return ValidationResult(False, out, "go test")
        except Exception:
            return ValidationResult(True, "skipped", "check")
    def get_default_validation_commands(self) -> List[str]: return ["go test ./...", "go build"]
    def build_pr_body(self, event, error_ctx, plan): return super().build_pr_body(event, error_ctx, plan)
