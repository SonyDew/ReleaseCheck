# ReleaseCheck

A read-only CLI for checking basic release readiness in a local Git worktree.

**Status:** In Development (SonyDev GitHub Marathon, Day 5, October 31, 2026).

ReleaseCheck checks whether the worktree is clean, whether a nonempty README and license file exist, whether `.gitignore` exists, whether HEAD is on a branch, and whether HEAD is tagged. You can require a specific tag. It does not publish releases or run project code.

## Install and run

Requires Python 3.10+ and Git. No runtime Python dependencies.

```sh
python -m pip install .
releasecheck .
releasecheck /path/to/project --tag v1.0.0 --strict
releasecheck . --json
```

Without installing:

```sh
python -m releasecheck /path/to/project --json
```

Exit codes: `0` means no blocking checks; `1` means a check failed (or a warning under `--strict`); `2` means the repository could not be inspected. Without `--strict`, warnings do not block. A missing README or license and a dirty worktree always fail. A missing `.gitignore`, detached HEAD, or untagged HEAD warns unless `--strict` is set. `--tag` requires the named lightweight or annotated tag to resolve to HEAD.

The tool does not print filenames from `git status`, tag names, remote URLs, file contents, or Git error output. It reads metadata and does not modify the repository. Ignored files are not counted as pending changes.

## Tests

```sh
python -m unittest discover -s tests -v
```

## Limitations

This is a basic local preflight check, not a complete release policy. It does not validate changelogs, version numbers, signatures, test results, CI, dependency vulnerabilities, or remote branch synchronization. It only checks common README/license filenames at the worktree root. It requires an existing commit and a non-bare worktree. Git commands have a 15-second timeout and a 4 MiB output limit. Checks are not atomic if the repository changes during inspection.

## Development checks (October 31, 2026)

On Linux with Python 3.13.5 and Git 2.47.3, all 34 `unittest` cases passed. The added cases cover malformed Unicode tag names, option-like tag names, staged changes, symlinked README files, blob tags, Git environment overrides, hidden filenames in text output, and bounded Git output.

A wheel was built and installed in a separate virtual environment. The installed command returned `0` for a clean worktree and for a matching tag under `--strict`, `1` for an untagged worktree under `--strict` and for a missing requested tag, and `2` for an inaccessible path. JSON reports were parsed for each case. Windows and Python 3.10–3.12 were not tested.
