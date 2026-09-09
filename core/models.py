from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


class FailureCategory(str, Enum):
    SYNTAX = "syntax"
    COMPILATION = "compilation"
    TEST_FAILURE = "test_failure"
    DEPENDENCY = "dependency"
    LINTER_FORMAT = "linter_format"
    INFRA_SECRET = "infra_secret"
    NETWORK_TIMEOUT = "network_timeout"
    UNKNOWN = "unknown"


@dataclass
class CIEvent:
    repo: str
    run_id: int
    workflow_name: str
    job_name: str
    broken_branch: str
    head_sha: str
    labels: List[str] = field(default_factory=list)
    html_url: str = ""
    updated_at: str = ""


@dataclass
class ErrorContext:
    tech: str
    category: FailureCategory
    file_path: Optional[str]
    line_number: Optional[int]
    error_summary: str
    raw_logs: str
    remediable: bool = True
    confidence: float = 1.0
    extra_details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FixResult:
    success: bool
    tech: str
    broken_branch: str
    fix_branch: str
    files_modified: List[str] = field(default_factory=list)
    root_cause: str = ""
    fix_description: str = ""
    verification_output: str = ""
    pr_url: Optional[str] = None
    error_message: Optional[str] = None
