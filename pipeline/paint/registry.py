"""Who paints what: the claims (paint/claims.json) and the submissions (paint/submissions/<block>-<version>.json).

Both live in git and change only through pull requests, so the history of every block is in the repo:

  claims.json        {"claims": [{"block": "b03", "version": "day", "by": "github-user", "issue": 12,
                                  "since": "2026-11-08"}]}
  submissions/b03-day.json   one painted block: where to download it, its checksum and how it was painted
                             (fields: SUBMISSION_FIELDS). Merged into main = reviewed and accepted.

A claim older than CLAIM_DAYS without a submission is shown as stale and may be taken over (ask on the issue).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from paint.config import CLAIMS, SUBMISSIONS

CLAIM_DAYS = 14
BLOCK_ID = re.compile(r"^b\d{2,3}$")
USER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
LICENCE = "CC BY-NC 4.0"
CLAIM_FIELDS = {"block", "version", "by", "since", "issue", "note"}
SUBMISSION_FIELDS = {"block", "version", "by", "url", "sha256", "bytes", "width", "height", "plan", "x0", "y0",
                     "size", "settings", "neighbours", "licence", "agree", "note"}
REQUIRED = SUBMISSION_FIELDS - {"note", "neighbours", "settings"}


def _date(s: str, where: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: since must be a date like 2026-11-08, not {s!r}") from None


def parse_claims(data: dict, versions: set[str] | None = None) -> list[dict]:
    """Validate claims.json's content; returns the claims. Raises ValueError with every problem found."""
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        raise ValueError('claims.json must be {"claims": [...]}')
    problems, seen, out = [], {}, []
    for i, c in enumerate(data["claims"]):
        where = f"claims[{i}]"
        if not isinstance(c, dict):
            problems.append(f"{where}: not an object")
            continue
        extra = set(c) - CLAIM_FIELDS
        if extra:
            problems.append(f"{where}: unknown field(s) {sorted(extra)}")
        for f in ("block", "by", "since"):
            if f not in c:
                problems.append(f"{where}: missing {f}")
        if "block" in c and not BLOCK_ID.match(str(c["block"])):
            problems.append(f"{where}: block {c['block']!r} is not like b03")
        if "by" in c and not USER.match(str(c["by"])):
            problems.append(f"{where}: by {c['by']!r} is not a GitHub user name")
        v = c.get("version", "day")
        if versions is not None and v not in versions:
            problems.append(f"{where}: version {v!r} is not one of {sorted(versions)}")
        if "issue" in c and (not isinstance(c["issue"], int) or c["issue"] < 0):
            problems.append(f"{where}: issue must be a number")
        try:
            since = _date(c.get("since"), where) if "since" in c else None
        except ValueError as e:
            problems.append(str(e))
            since = None
        key = (c.get("block"), v)
        if key in seen:
            problems.append(f"{where}: {key[0]} ({v}) is already claimed in claims[{seen[key]}]")
        seen[key] = i
        out.append({**c, "version": v, "since": since})
    if problems:
        raise ValueError("; ".join(problems))
    return out


def load_claims(path: Path = CLAIMS, versions: set[str] | None = None) -> list[dict]:
    if not path.exists():
        return []
    return parse_claims(json.loads(path.read_text(encoding="utf-8")), versions)


def is_stale(claim: dict, today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    return claim["since"] is not None and (today - claim["since"]).days > CLAIM_DAYS


def parse_submission(data: dict, name: str = "submission") -> dict:
    """Validate one submission JSON. Raises ValueError with every problem found."""
    if not isinstance(data, dict):
        raise ValueError(f"{name}: not an object")
    p = []
    missing = REQUIRED - set(data)
    if missing:
        p.append(f"missing {sorted(missing)}")
    extra = set(data) - SUBMISSION_FIELDS
    if extra:
        p.append(f"unknown field(s) {sorted(extra)}")
    if "block" in data and not BLOCK_ID.match(str(data["block"])):
        p.append(f"block {data['block']!r} is not like b03")
    if "by" in data and not USER.match(str(data["by"])):
        p.append(f"by {data['by']!r} is not a GitHub user name")
    if "url" in data and not re.match(r"^(https://|file://)", str(data["url"])):
        p.append("url must be https:// (file:// only in the dry run)")
    if "sha256" in data and not SHA256.match(str(data["sha256"])):
        p.append("sha256 must be 64 lower-case hex digits")
    for f in ("bytes", "width", "height", "size"):
        if f in data and (not isinstance(data[f], int) or data[f] <= 0):
            p.append(f"{f} must be a positive whole number")
    for f in ("x0", "y0"):
        if f in data and not isinstance(data[f], int):
            p.append(f"{f} must be a whole number")
    if "licence" in data and data["licence"] != LICENCE:
        p.append(f"licence must be {LICENCE!r} (LICENSE-ART.md)")
    if "agree" in data and data["agree"] is not True:
        p.append("agree must be true: painted blocks are accepted under the art licence")
    if "neighbours" in data and not (isinstance(data["neighbours"], dict)
                                     and all(SHA256.match(str(v)) for v in data["neighbours"].values())):
        p.append("neighbours must map block ids to the sha256 of the canvas held")
    if p:
        raise ValueError(f"{name}: " + "; ".join(p))
    return data


def submission_name(bid: str, version: str) -> str:
    return f"{bid}-{version}.json"


def load_submissions(folder: Path = SUBMISSIONS) -> dict[tuple[str, str], dict]:
    """{(block, version): submission} for every submission JSON in the folder (in main = accepted)."""
    out = {}
    for f in sorted(folder.glob("*.json")) if folder.exists() else []:
        s = parse_submission(json.loads(f.read_text(encoding="utf-8")), f.name)
        if f.name != submission_name(s["block"], s["version"]):
            raise ValueError(f"{f.name}: must be named {submission_name(s['block'], s['version'])}")
        out[(s["block"], s["version"])] = s
    return out
