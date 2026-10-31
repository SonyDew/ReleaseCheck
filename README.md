# ReleaseCheck

A read-only CLI for basic release checks in a local Git worktree.

**Status:** Completed MVP (SonyDev GitHub Marathon, Day 5, October 31, 2026). This is not a release certification tool.

ReleaseCheck checks for pending tracked or untracked changes, a nonempty README and license, a nonempty `.gitignore`, an unfinished Git operation, a detached HEAD, and a tag at HEAD. A specific lightweight or annotated tag can be required.

## Install and run

Requires Python 3.10+ and Git. There are no runtime Python dependencies.

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

Exit codes:

- `0`: no blocking checks.
- `1`: at least one failed check, or a warning with `--strict`.
- `2`: invalid arguments or an inspection error.

A dirty worktree, missing README or license, an unfinished Git operation, or a requested tag not pointing to HEAD is a failure. Missing `.gitignore`, detached HEAD, and no tag at HEAD are warnings unless `--strict` is used. Ignored files are excluded from the worktree check. The operation check looks for merge, rebase, cherry-pick, revert, sequencer, and bisect state markers.

Reports do not include filenames from `git status`, file contents, tag names, remote URLs, or Git stderr. Invalid CLI options are not echoed. Inspection uses file metadata and Git commands; it does not write to the repository.

## Tests

```sh
python -m unittest discover -s tests -v
```

## Limitations

This is a local preflight check, not a full release policy. It does not verify changelogs, version numbers, signatures, test results, CI, dependency vulnerabilities, or remote branch synchronization. README and license checks recognize common filenames only at the worktree root. They check for nonzero file size, not document validity. A Git worktree with an existing commit is required. Git commands have a 15-second timeout and a 4 MiB output limit. Concurrent changes to the repository may produce inconsistent results. Command-line parsing errors are printed to stderr, including when `--json` is requested.

## Development report — October 31, 2026

On Linux with Python 3.13.5 and Git 2.47.3, all 44 `unittest` cases passed. Tests include a clean worktree with a pending merge, Git operation markers in a linked worktree, malformed tag names, symlinks, staged and untracked changes, a Git output-size limit, and a timeout when a Git child retains the output pipe. The CLI was also tested after installing a locally built wheel. Windows and Python 3.10–3.12 were not tested.
