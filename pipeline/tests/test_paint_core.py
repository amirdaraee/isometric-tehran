"""The painter's core (paint/md_core.py) with a stand-in model: no GPU, no weights, no torch.

The stand-in knows the clean latent it wants and returns the exact flow-matching velocity towards it,
v = (x_t - target) / t, so any seam, gap or drift in the result comes from the painter, not from the model.
"""
import numpy as np

from paint.md_core import Plan, blend_tiles, feather, grid, hold_mask, paint, positions

STEPS = 8
SIG = [1 - i / STEPS * (1 - 1 / STEPS) for i in range(STEPS)] + [0.0]


def model(target_for):
    def v(key, xw, i):
        return (xw - target_for(key, xw.shape)) / SIG[i]
    return v


def test_coverage():
    for n in (128, 130, 200, 224, 500, 640, 1000):
        for off in (0, 48):
            ps = positions(n, 128, 96, off)
            assert ps[0] == 0 and ps[-1] + 128 >= n and all(p % 2 == 0 for p in ps), (n, off, ps)
            assert all(b - a <= 128 for a, b in zip(ps, ps[1:], strict=False)), (n, off, ps)
    assert set(grid(320, 320, 128, 96, False)) != set(grid(320, 320, 128, 96, True)), "shifted grid must differ"


def test_flat():
    x = paint(Plan(H=256, W=384, sigmas=SIG), model(lambda k, s: np.full(s, 0.3, np.float32)), channels=4)
    assert float(np.abs(x - 0.3).max()) < 1e-4


def test_no_seams():
    """Each window wants a different flat colour: the blend must be smooth, with no hard edges."""
    rng = np.random.default_rng(0)
    want = {}

    def target(k, s):
        want.setdefault(k, float(rng.random()))
        return np.full(s, want[k], np.float32)

    x = paint(Plan(H=320, W=448, sigmas=SIG), model(target), channels=2)[0]
    step = max(np.abs(np.diff(x, axis=0)).max(), np.abs(np.diff(x, axis=1)).max())
    assert step < (x.max() - x.min()) / 10


def test_resume(tmp_path):
    tgt = lambda k, s: np.broadcast_to(np.linspace(-1, 1, s[-1], dtype=np.float32), s)  # noqa: E731
    plan = Plan(H=256, W=256, sigmas=SIG)
    full = paint(plan, model(tgt), channels=3)
    sp = str(tmp_path / "state.npz")
    n = {"steps": 0}

    def on_step(i, steps, ran):
        n["steps"] = i

    assert paint(plan, model(tgt), channels=3, state_path=sp, on_step=on_step, stop=lambda: n["steps"] == 4) is None
    resumed = paint(plan, model(tgt), channels=3, state_path=sp, on_step=on_step)
    assert n["steps"] == STEPS
    assert np.allclose(full, resumed, atol=1e-5)


def test_hold():
    """Held pixels come out exactly as known; the rest reaches the model's target; held windows are skipped."""
    H, W = 256, 512
    known = np.random.default_rng(1).standard_normal((4, H, W)).astype(np.float32)
    known_px = np.zeros((H * 8, W * 8), bool)
    known_px[:, : W * 8 // 2] = True
    hold = hold_mask(known_px, band_px=64)
    assert hold[:, : W // 2 - 8].all() and not hold[:, W // 2 - 7:].any()
    calls = {"n": 0}

    def tgt(k, s):
        calls["n"] += 1
        return np.zeros(s, np.float32)

    plan = Plan(H=H, W=W, sigmas=SIG)
    x = paint(plan, model(tgt), channels=4, known=known, hold=hold)
    assert np.allclose(x[:, hold], known[:, hold], atol=1e-5)
    assert float(np.abs(x[:, :, W // 2 + 16:]).max()) < 1e-3
    assert calls["n"] == plan.window_runs(hold) < plan.window_runs()


def test_blend_and_feather():
    tiles = [(y, x, np.full((3, 64, 64), 0.7, np.float32)) for y in (0, 48, 96) for x in (0, 48, 96, 136)]
    assert float(np.abs(blend_tiles((3, 160, 200), tiles, 64, 16) - 0.7).max()) < 1e-6
    k = np.zeros((64, 64), bool)
    k[:, :32] = True
    a = feather(k, 8)
    assert a[0, 0] == 1 and a[0, 31] == 0 and a[0, 40] == 0 and 0 < a[0, 28] < 1
