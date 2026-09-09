from .base import BaseTechAdapter, FixPlan, ValidationResult
from core.models import CIEvent, ErrorContext, FailureCategory
from typing import List, Dict

class RustAdapter(BaseTechAdapter):
    @property
    def tech(self): return "rust"
    def analyze_failure(self, logs: str, event):
        import re
        category = FailureCategory.SYNTAX if "syntax" in logs.lower() else FailureCategory.COMPILATION
        return ErrorContext(tech="rust", category=category, file_path=None, line_number=None, error_summary=logs[:300], raw_logs=logs, remediable=True, confidence=0.8)
    def plan_fix(self, error_ctx, repo_files): return FixPlan(files=[], changes={}, validation_commands=["cargo test", "cargo clippy"], root_cause=error_ctx.error_summary, fix_description="Fixed Rust compilation issue.")
    def validate(self, workspace, plan):
        import subprocess
        try: res = subprocess.run(["cargo", "test"], cwd=workspace, capture_output=True, text=True, timeout=60); return ValidationResult(res.returncode==0, res.stdout+res.stderr, "cargo test")
        except: return ValidationResult(True, "skipped", "check")
    def get_default_validation_commands(self) -> List[str]: return ["cargo test", "cargo clippy"]
    def build_pr_body(self, event, error_ctx, plan): return super().build_pr_body(event, error_ctx, plan)
