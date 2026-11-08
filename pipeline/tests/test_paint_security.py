"""Untrusted input from submissions: names, versions, URLs and paths (paint/registry.py, paint/blocks.py), and the
pull-request rule for submissions (scripts/check_submission_pr.py)."""
import importlib.util
import json
from pathlib import Path

import pytest

from paint import blocks, config, registry

GOOD_URL = "https://github.com/painter/isometric-tehran/releases/download/paint-b03-day/b03-day.png"


def sub(**kw):
    s = {"block": "b03", "version": "day", "by": "painter", "url": GOOD_URL, "sha256": "a" * 64, "bytes": 30_000_000,
         "width": 4096, "height": 4096, "plan": "d331bbdbbdb8", "x0": 8990, "y0": 3156, "size": 4800,
         "neighbours": {"b00": "b" * 64}, "licence": "CC BY-NC 4.0", "agree": True}
    s.update(kw)
    return s


@pytest.mark.parametrize("field,value", [
    ("version", "../../../etc"), ("version", "winter"), ("version", "day/x"),
    ("block", "../b03"), ("block", "b3"), ("block", "b003"), ("block", "b03/"),
    ("neighbours", {"../b00": "b" * 64}),
])
def test_names_from_submissions_are_strict(field, value):
    with pytest.raises(ValueError):
        registry.parse_submission(sub(**{field: value}))


def test_file_name_must_match():
    registry.parse_submission(sub(), file_name="b03-day.json")
    for name in ("b04-day.json", "b03-night.json", "x.json"):
        with pytest.raises(ValueError):
            registry.parse_submission(sub(), file_name=name)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "http://github.com/a/b/releases/download/t/f.png", "https://example.com/b03.png",
    "https://169.254.169.254/latest/meta-data", "https://github.com/a/b/raw/main/f.png",
    "https://github.com.evil.example/a/b/releases/download/t/f.png", "https://user:pw@github.com/a/b/releases/download/t/f.png",
    "https://github.com:8443/a/b/releases/download/t/f.png", "ftp://github.com/x",
])
def test_only_github_release_urls(url):
    assert not registry.url_allowed(url)
    with pytest.raises(ValueError):
        registry.parse_submission(sub(url=url))


def test_allowed_urls():
    assert registry.url_allowed(GOOD_URL)
    assert registry.url_allowed("https://objects.githubusercontent.com/github-production-release-asset/1/2")
    # local files only through the dry run's explicit flag, never from a JSON alone
    assert registry.url_allowed("file:///tmp/x.png", allow_file=True)
    registry.parse_submission(sub(url="file:///tmp/x.png"), allow_file=True)


def test_download_refuses_bad_urls_and_paths(tmp_path):
    with pytest.raises(SystemExit):
        blocks.download("file:///etc/hosts", tmp_path / "x.png", base=tmp_path)
    src = tmp_path / "src.bin"
    src.write_bytes(b"canvas")
    with pytest.raises(ValueError):  # destination outside the base folder
        blocks.download(src.as_uri(), tmp_path / ".." / "escape.png", allow_file=True, base=tmp_path / "work")
    got = blocks.download(src.as_uri(), tmp_path / "work" / "ok.png", allow_file=True, base=tmp_path / "work")
    assert got.read_bytes() == b"canvas"


def test_inside():
    base = config.PAINT_WORK
    assert registry.inside(base / "review" / "b03-day.png") == (base / "review" / "b03-day.png").resolve()
    for bad in (base / ".." / "x.png", base / "review" / ".." / ".." / "x", Path("/etc/passwd")):
        with pytest.raises(ValueError):
            registry.inside(bad)


def test_load_submissions_checks_names(tmp_path):
    (tmp_path / "b04-day.json").write_text(json.dumps(sub()))
    with pytest.raises(ValueError):
        registry.load_submissions(tmp_path)


def _pr_rule():
    p = Path(__file__).resolve().parents[2] / "scripts" / "check_submission_pr.py"
    spec = importlib.util.spec_from_file_location("check_submission_pr", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_submission_pr_rule():
    rule = _pr_rule()
    assert rule.problems(["pipeline/paint/submissions/b03-day.json"]) == []
    assert rule.problems(["pipeline/paint/submissions/b03-day.json", "pipeline/paint/submissions/b04-night.json"]) == []
    assert rule.problems(["pipeline/paint/blocks.py", "docs/PAINTING.md"]) == []          # ordinary code PR
    bad = rule.problems(["pipeline/paint/submissions/b03-day.json", "pipeline/paint/review.py"])
    assert len(bad) == 1 and "review.py" in bad[0]
    assert rule.problems(["pipeline/paint/submissions/b03-day.json", ".github/workflows/ci.yml"])
    assert rule.problems(["pipeline/paint/submissions/evil.py"])
    assert rule.problems(["pipeline/paint/submissions/sub/b03-day.json"])
