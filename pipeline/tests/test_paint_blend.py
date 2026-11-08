"""Blending painted blocks and the review checks (paint/blend.py), on tiny images."""
import numpy as np

from paint import blend


def texture(h, w, seed=0):
    """A soft, picture-like test image: smooth colour waves plus fine noise."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    base = np.stack([120 + 40 * np.sin(x / 37), 130 + 30 * np.cos(y / 29), 110 + 25 * np.sin((x + y) / 53)], -1)
    return np.clip(base + rng.normal(0, 4, (h, w, 3)), 0, 255).astype(np.uint8)


def test_ramp_alpha():
    a = blend.ramp_alpha(100, 60, 10)
    assert a[0, 50] == 0 and a[30, 50] == 1 and 0 < a[5, 50] < 1
    assert np.allclose(a, a[::-1, ::-1])


def test_blend_into_feathers_and_clips():
    base = np.zeros((100, 200, 3), np.uint8)
    patch = np.full((80, 80, 3), 200, np.uint8)
    out = blend.blend_into(base, patch, 150, -20, 10)    # partly off the map: clipped
    assert out.shape == base.shape
    assert out[30, 190, 0] == 200                          # deep inside: the patch
    assert out[30, 151, 0] < 40                            # its edge: mostly the map underneath
    assert (out[:, :150] == 0).all()                       # nothing outside the patch changes
    # two neighbours holding the same pixels in their overlap blend into one continuous picture
    img = texture(100, 260)
    left, right = img[:, :160], img[:, 100:]
    m = blend.blend_into(blend.blend_into(np.zeros_like(img), left, 0, 0, 30), right, 100, 0, 30)
    assert np.abs(m[30:70, 30:230].astype(int) - img[30:70, 30:230]).max() <= 1  # away from the outer fades


def test_seams_found_only_where_there_is_one():
    img = texture(512, 512)
    keep = np.zeros((512, 512), bool)
    keep[:, :256] = True                                   # the left half was held
    assert blend.seams(img, keep)["bad"] == []
    shifted = img.copy()
    shifted[:, 256:] = np.clip(shifted[:, 256:].astype(int) + [30, -10, 20], 0, 255).astype(np.uint8)
    r = blend.seams(shifted, keep)
    assert r["bad_fraction"] > 0.5 and all(b["axis"] == "x" and b["at"] == 256 for b in r["bad"])


def test_held_drift():
    img = texture(256, 256)
    keep = np.zeros((256, 256), bool)
    keep[:, :128] = True
    assert blend.held_drift(img, img, keep, 16) < 0.01
    other = np.clip(img.astype(int) + 25, 0, 255).astype(np.uint8)
    assert blend.held_drift(img, other, keep, 16) > 5


def test_position():
    render = texture(512, 512, 3)
    render[:, :60] = blend.BG                              # empty map edge on the left
    same = blend.position(render, render)
    assert same["bg_iou"] == 1.0 and max(map(abs, same["shift_px"])) < 1
    moved = np.roll(render, (0, 24), axis=(0, 1))
    r = blend.position(moved, render)
    assert r["bg_iou"] < 0.9 and abs(r["shift_px"][0]) > 12


def test_composite_pyvips(tmp_path):
    import pyvips
    from PIL import Image

    Image.fromarray(np.full((40, 40, 3), 200, np.uint8)).save(tmp_path / "p.png")
    base = pyvips.Image.black(100, 60, bands=3).cast("uchar")
    out = blend.composite(base, [{"file": "p.png", "x0": 70, "y0": 10, "w": 40, "h": 40}], 10, tmp_path)
    a = np.ndarray(buffer=out.write_to_memory(), dtype=np.uint8, shape=(out.height, out.width, out.bands))
    ref = blend.blend_into(np.zeros((60, 100, 3), np.uint8), np.full((40, 40, 3), 200, np.uint8), 70, 10, 10)
    assert np.abs(a.astype(int) - ref).max() <= 2
