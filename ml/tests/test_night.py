"""Low-light CLAHE, haze removal and mode selection."""
import cv2
import numpy as np
import pytest

from ml.config import ml_config
from ml.night import NightEnhancer, ensure_bgr
from ml.tests.conftest import DAY, NIGHT


@pytest.fixture
def enhancer():
    return NightEnhancer()


def textured(brightness: int, spread: int, size=(240, 320), seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.integers(-spread, spread + 1, size=(*size, 3))
    gradient = np.linspace(-spread, spread, size[1])[None, :, None]
    return np.clip(brightness + base // 2 + gradient, 0, 255).astype(np.uint8)


def test_dark_frame_gets_clahe(enhancer, bus_image):
    dark = (bus_image * 0.12).astype(np.uint8)
    assert enhancer.get_brightness(dark) < ml_config.night_brightness_threshold

    enhanced, changed, mode = enhancer.check_and_enhance(dark)
    assert changed is True and mode == "night_enhanced"
    assert enhanced.shape == dark.shape and enhanced.dtype == np.uint8
    assert enhancer.get_brightness(enhanced) > enhancer.get_brightness(dark)
    assert enhancer.get_contrast(enhanced) > enhancer.get_contrast(dark)


def test_clahe_improves_detection_in_the_dark(enhancer, bus_image, loaded_detector):
    dark = (bus_image * 0.12).astype(np.uint8)
    raw_people = sum(d["cls_name"] == "person" for d in loaded_detector.detect(dark))
    enhanced, _, _ = enhancer.check_and_enhance(dark)
    enhanced_people = sum(d["cls_name"] == "person" for d in loaded_detector.detect(enhanced))
    assert enhanced_people >= raw_people
    assert enhanced_people >= 2


def test_normal_daylight_frame_untouched(enhancer, bus_image):
    enhanced, changed, mode = enhancer.check_and_enhance(bus_image)
    assert changed is False and mode == "normal"
    assert np.array_equal(enhanced, bus_image)


def test_haze_is_removed(enhancer):
    scene = textured(120, 90)
    fog = np.full_like(scene, 225)
    hazy = cv2.addWeighted(scene, 0.25, fog, 0.75, 0)
    assert enhancer.get_contrast(hazy) < ml_config.fog_contrast_threshold
    assert enhancer.get_brightness(hazy) > ml_config.fog_brightness_threshold

    enhanced, changed, mode = enhancer.check_and_enhance(hazy)
    assert changed is True and mode == "dehazing"
    assert enhancer.get_contrast(enhanced) > enhancer.get_contrast(hazy) * 1.5


def test_grayscale_and_bgra_inputs(enhancer):
    gray = np.full((120, 160), 20, dtype=np.uint8)
    enhanced, _, mode = enhancer.check_and_enhance(gray)
    assert enhanced.ndim == 3 and enhanced.shape[2] == 3 and mode == "night_enhanced"
    bgra = np.zeros((10, 10, 4), dtype=np.uint8)
    assert ensure_bgr(bgra).shape == (10, 10, 3)


def test_dehazing_output_is_valid_on_flat_image(enhancer):
    flat = np.full((64, 64, 3), 200, dtype=np.uint8)
    result = enhancer.enhance_dehazing(flat)
    assert result.shape == flat.shape and result.dtype == np.uint8


def test_night_hours(enhancer):
    assert enhancer.is_night_time(NIGHT) is True
    assert enhancer.is_night_time(DAY) is False
    assert ml_config.is_night_hour(23) and ml_config.is_night_hour(0) and ml_config.is_night_hour(4)
    assert not ml_config.is_night_hour(5) and not ml_config.is_night_hour(21)
