"""Low-light and haze enhancement applied before detection."""
from datetime import datetime
from typing import Tuple

import cv2
import numpy as np

from ml.config import ml_config

DCP_PATCH = 15
DCP_OMEGA = 0.95
DCP_MIN_TRANSMISSION = 0.1
ATMOSPHERE_TOP_FRACTION = 0.001


def ensure_bgr(frame: np.ndarray) -> np.ndarray:
    """Thermal and some IR cameras deliver single-channel frames."""
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    return frame


class NightEnhancer:
    def __init__(self):
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    def get_brightness(self, frame: np.ndarray) -> float:
        gray = cv2.cvtColor(ensure_bgr(frame), cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray))

    def get_contrast(self, frame: np.ndarray) -> float:
        gray = cv2.cvtColor(ensure_bgr(frame), cv2.COLOR_BGR2GRAY)
        return float(gray.std())

    def is_night_time(self, now: datetime = None) -> bool:
        return ml_config.is_night_hour((now or datetime.now()).hour)

    def enhance_clahe(self, frame: np.ndarray) -> np.ndarray:
        lab = cv2.cvtColor(ensure_bgr(frame), cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        enhanced = cv2.merge([self.clahe.apply(l_channel), a_channel, b_channel])
        return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    def enhance_dehazing(self, frame: np.ndarray) -> np.ndarray:
        """Dark channel prior dehazing (He et al.): estimate airlight and transmission, then recover."""
        image = ensure_bgr(frame).astype(np.float32) / 255.0
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (DCP_PATCH, DCP_PATCH))

        dark = cv2.erode(np.min(image, axis=2), kernel)
        flat_dark = dark.reshape(-1)
        count = max(1, int(flat_dark.size * ATMOSPHERE_TOP_FRACTION))
        brightest = np.argpartition(flat_dark, -count)[-count:]
        atmosphere = image.reshape(-1, 3)[brightest].max(axis=0)
        atmosphere = np.maximum(atmosphere, 1e-3)

        normalized_dark = cv2.erode(np.min(image / atmosphere, axis=2), kernel)
        transmission = 1.0 - DCP_OMEGA * normalized_dark
        transmission = cv2.GaussianBlur(transmission, (0, 0), sigmaX=DCP_PATCH / 3)
        transmission = np.clip(transmission, DCP_MIN_TRANSMISSION, 1.0)[:, :, np.newaxis]

        recovered = (image - atmosphere) / transmission + atmosphere
        return np.clip(recovered * 255.0, 0, 255).astype(np.uint8)

    @staticmethod
    def frame_statistics(frame: np.ndarray) -> Tuple[float, float]:
        """(brightness, contrast) from one grayscale conversion of a 1/4-scale copy (~16x fewer pixels)."""
        small = cv2.resize(frame, (max(1, frame.shape[1] // 4), max(1, frame.shape[0] // 4)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mean, std = cv2.meanStdDev(gray)
        return float(mean[0][0]), float(std[0][0])

    def check_and_enhance(self, frame: np.ndarray) -> Tuple[np.ndarray, bool, str]:
        frame = ensure_bgr(frame)
        brightness, contrast = self.frame_statistics(frame)

        if brightness < ml_config.night_brightness_threshold:
            return self.enhance_clahe(frame), True, "night_enhanced"

        # Fog / haze: bright but washed out.
        if contrast < ml_config.fog_contrast_threshold and brightness > ml_config.fog_brightness_threshold:
            return self.enhance_dehazing(frame), True, "dehazing"

        return frame, False, "normal"


night_enhancer = NightEnhancer()
