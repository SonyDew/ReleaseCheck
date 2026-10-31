"""Inspect a Git worktree without changing its files."""

from __future__ import annotations

import argparse
import queue
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time

from . import __version__


class InspectionError(Exception):
    """The repository could not be inspected safely."""


GIT_TIMEOUT_SECONDS = 15
GIT_OUTPUT_LIMIT = 4 * 1024 * 1024


def _git(root: Path, *args: str) -> bytes:
    command = ["git", "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false", *args]
    # Do not inherit Git environment overrides from the caller.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
    except (OSError, ValueError, UnicodeError) as exc:
        raise InspectionError("Git could not be executed or timed out") from exc

    # A child of Git can inherit its stdout pipe. Use a daemon reader so a
    # timed-out read cannot hold the CLI open after Git itself is terminated.
    result: queue.Queue[tuple[bytes | None, Exception | None]] = queue.Queue(maxsize=1)

    def read_output() -> None:
        try:
            assert process.stdout is not None
            result.put((process.stdout.read(GIT_OUTPUT_LIMIT + 1), None))
        except (OSError, ValueError) as exc:
            result.put((None, exc))

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        try:
            output, error = result.get(timeout=max(0.001, deadline - time.monotonic()))
        except queue.Empty as exc:
            raise InspectionError("Git could not be executed or timed out") from exc
        if error is not None:
            raise InspectionError("Git could not be executed or timed out") from error
        assert output is not None
        if len(output) > GIT_OUTPUT_LIMIT:
            raise InspectionError("Git output exceeds the inspection limit")
        try:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise InspectionError("Git could not be executed or timed out") from exc
        if process.returncode != 0:
            raise InspectionError("Git could not inspect the repository")
        return output
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        assert process.stdout is not None
        process.stdout.close()


def _document(root: Path, names: tuple[str, ...]) -> bool:
    for name in names:
        candidate = root / name
        try:
            info = candidate.lstat()
        except (OSError, ValueError):
            continue
        if stat.S_ISREG(info.st_mode) and info.st_size > 0:
            return True
    return False


def inspect(path: str | Path, tag: str | None = None) -> list[dict[str, str]]:
    try:
        root = Path(path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise InspectionError("Repository path is not accessible") from exc
    if not root.is_dir():
        raise InspectionError("Repository path is not a directory")
    top = _git(root, "rev-parse", "--show-toplevel").decode("utf-8", "surrogateescape").removesuffix("\n")
    try:
        matches_root = Path(top).resolve() == root
    except (OSError, RuntimeError, ValueError) as exc:
        raise InspectionError("Git returned an invalid worktree path") from exc
    if not matches_root:
        raise InspectionError("Path must point to the worktree root")
    _git(root, "rev-parse", "--verify", "HEAD")
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    status = _git(root, "status", "--porcelain=v1", "--untracked-files=normal", "-z")
    add("worktree", "fail" if status else "pass",
        "Tracked or untracked changes found" if status else "No pending changes found")

    readme = _document(root, ("README.md", "README.rst", "README.txt", "README"))
    license_file = _document(root, ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING"))
    add("readme", "pass" if readme else "fail",
        "Nonempty README found" if readme else "Nonempty README missing")
    add("license", "pass" if license_file else "fail",
        "Nonempty license file found" if license_file else "Nonempty license file missing")
    ignore = _document(root, (".gitignore",))
    add("gitignore", "pass" if ignore else "warn",
        ".gitignore found" if ignore else ".gitignore missing or empty")

    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir")
                   .decode("utf-8", "surrogateescape").removesuffix("\n"))
    active = False
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD",
                   "rebase-apply", "rebase-merge", "sequencer", "BISECT_LOG"):
        try:
            (git_dir / marker).lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise InspectionError("Git operation state could not be inspected") from exc
        active = True
        break
    add("operation", "fail" if active else "pass",
        "Git operation in progress" if active else "No Git operation in progress")

    # A detached HEAD is valid for a release build, but deserves review.
    try:
        _git(root, "symbolic-ref", "--quiet", "HEAD")
        add("branch", "pass", "HEAD is on a branch")
    except InspectionError:
        add("branch", "warn", "HEAD is detached")

    head = _git(root, "rev-parse", "HEAD").strip()
    if tag is not None:
        if not tag or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in tag):
            raise InspectionError("Invalid tag name")
        try:
            _git(root, "check-ref-format", f"refs/tags/{tag}")
        except InspectionError as exc:
            raise InspectionError("Invalid tag name") from exc
        try:
            target = _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}").strip()
        except InspectionError:
            target = b""
        matched = target == head
        add("tag", "pass" if matched else "fail",
            "Requested tag points to HEAD" if matched else "Requested tag does not point to HEAD")
    else:
        tags = _git(root, "tag", "--points-at", "HEAD")
        add("tag", "pass" if tags.strip() else "warn",
            "HEAD has at least one tag" if tags.strip() else "HEAD has no tag")
    return checks


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse normally echoes invalid options, which may contain secrets.
        self.exit(2, "releasecheck: invalid arguments\n")


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(description="Check a local Git worktree before a release.")
    parser.add_argument("path", nargs="?", default=".", help="Git worktree root (default: current directory)")
    parser.add_argument("--tag", help="Require this tag to point to HEAD")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report")
    parser.add_argument("--version", action="version", version=f"releasecheck {__version__}")
    args = parser.parse_args(argv)
    try:
        checks = inspect(args.path, args.tag)
    except InspectionError as exc:
        # Never include Git output, paths, or repository content in errors.
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        else:
            print(f"releasecheck: {exc}", file=sys.stderr)
        return 2
    failed = any(c["status"] == "fail" or (args.strict and c["status"] == "warn") for c in checks)
    outcome = "fail" if failed else "pass"
    if args.json:
        print(json.dumps({"status": outcome, "checks": checks}, sort_keys=True))
    else:
        for check in checks:
            print(f"{check['status'].upper():4} {check['name']}: {check['detail']}")
        print(f"Result: {outcome}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
