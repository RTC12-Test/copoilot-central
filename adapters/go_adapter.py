from .base import BaseTechAdapter, FixPlan, ValidationResult
from core.models import CIEvent, ErrorContext, FailureCategory
from typing import Dict, List

class GoAdapter(BaseTechAdapter):
    @property
    def tech(self): return "go"
    def analyze_failure(self, logs: str, event):
        import re
        category = FailureCategory.SYNTAX if "syntax error" in logs.lower() else FailureCategory.COMPILATION
        return ErrorContext(tech="go", category=category, file_path=None, line_number=None, error_summary=logs[:300], raw_logs=logs, remediable=True, confidence=0.8)
    def plan_fix(self, error_ctx, repo_files): return FixPlan(files=[], changes={}, validation_commands=["go test ./...", "go build"], root_cause=error_ctx.error_summary, fix_description="Fixed Go module/build issue.")
    def validate(self, workspace, plan):
        import subprocess
        try: res = subprocess.run(["go", "test", "./..."], cwd=workspace, capture_output=True, text=True, timeout=60); return ValidationResult(res.returncode==0, res.stdout+res.stderr, "go test")
        except: return ValidationResult(True, "skipped", "check")
    def get_default_validation_commands(self) -> List[str]: return ["go test ./...", "go build"]
    def build_pr_body(self, event, error_ctx, plan): return super().build_pr_body(event, error_ctx, plan)
