"""Block planning, claims and submissions (paint/blocks.py, paint/registry.py), and the committed paint files."""
import datetime as dt
import json

import numpy as np
import pytest

from paint import blocks, config, registry

GRID = blocks.Grid()  # 4800 px blocks, 1200 px overlap, 600 -> 512 px


def small_plan():
    W, H = 20480, 10240
    mask = np.zeros((H // 32, W // 32), bool)
    mask[40:280, 60:600] = True  # data in the middle, empty edges
    lms = [{"slug": "here", "x": 7790, "y": 5556}]
    return blocks.plan_blocks(W, H, (7790, 5556), GRID, mask, 32, 0.03, lms), W, H


def test_grid_scale():
    assert GRID.stride == 3600 and GRID.model_size == 4096
    assert GRID.to_model(3600) == 3072 and GRID.to_model(1200) == 1024  # overlap = one 1024 px window
    with pytest.raises(ValueError):
        GRID.to_model(100)


def test_plan_outward_from_anchor():
    bl, W, H = small_plan()
    b0 = bl[0]
    assert b0["id"] == "b00" and (b0["x0"], b0["y0"]) == (7790 - 2400, 5556 - 2400)
    assert b0["landmarks"] == ["here"] and b0["before"] == []
    assert [b["dist"] for b in bl] == sorted(b["dist"] for b in bl)
    # the map is covered: every pixel with data is in some block
    for x in range(0, W, 997):
        for y in range(0, H, 991):
            if 60 * 32 <= x < 600 * 32 and 40 * 32 <= y < 280 * 32:
                assert any(b["x0"] <= x < b["x0"] + 4800 and b["y0"] <= y < b["y0"] + 4800 for b in bl), (x, y)
    # neighbours overlap by exactly the overlap, and "before" lists only earlier overlapping blocks
    order = {b["id"]: i for i, b in enumerate(bl)}
    for b in bl:
        for o in b["before"]:
            ob = next(x for x in bl if x["id"] == o)
            assert order[o] < order[b["id"]] and blocks.overlaps(ob, b)
    # deterministic: same inputs, same ids and hash
    bl2, _, _ = small_plan()
    assert blocks.plan_hash(bl, W, H, GRID) == blocks.plan_hash(bl2, W, H, GRID)


def test_plan_skips_empty_blocks():
    bl, _, _ = small_plan()
    assert all(b["fill"] >= 0.03 for b in bl)
    assert len(bl) < len(blocks.axis(5390, 20480, 4800, 3600)) * len(blocks.axis(3156, 10240, 4800, 3600))


def test_states():
    bl, W, H = small_plan()
    plan = {"blocks": bl}
    b1 = bl[1]["id"]
    claims = registry.parse_claims({"claims": [{"block": b1, "by": "someone", "since": "2026-11-01", "issue": 4}]})
    st = {r["id"]: r for r in blocks.states(plan, claims, {})}
    assert st["b00"]["state"] == "ready" and st[b1]["state"] == "claimed" and "#4" in st[b1]["detail"]
    assert all(st[b["id"]]["state"] == "waiting" for b in bl[2:] if b["before"])
    subs = {("b00", "day"): {"by": "painter"}}
    st = {r["id"]: r for r in blocks.states(plan, claims, subs)}
    assert st["b00"]["state"] == "done"
    nxt = [b for b in bl[2:] if b["before"] == ["b00"]]
    assert all(st[b["id"]]["state"] == "ready" for b in nxt)
    night = {r["id"]: r for r in blocks.states(plan, [], subs, "night")}
    assert night["b00"]["state"] == "ready" and night[b1]["state"] == "waiting"


def test_claims_parsing():
    ok = registry.parse_claims({"claims": [{"block": "b03", "by": "a-user", "since": "2026-11-08"},
                                           {"block": "b03", "version": "night", "by": "b", "since": "2026-11-08"}]},
                               {"day", "night"})
    assert ok[0]["version"] == "day" and ok[0]["since"] == dt.date(2026, 11, 8)
    bad = [
        {"claims": [{"block": "b3x", "by": "a", "since": "2026-11-08"}]},
        {"claims": [{"block": "b03", "by": "a b", "since": "2026-11-08"}]},
        {"claims": [{"block": "b03", "by": "a", "since": "8 Nov"}]},
        {"claims": [{"block": "b03", "by": "a", "since": "2026-11-08", "colour": "red"}]},
        {"claims": [{"block": "b03", "by": "a", "since": "2026-11-08"}, {"block": "b03", "by": "b", "since": "2026-11-09"}]},
        {"claims": [{"block": "b03", "version": "winter", "by": "a", "since": "2026-11-08"}]},
        {"claims": "b03"},
    ]
    for data in bad:
        with pytest.raises(ValueError):
            registry.parse_claims(data, {"day", "night"})
    c = registry.parse_claims({"claims": [{"block": "b03", "by": "a", "since": "2026-10-01"}]})[0]
    assert registry.is_stale(c, dt.date(2026, 11, 8)) and not registry.is_stale(c, dt.date(2026, 10, 10))


def sub(**kw):
    s = {"block": "b03", "version": "day", "by": "painter", "url": "https://github.com/painter/x/releases/download/t/b03-day.png",
         "sha256": "a" * 64, "bytes": 30_000_000, "width": 4096, "height": 4096, "plan": "d331bbdbbdb8",
         "x0": 8990, "y0": 3156, "size": 4800, "neighbours": {"b00": "b" * 64}, "licence": "CC BY-NC 4.0",
         "agree": True}
    s.update(kw)
    return s


def test_submission_parsing():
    assert registry.parse_submission(sub())["block"] == "b03"
    for bad in (sub(url="http://x"), sub(sha256="xyz"), sub(licence="CC0"), sub(agree=False), sub(width=0),
                sub(extra=1), sub(neighbours={"b00": "nope"})):
        with pytest.raises(ValueError):
            registry.parse_submission(bad)
    s = sub()
    del s["url"]
    with pytest.raises(ValueError):
        registry.parse_submission(s)


def test_committed_paint_files():
    """claims.json and every submission in the repo are valid; submissions are https and named for their block."""
    versions = set(config.settings()["versions"])
    registry.load_claims(versions=versions)
    for (bid, v), s in registry.load_submissions().items():
        assert s["url"].startswith("https://"), f"{bid}-{v}: url must be https"
        assert v in versions
    hints = json.loads((config.PROMPTS / "landmarks.json").read_text(encoding="utf-8"))["hints"]
    slugs = {lm["slug"] for lm in json.loads((config.ROOT / "pipeline" / "landmarks.json").read_text(encoding="utf-8"))}
    assert set(hints) <= slugs, set(hints) - slugs
    for name, v in config.settings()["versions"].items():
        text = (config.PROMPTS / v["prompt"]).read_text(encoding="utf-8")
        assert "{style}" in text, f"{v['prompt']} must name the style with {{style}}"
        assert v["lora"] in config.settings()["loras"], name
