"""Unit tests for the pluggable monitoring layer (no network/CLI calls)."""
import unittest
from unittest import mock

from core.models import CIEvent, FailureCategory


def _event():
    return CIEvent(repo="RTC12-Test/terraform_child", run_id=42,
                   workflow_name="Terraform Build", job_name="Feature/tas",
                   broken_branch="feature/tas", head_sha="x")


class TestHeuristicMonitor(unittest.TestCase):
    def test_terraform_syntax(self):
        from monitoring import HeuristicFailureMonitor
        logs = 'Error: Unexpected "module" block on main.tf line 8'
        a = HeuristicFailureMonitor().analyze(_event(), logs,
                                              {"labels": ["ci_terraform"]}, "terraform")
        self.assertEqual(a.source, "heuristic")
        self.assertEqual(a.tech, "terraform")
        self.assertTrue(a.root_cause)

    def test_unknown_tech_falls_back_to_unknown_category(self):
        from monitoring import HeuristicFailureMonitor
        a = HeuristicFailureMonitor().analyze(_event(), "random noise",
                                              {"labels": []}, "unknown")
        self.assertEqual(a.category, FailureCategory.UNKNOWN)


class FakeModel(object):
    name = "fake"

    def is_available(self):
        return True

    def analyze_logs(self, logs, context=None):
        return ("1. **Root cause:** The module block is nested inside locals "
                "in main.tf.\n"
                "2. **Category:** syntax\n"
                "3. **Affected file(s) and line(s):** `main.tf:8`")


class BadModel(FakeModel):
    def is_available(self):
        return False


class TestAIMonitor(unittest.TestCase):
    def test_ai_parse_category_and_root_cause(self):
        from monitoring import AIFailureMonitor
        a = AIFailureMonitor(ai_model=FakeModel()).analyze(
            _event(), "some logs", {"labels": ["ci_terraform"]}, "terraform")
        self.assertEqual(a.source, "ai")
        self.assertEqual(a.category, FailureCategory.SYNTAX)
        self.assertIn("module block", a.root_cause)

    def test_ai_unavailable_falls_back_to_heuristic(self):
        from monitoring import AIFailureMonitor
        a = AIFailureMonitor(ai_model=BadModel()).analyze(
            _event(), "random noise", {"labels": []}, "unknown")
        self.assertEqual(a.source, "heuristic")

    def test_ai_empty_response_falls_back(self):
        from monitoring import AIFailureMonitor

        class EmptyModel(FakeModel):
            def analyze_logs(self, logs, context=None):
                return "   "

        a = AIFailureMonitor(ai_model=EmptyModel()).analyze(
            _event(), "random noise", {"labels": []}, "unknown")
        self.assertEqual(a.source, "heuristic")


class TestMonitorFactory(unittest.TestCase):
    def test_default_heuristic(self):
        from monitoring import get_failure_monitor
        self.assertEqual(get_failure_monitor({}).name, "heuristic")

    def test_ai_selected_via_config(self):
        import os
        from monitoring import get_failure_monitor
        with mock.patch.dict(os.environ, {}, clear=False):
            m = get_failure_monitor({"monitoring": {"provider": "ai"}}, FakeModel())
        self.assertEqual(m.name, "ai")

    def test_env_override(self):
        import os
        from monitoring import get_failure_monitor
        with mock.patch.dict(os.environ, {"CI_MONITOR_PROVIDER": "ai"}, clear=False):
            m = get_failure_monitor({"monitoring": {"provider": "heuristic"}}, FakeModel())
        self.assertEqual(m.name, "ai")


if __name__ == "__main__":
    unittest.main()