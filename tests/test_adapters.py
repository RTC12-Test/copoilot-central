"""Unit tests for technology-specific adapters."""
import unittest
from core.models import CIEvent, ErrorContext, FailureCategory
from adapters import get_adapter, resolve_tech_from_label, REGISTRY
from adapters.base import FixPlan


def make_event(repo="test-repo", run_id=1, branch="feature/test", logs=""):
    return CIEvent(
        repo=repo, run_id=run_id, workflow_name="CI",
        job_name="test", broken_branch=branch, head_sha="abc123",
        html_url="", updated_at="2026-09-09T00:00:00Z", labels=[]
    )


class BaseAdapterTest(unittest.TestCase):
    """Shared helpers for adapter tests."""
    def assert_failure_analysis(self, adapter, logs, expected_category):
        event = make_event(run_id=1, logs=logs)
        ctx = adapter.analyze_failure(logs, event)
        self.assertEqual(ctx.category, expected_category)
        return ctx


class TestTerraformAdapter(BaseAdapterTest):
    def test_syntax_error_detection(self):
        logs = 'Error: Failed to parse .tf file: line 5, column 10: syntax error'
        ctx = self.assert_failure_analysis(get_adapter("terraform"), logs, FailureCategory.SYNTAX)
        self.assertIn("syntax", ctx.error_summary.lower())

    def test_dependency_error(self):
        logs = "Error: Registry.terraform.io: module not found"
        ctx = self.assert_failure_analysis(get_adapter("terraform"), logs, FailureCategory.DEPENDENCY)

    def test_plan_fix_with_file(self):
        adapter = get_adapter("terraform")
        ctx = ErrorContext(tech="terraform", category=FailureCategory.SYNTAX,
                           file_path="main.tf", line_number=5,
                           error_summary="Invalid HCL syntax", raw_logs="Error at main.tf:5",
                           remediable=True, confidence=0.9)
        repo_files = {"main.tf": "resource \"aws_instance\" \"x\" {"}
        plan = adapter.plan_fix(ctx, repo_files)
        self.assertIsInstance(plan, FixPlan)
        self.assertIn("main.tf", plan.files)
        self.assertTrue(len(plan.files) > 0)
        self.assertTrue(plan.root_cause)
        self.assertTrue(plan.fix_description)
        self.assertTrue(len(plan.validation_commands) > 0)

    def test_validation_commands(self):
        adapter = get_adapter("terraform")
        cmds = adapter.get_default_validation_commands()
        self.assertIn("terraform fmt -check", cmds)
        self.assertIn("terraform validate", cmds)

    def test_extracts_misplaced_module_from_locals(self):
        adapter = get_adapter("terraform")
        broken = (
            'locals {\n'
            '  common_tags = {\n'
            '    Project = var.project_name\n'
            '  }\n'
            '\n'
            '  module "vpc" {}\n'
            '  source = "./modules/vpc"\n'
            '  project_name = var.project_name\n'
            '  tags = local.common_tags\n'
            '}\n'
        )
        ctx = ErrorContext(tech="terraform", category=FailureCategory.SYNTAX,
                           file_path="main.tf", line_number=6,
                           error_summary='Unexpected "module" block', raw_logs="")
        plan = adapter.plan_fix(ctx, {"main.tf": broken})
        fixed = plan.changes.get("main.tf", broken)
        self.assertNotEqual(fixed, broken)
        self.assertIn('module "vpc" {', fixed)
        module_start = fixed.index('module "vpc" {')
        self.assertNotIn('module "vpc"', fixed[:module_start])
        self.assertIn('source = "./modules/vpc"', fixed)

    def test_closes_unclosed_list_bracket(self):
        adapter = get_adapter("terraform")
        broken = 'data "aws_ami" "l" {\n  owners      = ["amazon"\n}\n'
        ctx = ErrorContext(tech="terraform", category=FailureCategory.SYNTAX,
                           file_path="main.tf", line_number=2,
                           error_summary="Expected a comma", raw_logs="")
        plan = adapter.plan_fix(ctx, {"main.tf": broken})
        fixed = plan.changes.get("main.tf", broken)
        self.assertIn('["amazon"]', fixed)

    def test_fixes_missing_brace_space(self):
        adapter = get_adapter("terraform")
        broken = 'resource "aws_sg" "x"{}\n  name = "x"\n'
        ctx = ErrorContext(tech="terraform", category=FailureCategory.SYNTAX,
                           file_path="main.tf", line_number=1,
                           error_summary="Unexpected end", raw_logs="")
        plan = adapter.plan_fix(ctx, {"main.tf": broken})
        fixed = plan.changes.get("main.tf", broken)
        self.assertIn('resource "aws_sg" "x" {', fixed)

    def test_pr_body(self):
        adapter = get_adapter("terraform")
        event = make_event()
        ctx = adapter.analyze_failure("error", event)
        plan = FixPlan(files=["main.tf"], changes={}, validation_commands=["terraform validate"],
                       root_cause="test", fix_description="fix")
        body = adapter.build_pr_body(event, ctx, plan)
        self.assertIn("Automated CI Remediation", body)
        self.assertIn("ci_terraform", body)


class TestPythonAdapter(BaseAdapterTest):
    def test_syntax_error(self):
        logs = "SyntaxError: unexpected indent at line 10"
        ctx = self.assert_failure_analysis(get_adapter("python"), logs, FailureCategory.SYNTAX)

    def test_missing_dependency(self):
        logs = "ModuleNotFoundError: No module named 'requests'"
        ctx = self.assert_failure_analysis(get_adapter("python"), logs, FailureCategory.DEPENDENCY)

    def test_test_failure(self):
        logs = "AssertionError: expected 5 but got 3"
        ctx = self.assert_failure_analysis(get_adapter("python"), logs, FailureCategory.TEST_FAILURE)

    def test_plan_fix_with_file(self):
        adapter = get_adapter("python")
        ctx = ErrorContext(tech="python", category=FailureCategory.SYNTAX,
                           file_path="app.py", line_number=10,
                           error_summary="IndentationError", raw_logs="File app.py line 10",
                           remediable=True, confidence=0.9)
        repo_files = {"app.py": "def foo():\n    pass"}
        plan = adapter.plan_fix(ctx, repo_files)
        self.assertIsInstance(plan, FixPlan)
        self.assertIn("app.py", plan.files)

    def test_validation_commands(self):
        adapter = get_adapter("python")
        cmds = adapter.get_default_validation_commands()
        self.assertIn("python -m black --check .", cmds)
        self.assertIn("flake8 .", cmds)
        self.assertIn("pytest -q", cmds)


class TestJavaAdapter(BaseAdapterTest):
    def test_syntax_error(self):
        logs = "error: cannot find symbol"
        ctx = self.assert_failure_analysis(get_adapter("java"), logs, FailureCategory.SYNTAX)

    def test_missing_dependency(self):
        logs = "package com.example does not exist"
        ctx = self.assert_failure_analysis(get_adapter("java"), logs, FailureCategory.DEPENDENCY)

    def test_plan_fix_with_file(self):
        adapter = get_adapter("java")
        ctx = ErrorContext(tech="java", category=FailureCategory.SYNTAX,
                           file_path="App.java", line_number=5,
                           error_summary="cannot find symbol", raw_logs="App.java:5: error",
                           remediable=True, confidence=0.9)
        repo_files = {"App.java": "public class App {}"}
        plan = adapter.plan_fix(ctx, repo_files)
        self.assertIsInstance(plan, FixPlan)
        self.assertIn("App.java", plan.files)

    def test_validation_commands(self):
        adapter = get_adapter("java")
        cmds = adapter.get_default_validation_commands()
        self.assertIn("./mvnw verify", cmds)
        self.assertIn("./gradlew check", cmds)


class TestGoAdapter(BaseAdapterTest):
    def test_compilation_error(self):
        logs = "syntax error: unexpected :=, expecting ;"
        ctx = self.assert_failure_analysis(get_adapter("go"), logs, FailureCategory.COMPILATION)

    def test_plan_fix(self):
        adapter = get_adapter("go")
        ctx = ErrorContext(tech="go", category=FailureCategory.COMPILATION,
                           file_path=None, line_number=None,
                           error_summary="compilation failed", raw_logs="error",
                           remediable=True, confidence=0.8)
        plan = adapter.plan_fix(ctx, {})
        self.assertIsInstance(plan, FixPlan)
        self.assertIn("go test ./...", plan.validation_commands)
        self.assertIn("go build", plan.validation_commands)


class TestRustAdapter(BaseAdapterTest):
    def test_compilation_error(self):
        logs = "error: cannot find function `foo` in this scope"
        ctx = self.assert_failure_analysis(get_adapter("rust"), logs, FailureCategory.COMPILATION)

    def test_plan_fix(self):
        adapter = get_adapter("rust")
        ctx = ErrorContext(tech="rust", category=FailureCategory.COMPILATION,
                           file_path=None, line_number=None,
                           error_summary="compilation failed", raw_logs="error",
                           remediable=True, confidence=0.8)
        plan = adapter.plan_fix(ctx, {})
        self.assertIsInstance(plan, FixPlan)
        self.assertIn("cargo test", plan.validation_commands)
        self.assertIn("cargo clippy", plan.validation_commands)


class TestLabelResolution(unittest.TestCase):
    def test_resolve_tech_from_label(self):
        self.assertEqual(resolve_tech_from_label("ci_terraform"), "terraform")
        self.assertEqual(resolve_tech_from_label("ci_python"), "python")
        self.assertEqual(resolve_tech_from_label("ci_java"), "java")
        self.assertEqual(resolve_tech_from_label("ci_go"), "go")
        self.assertEqual(resolve_tech_from_label("ci_golang"), "go")
        self.assertEqual(resolve_tech_from_label("ci_rust"), "rust")
        self.assertEqual(resolve_tech_from_label("non_ci_label"), "non_ci_label")

    def test_infer_multiple_techs_from_workflow(self):
        from engine.orchestrator import CIOrchestrator
        o = CIOrchestrator(github_token="")
        # A single workflow covering golang + terraform yields both labels
        self.assertEqual(o._infer_tech_from_workflow("ci-golang-terraform.yaml"),
                         ["terraform", "go"])
        self.assertEqual(o._infer_tech_from_workflow("golang-ci.yaml"), ["go"])
        self.assertEqual(o._infer_tech_from_workflow("terraform-ci.yaml"), ["terraform"])
        self.assertEqual(o._infer_tech_from_workflow("start-ec2.yaml"), [])

    def test_tech_from_run_two_labels(self):
        from engine.orchestrator import CIOrchestrator
        o = CIOrchestrator(github_token="")
        ev = CIEvent(
            repo="asd2", run_id=7, workflow_name="Golang + Terraform CI",
            job_name="golang-check", broken_branch="main", head_sha="abc",
            html_url="", updated_at="2026-09-10T00:00:00Z", labels=[],
            workflow_path=".github/workflows/ci-golang-terraform.yaml",
        )
        self.assertEqual(o._tech_from_run(ev), ["terraform", "go"])
        # Generic workflow name + matching logs also resolves both stacks
        self.assertEqual(o._tech_from_logs(
            "go: go build ./... FAILED\nError: terraform validate main.tf"),
            ["terraform", "go"])

    def test_all_adapters_registered(self):
        for tech in ["terraform", "python", "java", "go", "rust"]:
            self.assertIn(tech, REGISTRY, f"{tech} not in REGISTRY")
            adapter = get_adapter(tech)
            self.assertEqual(adapter.tech, tech)

    def test_multiple_labels(self):
        from engine.label_resolver import resolve_technology_from_labels
        techs = resolve_technology_from_labels(["ci_terraform", "ci_java"])
        self.assertEqual(techs, ["terraform", "java"])

    def test_resolve_tech_log_driven_multi_tech(self):
        from engine.orchestrator import CIOrchestrator
        o = CIOrchestrator(github_token="")
        repo = {"name": "infrastructure", "url": "https://github.com/org/infrastructure",
                "branch": "main", "labels": ["ci_terraform", "ci_java"]}
        # Java failure chooses java even though terraform is listed first
        self.assertEqual(o._resolve_tech(repo, "BUILD FAILURE mvn clean javac Exception in thread junit"), "java")
        # Terraform failure chooses terraform
        self.assertEqual(o._resolve_tech(repo, "Error: Unexpected module block main.tf terraform validate"), "terraform")
        # Ambiguous/empty falls back to first label
        self.assertEqual(o._resolve_tech(repo, ""), "terraform")

    def test_resolve_tech_non_child_repo(self):
        from engine.orchestrator import CIOrchestrator
        o = CIOrchestrator(github_token="")
        repo = {"name": "some_other_repo", "url": "https://github.com/org/some_other_repo",
                "branch": "main", "labels": ["ci_python"]}
        self.assertEqual(o._resolve_tech(repo, "pytest failure"), "python")

    def test_dynamic_discovery_from_org_topics(self):
        from engine.orchestrator import CIOrchestrator

        class FakeClient:
            def resolve_orgs_from_token(self):
                return ["RTC12-Test"]

            def list_org_repos(self, org):
                return [
                    {"name": "terraform_child", "full_name": "RTC12-Test/terraform_child",
                     "url": "https://github.com/RTC12-Test/terraform_child",
                     "default_branch": "main", "topics": ["ci_terraform"]},
                    {"name": "infrastructure", "full_name": "RTC12-Test/infrastructure",
                     "url": "https://github.com/RTC12-Test/infrastructure",
                     "default_branch": "main", "topics": ["ci_terraform", "ci_java"]},
                    {"name": "docs", "full_name": "RTC12-Test/docs",
                     "url": "https://github.com/RTC12-Test/docs",
                     "default_branch": "main", "topics": ["documentation"]},
                ]

        o = CIOrchestrator(github_token="")
        o.client = FakeClient()
        # Enumerate ALL org repos without any code filter (name/topic/push-window);
        # AI-based selection is tested separately. heuristic = keep all candidates.
        o.config["monitoring"]["repo_selection"] = "heuristic"
        repos = o.discover_repos()
        names = [r["name"] for r in repos]
        self.assertIn("infrastructure", names)
        self.assertIn("terraform_child", names)
        self.assertIn("docs", names)
        # Multiple ci_* labels preserved
        infra = next(r for r in repos if r["name"] == "infrastructure")
        self.assertEqual(infra["labels"], ["ci_terraform", "ci_java"])
        # Repo with no ci_* topic is still enumerated; run-level label decides it
        docs = next(r for r in repos if r["name"] == "docs")
        self.assertEqual(docs["labels"], [])

    def test_repo_selection_defaults_to_ai(self):
        from engine.orchestrator import CIOrchestrator
        o = CIOrchestrator(github_token="")
        self.assertEqual(o._repo_selection_provider(), "ai")

    def test_orgs_derived_from_token_when_config_empty(self):
        from engine.orchestrator import CIOrchestrator

        class FakeClient:
            def resolve_orgs_from_token(self):
                return ["RTC12-Test", "AcmeCorp"]

        o = CIOrchestrator(github_token="")
        # ensure config has no explicit organizations for this test
        o.config = {"repositories": []}
        o.client = FakeClient()
        self.assertEqual(o._org_repos(), ["RTC12-Test", "AcmeCorp"])

    def test_run_selection_ai_picks_run(self):
        from engine.orchestrator import CIOrchestrator

        class FakeClient:
            def resolve_orgs_from_token(self):
                return ["RTC12-Test"]

            def list_recent_failed_runs(self, url, limit):
                if url == "u1":
                    return [CIEvent(repo="RTC12-Test/asd2", run_id=11,
                                    workflow_name="W", job_name="J",
                                    broken_branch="main", head_sha="a",
                                    updated_at="2026-09-14T06:30:00Z")]
                return [CIEvent(repo="RTC12-Test/terraform_child", run_id=22,
                                workflow_name="W", job_name="J",
                                broken_branch="main", head_sha="b",
                                updated_at="2026-09-14T06:31:00Z")]

        class AiModel:
            name = "fake"
            def is_available(self): return True
            def select_run(self, candidates, context=None):
                return "RTC12-Test/asd2#11"

        o = CIOrchestrator(github_token="")
        o.client = FakeClient()
        o.ai_model = AiModel()
        chosen = o.get_latest_failed([{"name": "asd2", "url": "u1"},
                                      {"name": "terraform_child", "url": "u2"}])
        self.assertEqual(chosen.run_id, 11)
        self.assertEqual(chosen.repo, "RTC12-Test/asd2")

    def test_run_selection_falls_back_to_latest_when_model_unavailable(self):
        from engine.orchestrator import CIOrchestrator
        from core.models import CIEvent

        class FakeClient:
            def resolve_orgs_from_token(self):
                return ["RTC12-Test"]

            def list_recent_failed_runs(self, url, limit):
                if url == "u1":
                    return [CIEvent(repo="RTC12-Test/asd2", run_id=11,
                                    workflow_name="W", job_name="J",
                                    broken_branch="main", head_sha="a",
                                    updated_at="2026-09-14T06:30:00Z")]
                return [CIEvent(repo="RTC12-Test/terraform_child", run_id=22,
                                workflow_name="W", job_name="J",
                                broken_branch="main", head_sha="b",
                                updated_at="2026-09-14T06:31:00Z")]

        class AiModel:
            name = "fake"
            def is_available(self): return False

        o = CIOrchestrator(github_token="")
        o.client = FakeClient()
        o.ai_model = AiModel()
        chosen = o.get_latest_failed([{"name": "asd2", "url": "u1"},
                                      {"name": "terraform_child", "url": "u2"}])
        # newest by updated_at wins as the code fallback
        self.assertEqual(chosen.run_id, 22)

    def test_run_selection_unmatched_key_falls_back(self):
        from engine.orchestrator import CIOrchestrator
        from core.models import CIEvent

        class FakeClient:
            def resolve_orgs_from_token(self):
                return ["RTC12-Test"]

            def list_recent_failed_runs(self, url, limit):
                return [CIEvent(repo="RTC12-Test/asd2", run_id=11, workflow_name="W",
                                job_name="J", broken_branch="main", head_sha="a",
                                updated_at="2026-09-14T06:30:00Z")]

        class AiModel:
            name = "fake"
            def is_available(self): return True
            def select_run(self, candidates, context=None):
                return "RTC12-Test/nope#999"

        o = CIOrchestrator(github_token="")
        o.client = FakeClient()
        o.ai_model = AiModel()
        chosen = o.get_latest_failed([{"name": "asd2", "url": "u1"}])
        self.assertEqual(chosen.run_id, 11)

    def test_run_selection_scans_all_repos_and_skips_clean_ones(self):
        from engine.orchestrator import CIOrchestrator
        from core.models import CIEvent

        scanned = []

        class FakeClient:
            def resolve_orgs_from_token(self):
                return ["RTC12-Test"]

            def list_recent_failed_runs(self, url, limit):
                scanned.append(url)
                if url == "u1":
                    return [CIEvent(repo="RTC12-Test/asd2", run_id=11,
                                    workflow_name="W", job_name="J",
                                    broken_branch="main", head_sha="a",
                                    updated_at="2026-09-14T06:30:00Z")]
                if url == "u2":
                    return [CIEvent(repo="RTC12-Test/terraform_child", run_id=22,
                                    workflow_name="W", job_name="J",
                                    broken_branch="main", head_sha="b",
                                    updated_at="2026-09-14T06:31:00Z")]
                return []  # repo with no failed runs

        class AiModel:
            name = "fake"
            def is_available(self): return False

        o = CIOrchestrator(github_token="")
        o.client = FakeClient()
        o.ai_model = AiModel()
        repos = [{"name": "asd2", "url": "u1"},
                 {"name": "terraform_child", "url": "u2"},
                 {"name": "docs", "url": "u3"}]
        chosen = o.get_latest_failed(repos)
        # every repo was scanned, including the clean one
        self.assertEqual(sorted(scanned), ["u1", "u2", "u3"])
        # one candidate per failing repo; clean repo contributes nothing
        self.assertEqual(chosen.run_id, 22)


if __name__ == "__main__":
    unittest.main()