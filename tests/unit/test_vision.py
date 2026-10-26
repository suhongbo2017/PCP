"""Template matching on synthetic images (no device)."""
import cv2
import numpy as np
import pytest

from phonectl.automation.vision import find_template, load_image


@pytest.fixture
def screen():
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, size=(300, 200, 3), dtype=np.uint8)


def test_finds_exact_crop_at_its_position(screen):
    tpl = screen[120:160, 50:90].copy()
    m = find_template(screen, tpl, threshold=0.99)
    assert m is not None
    assert (m.x, m.y, m.w, m.h) == (50, 120, 40, 40)
    assert m.score > 0.99
    assert m.center == (70, 140)


def test_returns_none_when_absent(screen):
    other = np.random.default_rng(99).integers(0, 255, size=(40, 40, 3), dtype=np.uint8)
    assert find_template(screen, other, threshold=0.9) is None


def test_region_restricts_search_and_offsets_result(screen):
    tpl = screen[120:160, 50:90].copy()
    # outside the region -> not found
    assert find_template(screen, tpl, threshold=0.99, region=(0, 0, 40, 40)) is None
    # inside the region -> found, coordinates still in full-image pixels
    m = find_template(screen, tpl, threshold=0.99, region=(40, 100, 80, 80))
    assert m is not None and (m.x, m.y) == (50, 120)


def test_multi_scale_finds_a_resized_target(screen):
    crop = screen[100:160, 60:120].copy()  # 60x60 target at scale 1.0
    # The on-screen target is 1.5x bigger than our template; scale the template up to match.
    big_screen = cv2.resize(screen, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_LINEAR)
    tpl_small = crop
    m = find_template(big_screen, tpl_small, threshold=0.9, scales=(1.0, 1.5))
    assert m is not None
    assert m.scale == pytest.approx(1.5)


def test_scale_one_only_misses_a_resized_target(screen):
    big_screen = cv2.resize(screen, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_LINEAR)
    tpl = screen[100:160, 60:120].copy()
    assert find_template(big_screen, tpl, threshold=0.99, scales=(1.0,)) is None


def test_flat_template_is_rejected(screen):
    flat = np.full((20, 20, 3), 128, dtype=np.uint8)
    assert find_template(screen, flat, threshold=0.0) is None


def test_template_larger_than_image_is_skipped(screen):
    big = np.zeros((400, 400, 3), dtype=np.uint8)
    assert find_template(screen, big, threshold=0.0) is None


def test_load_image_handles_non_ascii_path(tmp_path, screen):
    p = tmp_path / "图标.png"
    ok, buf = cv2.imencode(".png", screen[10:30, 10:30])
    p.write_bytes(buf.tobytes())
    img = load_image(p)
    assert img.shape == (20, 20, 3)


def test_load_image_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_image(tmp_path / "nope.png")
