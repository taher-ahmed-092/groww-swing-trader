"""Phone gyroscope tilt is fully removed from the dashboard — desktop mouse
parallax stays as the only tilt-like effect."""
from __future__ import annotations

from pathlib import Path

_HTML = Path("dashboard/dashboard.html").read_text(encoding="utf-8")


def test_tilt_button_absent():
    assert "enable-tilt" not in _HTML
    assert "Enable Tilt" not in _HTML


def test_device_orientation_code_absent():
    assert "DeviceOrientationEvent" not in _HTML
    assert "deviceorientation" not in _HTML
    assert "startTilt" not in _HTML


def test_mouse_parallax_still_present():
    assert "mousemove" in _HTML
    assert "tiltX" in _HTML and "tiltY" in _HTML
