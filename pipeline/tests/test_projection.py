import math

import numpy as np
import pytest

from common import Camera, Projection, bbox


def test_city_bbox_frames_the_historic_core(cfg):
    w, s, e, n = bbox(cfg)
    # Golestan Palace, the Grand Bazaar, Toopkhaneh Square, Lalehzar Street
    for lon, lat in [(51.4203, 35.6797), (51.4238, 35.6769), (51.4215, 35.6857), (51.4231, 35.6933)]:
        assert w < lon < e and s < lat < n


def test_bad_bbox_is_refused():
    with pytest.raises(ValueError):
        bbox({"bbox": {"west": 51.5, "south": 35.7, "east": 51.4, "north": 35.6}})


def test_projection_round_trip_and_origin(cfg):
    p = Projection(cfg)
    w, s, e, n = bbox(cfg)
    x, y = p.to_local((w + e) / 2, (s + n) / 2)
    assert abs(float(x)) < 1e-6 and abs(float(y)) < 1e-6
    lon, lat = p.to_lonlat(*p.to_local(51.4203, 35.6797))
    assert lon == pytest.approx(51.4203, abs=1e-9) and lat == pytest.approx(35.6797, abs=1e-9)
    x0, y0, x1, y1 = p.local_bounds(cfg)
    assert 2500 < x1 - x0 < 3800 and 3000 < y1 - y0 < 4000   # about 3.2 km by 3.6 km


def test_camera_is_luxembourg_dimetric(cfg):
    cam = Camera(cfg)
    assert cfg["camera"]["lookAzimuthDeg"] == 315 and cfg["camera"]["elevationDeg"] == 30
    # north-west is into the screen, north-east is screen right
    np.testing.assert_allclose(cam.fwd, [-math.sqrt(0.5), math.sqrt(0.5)], atol=1e-12)
    np.testing.assert_allclose(cam.right, [math.sqrt(0.5), math.sqrt(0.5)], atol=1e-12)
    # 2:1: a square on the ground is twice as wide as it is tall on screen
    sx, sy = cam.to_screen(np.array([1, 0, -1, 0]), np.array([0, 1, 0, -1]), 0)
    assert (sx.max() - sx.min()) / (sy.max() - sy.min()) == pytest.approx(2.0)
    # one metre up is cos(30) metres up on screen, and nearer the camera
    assert cam.to_screen(0, 0, 1)[1] == pytest.approx(math.cos(math.radians(30)))
    assert cam.depth(0, 0, 1) < cam.depth(0, 0, 0)


def test_view_matrix_matches_to_screen(cfg):
    cam = Camera(cfg)
    rng = np.random.default_rng(1)
    p = rng.uniform(-500, 500, (20, 3))
    s = p @ cam.view_matrix().T
    sx, sy = cam.to_screen(p[:, 0], p[:, 1], p[:, 2])
    np.testing.assert_allclose(s[:, 0], sx)
    np.testing.assert_allclose(s[:, 1], sy)
    np.testing.assert_allclose(s[:, 2], cam.depth(p[:, 0], p[:, 1], p[:, 2]))
