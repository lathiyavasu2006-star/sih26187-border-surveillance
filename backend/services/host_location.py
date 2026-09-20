"""Position of the machine running the backend, read from the Windows Location Service.

Local webcams (device-index cameras) are wired to this machine, so its position is the camera's real
position. Browsers embedded in kiosk / desktop shells often have geolocation blocked; this gives the console
a location source that does not depend on the browser. The OS location privacy switch still applies: when
the operator has turned location off in Windows, nothing is returned.
"""
import asyncio
import json
import logging
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sih26187.host_location")

CACHE_SECONDS = 60.0
PROCESS_TIMEOUT_SECONDS = 30.0

# Run as a file: -EncodedCommand is held indefinitely by AMSI/Defender on this class of machine.
_SCRIPT_PATH = Path(__file__).with_name("host_location.ps1")


class HostLocationUnavailable(Exception):
    """The host position cannot be determined; the message is safe to show to an operator."""


@dataclass(frozen=True)
class HostFix:
    lat: float
    lng: float
    accuracy_m: Optional[int]
    captured_at: datetime
    source: str = "windows-location-service"


_cache: Optional[HostFix] = None
_cache_at = 0.0
_lock = asyncio.Lock()


def _supported() -> bool:
    return sys.platform == "win32"


def _run_powershell() -> str:
    """Runs the watcher script and returns its stdout. Separate so tests can replace it."""
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(_SCRIPT_PATH)],
        stdin=subprocess.DEVNULL,  # a redirected-but-open stdin makes powershell.exe wait forever
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        logger.warning("Location script failed (%s): %s", completed.returncode, completed.stderr.strip()[:500])
        raise HostLocationUnavailable("The Windows location service could not be queried")
    return completed.stdout


def parse_fix(output: str) -> HostFix:
    """Turns the script's JSON into a fix, or raises with the reason the OS gave."""
    line = next((item for item in reversed(output.strip().splitlines()) if item.strip().startswith("{")), "")
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise HostLocationUnavailable("The Windows location service returned no data") from exc

    if data.get("permission") == "Denied":
        raise HostLocationUnavailable(
            "Location is turned off for this computer — enable it in Windows Settings › Privacy & security › Location"
        )
    if data.get("status") in ("Disabled", "NoData") and not data.get("known"):
        raise HostLocationUnavailable("Windows location service is disabled or has no position source (Wi-Fi/GPS)")
    lat, lng = data.get("lat"), data.get("lng")
    if not data.get("known") or not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        raise HostLocationUnavailable("Windows could not determine this computer's position in time — try again")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or math.isnan(lat) or math.isnan(lng):
        raise HostLocationUnavailable("Windows returned an invalid position")
    accuracy = data.get("accuracy")
    accuracy_m = int(round(accuracy)) if isinstance(accuracy, (int, float)) and math.isfinite(accuracy) else None
    return HostFix(
        lat=round(float(lat), 6),
        lng=round(float(lng), 6),
        accuracy_m=accuracy_m,
        captured_at=datetime.now(timezone.utc),
    )


async def read_host_location(force: bool = False) -> HostFix:
    """Current host position. Cached for a minute and serialised, so repeated clicks spawn one process."""
    global _cache, _cache_at
    if not _supported():
        raise HostLocationUnavailable("Server-side location is only available on the Windows console host")
    async with _lock:
        if not force and _cache is not None and time.monotonic() - _cache_at < CACHE_SECONDS:
            return _cache
        try:
            output = await asyncio.to_thread(_run_powershell)
        except subprocess.TimeoutExpired as exc:
            raise HostLocationUnavailable("Windows location service timed out") from exc
        except OSError as exc:
            raise HostLocationUnavailable("PowerShell is not available to query the location service") from exc
        fix = parse_fix(output)
        _cache, _cache_at = fix, time.monotonic()
        return fix


def clear_cache() -> None:
    global _cache, _cache_at
    _cache, _cache_at = None, 0.0
