#!/usr/bin/env python3
"""Pull-request rule for painted-block submissions (CI job "repo-checks", pull requests only).

A pull request that adds or changes a submission (pipeline/paint/submissions/<block>-<version>.json) must change
nothing else: no code, no workflow, no other file. Then the maintainer reviews data only, and runs the review
tools from a trusted checkout of main (docs/PAINTING.md, "Review"). Also, nothing but such JSON files may ever
be put in pipeline/paint/submissions/.

    python3 scripts/check_submission_pr.py <base ref>      e.g. origin/main
Standard library only. Exit status 1 with the problems if the rule is broken.
"""
from __future__ import annotations

import re
import subprocess
import sys

SUBMISSION = re.compile(r"^pipeline/paint/submissions/b\d{2}-[a-z_]+\.json$")
FOLDER = "pipeline/paint/submissions/"
ALLOWED_IN_FOLDER = {FOLDER + ".gitkeep"}


def problems(changed: list[str]) -> list[str]:
    """Problems with a pull request that changes these paths."""
    out = [f"{p}: only <block>-<version>.json files belong in {FOLDER}" for p in changed
           if p.startswith(FOLDER) and not SUBMISSION.match(p) and p not in ALLOWED_IN_FOLDER]
    subs = [p for p in changed if SUBMISSION.match(p)]
    if subs:
        others = [p for p in changed if not SUBMISSION.match(p)]
        out += [f"{p}: a submission pull request may change only {FOLDER}*.json (it changes {', '.join(subs)}); "
                f"send other changes in a separate pull request" for p in others]
    return out


def changed_files(base: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", "--no-renames", f"{base}...HEAD"], capture_output=True,
                         text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    changed = changed_files(sys.argv[1])
    found = problems(changed)
    print(f"{len(changed)} changed file(s)")
    for p in found:
        print("ERROR", p)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
