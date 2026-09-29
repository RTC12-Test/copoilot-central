"""Tests for RepositoryManager commit/push resilience."""
import os
import subprocess
import tempfile
import unittest

from core.repository_manager import RepositoryManager


def _git(ws, *args):
    subprocess.run(["git", "-C", ws, *args], check=True,
                   capture_output=True, text=True)


def _make_repo(base, remote_name="remote.git"):
    """Create a workspace repo with an initial commit + bare origin; return both."""
    ws = os.path.join(base, "ws")
    remote = os.path.join(base, remote_name)
    os.makedirs(ws)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "test@test.com")
    _git(ws, "config", "user.name", "test")
    with open(os.path.join(ws, "a.txt"), "w") as f:
        f.write("one\n")
    _git(ws, "add", ".")
    _git(ws, "commit", "-q", "-m", "init", "--no-verify")
    subprocess.run(["git", "init", "--bare", "-q", remote], check=True)
    _git(ws, "remote", "add", "origin", remote)
    return ws, remote


class TestRepositoryManagerResilience(unittest.TestCase):

    def test_commit_and_push_skips_missing_planned_file(self):
        with tempfile.TemporaryDirectory() as base:
            ws, remote = _make_repo(base)
            _git(ws, "checkout", "-qb", "ai-fix/t-1")
            with open(os.path.join(ws, "a.txt"), "w") as f:
                f.write("one\ntwo\n")  # tracked modification
            ok = RepositoryManager(base_workspace=base).commit_and_push(
                ws, "ai-fix/t-1", "fix", files=["a.txt", "phantom.txt"])
            self.assertTrue(ok)
            out = subprocess.run(["git", "ls-remote", remote, "ai-fix/t-1"],
                                 capture_output=True, text=True, check=True)
            self.assertIn("ai-fix/t-1", out.stdout)
            _git(ws, "checkout", "-q", "ai-fix/t-1")
            with open(os.path.join(ws, "a.txt")) as f:
                self.assertIn("two", f.read())

    def test_commit_and_push_falls_back_when_all_planned_files_missing(self):
        with tempfile.TemporaryDirectory() as base:
            ws, remote = _make_repo(base)
            _git(ws, "checkout", "-qb", "ai-fix/t-2")
            with open(os.path.join(ws, "a.txt"), "w") as f:
                f.write("one\ntwo\n")
            ok = RepositoryManager(base_workspace=base).commit_and_push(
                ws, "ai-fix/t-2", "fix", files=["missing-a.txt", "missing-b.txt"])
            self.assertTrue(ok)
            out = subprocess.run(["git", "ls-remote", remote, "ai-fix/t-2"],
                                 capture_output=True, text=True, check=True)
            self.assertIn("ai-fix/t-2", out.stdout)

    def test_commit_and_push_no_changes_returns_false(self):
        with tempfile.TemporaryDirectory() as base:
            ws, _ = _make_repo(base)
            ok = RepositoryManager(base_workspace=base).commit_and_push(
                ws, "ai-fix/t-3", "fix", files=["a.txt"])
            self.assertFalse(ok)  # nothing staged -> no commit


if __name__ == "__main__":
    unittest.main()