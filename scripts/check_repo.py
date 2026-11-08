#!/usr/bin/env python3
"""Repository checks run in CI (job "repo-checks") and before a pull request:

  - no file larger than MAX_FILE_MB, except under the allowed folders;
  - the committed map tiles stay under TILES_BUDGET_MB, and the whole repository under REPO_BUDGET_MB;
  - no raw data, work files or local settings (data/, .env files, GeoParquet, GeoTIFF, numpy archives...);
  - nothing that looks like a secret (API keys, tokens, private keys).

    python scripts/check_repo.py            # the files git tracks (what a pull request would add)
Exit status 1 with a list of problems if any check fails. Standard library only.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_MB = 2.0
TILES = "web/public/tiles/"
TILES_BUDGET_MB = 150.0
REPO_BUDGET_MB = 200.0
# Files that may be larger than MAX_FILE_MB (none yet; add a path with a reason if one is ever needed).
ALLOW_LARGE: set[str] = set()

FORBIDDEN = [
    (re.compile(r"^data/"), "raw data and work files live in data/ and are not committed"),
    (re.compile(r"(^|/)\.env(\..*)?$"), "environment files hold secrets"),
    (re.compile(r"\.(parquet|tif|tiff|npz|gpkg|pbf|osm|zip|tar|gz|7z)$", re.I), "large data format"),
    (re.compile(r"(^|/)(node_modules|\.venv|dist)/"), "build output or dependencies"),
    (re.compile(r"\.(pem|key|p12|pfx)$", re.I), "key file"),
]

SECRETS = [
    (re.compile(rb"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(rb"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY"), "private key"),
    (re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token"),
    (re.compile(rb"github_pat_[A-Za-z0-9_]{40,}"), "GitHub token"),
    (re.compile(rb"sk-(proj-|ant-)?[A-Za-z0-9_-]{32,}"), "API secret key"),
    (re.compile(rb"sntrys_[A-Za-z0-9+/=_-]{40,}"), "Sentry auth token"),
    (re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(rb"AIza[0-9A-Za-z_-]{35}"), "Google API key"),
    (re.compile(rb"rpa_[A-Za-z0-9]{30,}"), "RunPod API key"),
]
TEXT_EXT = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".yml", ".yaml", ".toml", ".txt", ".html", ".css",
            ".sh", ".cfg", ".ini", ".svg", ""}


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p and (ROOT / p).is_file()]


def main() -> int:
    files = tracked()
    problems: list[str] = []
    total = tiles = 0
    for p in files:
        size = (ROOT / p).stat().st_size
        total += size
        if p.startswith(TILES):
            tiles += size
        if size > MAX_FILE_MB * 1e6 and p not in ALLOW_LARGE:
            problems.append(f"{p}: {size / 1e6:.1f} MB is over {MAX_FILE_MB} MB")
        for rx, why in FORBIDDEN:
            if rx.search(p):
                problems.append(f"{p}: {why}")
        if Path(p).suffix.lower() in TEXT_EXT and size < 2e6 and p != "scripts/check_repo.py":
            data = (ROOT / p).read_bytes()
            for rx, what in SECRETS:
                if rx.search(data):
                    problems.append(f"{p}: looks like a {what}")
    if tiles > TILES_BUDGET_MB * 1e6:
        problems.append(f"{TILES}: {tiles / 1e6:.0f} MB is over the {TILES_BUDGET_MB:.0f} MB budget (move tiles to release assets)")
    if total > REPO_BUDGET_MB * 1e6:
        problems.append(f"repository: {total / 1e6:.0f} MB is over the {REPO_BUDGET_MB:.0f} MB budget")
    print(f"{len(files)} files, {total / 1e6:.1f} MB tracked ({tiles / 1e6:.1f} MB of tiles)")
    for p in problems:
        print("ERROR", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
