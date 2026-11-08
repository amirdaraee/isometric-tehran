"""The whole painting flow on the CPU with the stand-in model (paint.py dry-run): plan, prepare, paint with a pause
and resume, submit, review, paint a neighbour holding the first block, blend, tiles."""
import json

from paint.dry_run import dry_run


def test_dry_run(tmp_path):
    out = tmp_path / "dry"
    assert dry_run(out) == 0
    sub = json.loads((out / "submissions" / "b00-day.json").read_text())
    assert sub["width"] == sub["height"] == 2048 and sub["agree"] is True
    rep = json.loads((out / "blocks" / "review" / "b01-day.json").read_text())
    assert rep["ok"] and rep["held_dE"] < 1 and rep["seams"]["bad"] == []
    assert (out / "tiles" / "map.dzi").exists() and (out / "before_after.jpg").exists()
