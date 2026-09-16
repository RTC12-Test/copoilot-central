"""Unit tests for the pluggable AI model layer (no network/CLI calls)."""
import unittest
from unittest import mock


class TestModelFactory(unittest.TestCase):
    def test_default_is_copilot(self):
        from ai import get_ai_model
        m = get_ai_model({"ai": {"model": {"name": "copilot"}}})
        self.assertEqual(m.name, "copilot")

    def test_env_override_takes_precedence(self):
        import os
        from ai import get_ai_model
        with mock.patch.dict(os.environ, {"CI_AI_MODEL": "openai"}, clear=False):
            m = get_ai_model({"ai": {"model": {"name": "copilot"}}})
            self.assertEqual(m.name, "openai")

    def test_openai_selection(self):
        import os
        from ai import get_ai_model
        with mock.patch.dict(os.environ, {}, clear=False):
            m = get_ai_model({"ai": {"model": {"name": "openai"}}})
            self.assertEqual(m.name, "openai")

    def test_anthropic_selection(self):
        from ai import get_ai_model
        m = get_ai_model({"ai": {"model": {"name": "anthropic"}}})
        self.assertEqual(m.name, "anthropic")


class TestAIMockModel(unittest.TestCase):
    def test_mock_model_plug_and_play(self):
        from ai.base import AIModel

        class FakeModel(AIModel):
            name = "fake"

            def analyze_logs(self, logs, context=None):
                return "root cause"

            def suggest_fix(self, file_path, content, analysis, context=None):
                return "FIXED CONTENT"

            def is_available(self):
                return True

        m = FakeModel()
        self.assertTrue(m.is_available())
        self.assertEqual(m.analyze_logs("logs"), "root cause")
        self.assertEqual(m.suggest_fix("a.py", "x", "rc"), "FIXED CONTENT")

    def test_factory_returns_ai_model_interface(self):
        from ai import get_ai_model
        from ai.base import AIModel
        m = get_ai_model({"ai": {"model": {"name": "copilot"}}})
        self.assertIsInstance(m, AIModel)


class TestParseRepoSelection(unittest.TestCase):
    def test_parses_clean_array(self):
        from ai.base import parse_repo_selection
        self.assertEqual(
            parse_repo_selection('["RTC12-Test/terraform_child","RTC12-Test/python_child"]'),
            ["RTC12-Test/terraform_child", "RTC12-Test/python_child"])

    def test_parses_array_with_prose_and_fences(self):
        from ai.base import parse_repo_selection
        text = ('Here are the repos:\n```json\n'
                '["RTC12-Test/terraform_child", "RTC12-Test/python_child"]\n'
                '```\nManaged to pick.')
        self.assertIn("RTC12-Test/terraform_child", parse_repo_selection(text))

    def test_parses_object_items(self):
        from ai.base import parse_repo_selection
        text = '[{"full_name": "RTC12-Test/a"}, {"repo": "RTC12-Test/b"}]'
        self.assertEqual(parse_repo_selection(text),
                         ["RTC12-Test/a", "RTC12-Test/b"])

    def test_returns_empty_on_garbage(self):
        from ai.base import parse_repo_selection
        self.assertEqual(parse_repo_selection("no array here"), [])
        self.assertEqual(parse_repo_selection(""), [])


class TestSelectRepos(unittest.TestCase):
    def test_base_passthrough_keeps_all(self):
        from ai.base import AIModel

        class M(AIModel):
            name = "m"
            def analyze_logs(self, logs, context=None): return ""
            def suggest_fix(self, f, c, a, context=None): return ""
            def is_available(self): return True

        cands = [{"repo_key": "RTC12-Test/a", "name": "a"},
                 {"repo_key": "RTC12-Test/b", "name": "b"}]
        self.assertEqual(M().select_repos(cands),
                         ["RTC12-Test/a", "RTC12-Test/b"])

    def test_copilot_select_repos_parses(self):
        import os
        from ai.copilot_model import CopilotCLIModel
        with mock.patch.object(CopilotCLIModel, "_run",
                               return_value='["RTC12-Test/terraform_child"]'):
            m = CopilotCLIModel()
            out = m.select_repos([{"repo_key": "RTC12-Test/terraform_child"},
                                  {"repo_key": "RTC12-Test/other"}])
        self.assertEqual(out, ["RTC12-Test/terraform_child"])

    def test_copilot_select_run_parses(self):
        from ai.copilot_model import CopilotCLIModel
        from core.models import CIEvent
        cands = [
            CIEvent(repo="RTC12-Test/asd2", run_id=11, workflow_name="W",
                    job_name="J", broken_branch="main", head_sha="a",
                    updated_at="2026-09-14T06:30:00Z"),
            CIEvent(repo="RTC12-Test/terraform_child", run_id=22, workflow_name="W",
                    job_name="J", broken_branch="main", head_sha="b",
                    updated_at="2026-09-14T06:31:00Z"),
        ]
        with mock.patch.object(CopilotCLIModel, "_run",
                               return_value='{"repo": "RTC12-Test/asd2", "run_id": 11}'):
            m = CopilotCLIModel()
            self.assertEqual(m.select_run(cands),
                             "RTC12-Test/asd2#11")

    def test_default_select_run_falls_back_to_none(self):
        from ai.provider_models import OpenAIModel
        m = OpenAIModel()
        with mock.patch.object(m, "is_available", return_value=False):
            self.assertIsNone(m.select_run([]))


class TestParsePrContent(unittest.TestCase):
    def test_parses_json_with_fences(self):
        from ai.base import parse_pr_content
        text = ('```json\n{"title": "Fix: syntax error", '
                '"body": "## Summary\\nFixed the module block."}\n```')
        out = parse_pr_content(text)
        self.assertEqual(out["title"], "Fix: syntax error")
        self.assertIn("## Summary", out["body"])

    def test_empty_on_garbage(self):
        from ai.base import parse_pr_content
        self.assertEqual(parse_pr_content("nope"), {})
        self.assertEqual(parse_pr_content(""), {})

    def test_copilot_create_pr_content(self):
        from core.models import CIEvent
        from ai.copilot_model import CopilotCLIModel
        from adapters.base import FixPlan
        resp = ('{"title": "Fix terraform module placement", '
                '"body": "## Root Cause\\nnested module"}')
        with mock.patch.object(CopilotCLIModel, "_run", return_value=resp):
            m = CopilotCLIModel()
            ev = CIEvent(repo="RTC12-Test/terraform_child", run_id=7,
                         workflow_name="W", job_name="J",
                         broken_branch="feature/tas", head_sha="x")
            plan = FixPlan(files=["main.tf"], root_cause="nested module",
                           fix_description="extracted module", tech="terraform")
            title, body = m.create_pr_content(plan, ev, {"tech": "terraform"})
        self.assertEqual(title, "Fix terraform module placement")
        self.assertIn("nested module", body)


class TestFixWorkspace(unittest.TestCase):
    def test_copilot_fix_workspace_reads_changed_files(self):
        import tempfile
        from ai.copilot_model import CopilotCLIModel
        with tempfile.TemporaryDirectory() as ws:
            import os
            os.makedirs(os.path.join(ws, ".git"))
            task = ["main.tf"]
            with mock.patch.object(CopilotCLIModel, "_run",
                                   return_value="done"), \
                 mock.patch.object(CopilotCLIModel, "_read_changed_files",
                                   return_value={"main.tf": 'module "vpc" {}'}):
                m = CopilotCLIModel()
                changed = m.fix_workspace(ws, "logs", "analysis", {"tech": "terraform"})
        self.assertEqual(changed, {"main.tf": 'module "vpc" {}'})

    def test_copilot_fix_workspace_no_git_returns_empty(self):
        import tempfile
        from ai.copilot_model import CopilotCLIModel
        with tempfile.TemporaryDirectory() as ws:
            with mock.patch.object(CopilotCLIModel, "_run", return_value="done"):
                m = CopilotCLIModel()
                self.assertEqual(m.fix_workspace(ws, "logs", "analysis"), {})


if __name__ == "__main__":
    unittest.main()
