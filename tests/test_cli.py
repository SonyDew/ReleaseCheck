import contextlib
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from releasecheck.cli import InspectionError, inspect, main


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class ReleaseCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "README.md").write_text("Readme\n")
        (self.root / "LICENSE").write_text("License\n")
        (self.root / ".gitignore").write_text("*.tmp\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "initial")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main([str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def checks(self):
        return {x["name"]: x for x in inspect(self.root)}

    def test_clean_repo_warns_without_tag(self):
        rc, out, err = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertIn("WARN tag", out)
        self.assertEqual(err, "")

    def test_strict_warnings_fail(self):
        self.assertEqual(self.run_cli("--strict")[0], 1)

    def test_tag_points_to_head(self):
        git(self.root, "tag", "v1")
        self.assertEqual(self.run_cli("--tag", "v1", "--strict")[0], 0)

    def test_annotated_tag(self):
        git(self.root, "tag", "-a", "v1", "-m", "version")
        self.assertEqual(self.run_cli("--tag", "v1")[0], 0)

    def test_missing_tag_fails(self):
        self.assertEqual(self.run_cli("--tag", "missing")[0], 1)

    def test_tag_on_old_commit_fails(self):
        git(self.root, "tag", "v0")
        (self.root / "README.md").write_text("new\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "update")
        self.assertEqual(self.run_cli("--tag", "v0")[0], 1)

    def test_dirty_tracked_file_fails(self):
        (self.root / "README.md").write_text("modified\n")
        self.assertEqual(self.checks()["worktree"]["status"], "fail")
        self.assertEqual(self.run_cli()[0], 1)

    def test_untracked_file_fails_without_leaking_name(self):
        secret = "secret-value-should-not-print"
        (self.root / secret).write_text("x")
        rc, out, err = self.run_cli("--json")
        self.assertEqual(rc, 1)
        self.assertNotIn(secret, out + err)

    def test_ignored_file_does_not_fail(self):
        (self.root / "ignored.tmp").write_text("x")
        self.assertEqual(self.checks()["worktree"]["status"], "pass")

    def test_missing_readme(self):
        (self.root / "README.md").unlink()
        self.assertEqual(self.checks()["readme"]["status"], "fail")

    def test_empty_license(self):
        (self.root / "LICENSE").write_text("")
        self.assertEqual(self.checks()["license"]["status"], "fail")

    def test_symlink_license_not_accepted(self):
        (self.root / "LICENSE").unlink()
        try:
            (self.root / "LICENSE").symlink_to(self.root / "README.md")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertEqual(self.checks()["license"]["status"], "fail")

    def test_missing_gitignore_warning(self):
        (self.root / ".gitignore").unlink()
        self.assertEqual(self.checks()["gitignore"]["status"], "warn")

    def test_detached_head_warning(self):
        git(self.root, "checkout", "--detach", "-q")
        self.assertEqual(self.checks()["branch"]["status"], "warn")

    def test_nested_directory_rejected(self):
        (self.root / "nested").mkdir()
        with self.assertRaises(InspectionError):
            inspect(self.root / "nested")

    def test_non_repo_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(InspectionError):
                inspect(other)

    def test_nonexistent_path_does_not_leak(self):
        rc, out, err = self.run_cli("--json")
        self.assertEqual(rc, 0)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main([str(self.root / "secret-path"), "--json"])
        self.assertEqual(rc, 2)
        self.assertNotIn("secret-path", output.getvalue())

    def test_json_structure(self):
        rc, out, _ = self.run_cli("--json")
        report = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(len(report["checks"]), 6)

    def test_invalid_tag_name_sanitized(self):
        rc, out, err = self.run_cli("--tag", "secret\nname", "--json")
        self.assertEqual(rc, 2)
        self.assertNotIn("secret", out + err)

    def test_read_only(self):
        before = git(self.root, "status", "--porcelain").stdout
        self.run_cli()
        self.assertEqual(before, git(self.root, "status", "--porcelain").stdout)

    def test_invalid_git_ref_name(self):
        rc, out, err = self.run_cli("--tag", "bad..tag", "--json")
        self.assertEqual(rc, 2)
        self.assertNotIn("bad..tag", out + err)

    def test_git_environment_overrides_ignored(self):
        with patch.dict(os.environ, {"GIT_DIR": "/missing/gitdir", "GIT_WORK_TREE": "/missing/worktree"}):
            self.assertEqual(self.run_cli()[0], 0)

    def test_directory_with_trailing_space(self):
        copy = self.root / "copy "
        shutil.copytree(self.root / ".git", copy / ".git")
        for name in ("README.md", "LICENSE", ".gitignore"):
            shutil.copy2(self.root / name, copy / name)
        self.assertEqual({c["name"]: c["status"] for c in inspect(copy)}["worktree"], "pass")

    def test_json_error_has_no_git_output(self):
        with tempfile.TemporaryDirectory() as other:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main([other, "--json"])
            self.assertEqual(rc, 2)
            report = json.loads(out.getvalue())
            self.assertEqual(report["status"], "error")
            self.assertNotIn(other, out.getvalue())

    def test_unborn_branch_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            git(other, "init", "-q")
            with self.assertRaises(InspectionError):
                inspect(other)

    def test_version(self):
        with self.assertRaises(SystemExit) as raised:
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)


    def test_surrogate_tag_rejected_without_traceback(self):
        for tag in ("\ud800", "prefix\udfff"):
            with self.subTest(tag=repr(tag)):
                rc, out, err = self.run_cli("--tag", tag, "--json")
                self.assertEqual(rc, 2)
                self.assertEqual(json.loads(out)["status"], "error")
                self.assertNotIn("prefix", out + err)

    def test_tag_with_option_like_name_is_not_executed(self):
        rc, out, err = self.run_cli("--tag=--help", "--json")
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["status"], "fail")
        self.assertEqual(err, "")

    def test_dirty_index_detected(self):
        (self.root / "README.md").write_text("staged change\n")
        git(self.root, "add", "README.md")
        self.assertEqual(self.checks()["worktree"]["status"], "fail")

    def test_secret_filename_hidden_in_text_mode(self):
        secret = "private-file-token-abc123"
        (self.root / secret).write_text("value\n")
        rc, out, err = self.run_cli()
        self.assertEqual(rc, 1)
        self.assertNotIn(secret, out + err)

    def test_readme_symlink_not_accepted(self):
        (self.root / "README.md").unlink()
        try:
            (self.root / "README.md").symlink_to(self.root / "LICENSE")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertEqual(self.checks()["readme"]["status"], "fail")

    def test_tag_pointing_to_blob_does_not_pass(self):
        blob = git(self.root, "hash-object", "README.md").stdout.decode().strip()
        git(self.root, "tag", "blob-tag", blob)
        self.assertEqual(self.run_cli("--tag", "blob-tag")[0], 1)

    def test_git_environment_config_override_ignored(self):
        with patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "alias.status",
                                         "GIT_CONFIG_VALUE_0": "!echo hacked"}):
            self.assertEqual(self.run_cli()[0], 0)

    @unittest.skipIf(os.name == "nt", "Executable fixture uses a POSIX shebang")
    def test_git_output_limit_stops_producer(self):
        from releasecheck.cli import _git
        with tempfile.TemporaryDirectory() as tools_dir:
            fake_git = Path(tools_dir) / "git"
            fake_git.write_text("#!/usr/bin/env python3\nimport sys, time\n"
                                "sys.stdout.buffer.write(b'x' * (4 * 1024 * 1024 + 1))\n"
                                "sys.stdout.flush()\ntime.sleep(10)\n")
            fake_git.chmod(0o755)
            with patch.dict(os.environ, {"PATH": tools_dir + os.pathsep + os.environ["PATH"]}):
                start = __import__("time").monotonic()
                with self.assertRaisesRegex(InspectionError, "output exceeds"):
                    _git(self.root, "status")
                self.assertLess(__import__("time").monotonic() - start, 5)


if __name__ == "__main__":
    unittest.main()
