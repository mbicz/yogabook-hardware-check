"""Existing desktop nodes, not stream activation or physical media acceptance."""
import json
import warnings

import pytest

from checks.yogabook.desktop import desktop_session, session_bus_call
from ybcheck.plugin import HardwareWarning


@pytest.fixture
def pipewire_nodes(shell, require_program, desktop_session, evidence):
    program = require_program("pw-dump")
    result = session_bus_call(shell, desktop_session, program, timeout=10)
    if result.returncode and any(word in (result.stdout + result.stderr).lower()
                                 for word in ("permission denied", "access denied", "connection refused", "failed to connect", "host is down")):
        pytest.skip(f"blocked: cannot query user's PipeWire instance: {result.stderr.strip()}")
    assert result.returncode == 0, result.stderr
    nodes = [item.get("info", {}).get("props", {}) for item in json.loads(result.stdout)
             if item.get("type") == "PipeWire:Interface:Node"]
    evidence("pipewire_nodes", nodes)
    evidence("limitations", "Existing session graph only, no stream is opened. Node presence does not prove frames, sound, speech, correct routing or real application acceptance.")
    return nodes


@pytest.mark.parametrize("side", [pytest.param(side, marks=pytest.mark.hardware(
    id=f"camera.{side}.desktop_node", component=f"camera.{side}", layer="userspace"))
    for side in ("front", "rear")])
def test_camera_desktop_node(side, pipewire_nodes):
    assert any(item.get("node.name") == f"yb-camera-{side}" and item.get("media.class") == "Video/Source" for item in pipewire_nodes), f"Missing yb-camera-{side} PipeWire video source; check installed yogabook-camera/yb-camerad user service"


@pytest.mark.hardware(id="audio.desktop.physical_sink", component="audio", layer="userspace")
def test_audio_desktop_sink(pipewire_nodes):
    assert any(item.get("media.class") == "Audio/Sink" and
               item.get("api.alsa.path") == "hw:chtyogabook"
               for item in pipewire_nodes), "No chtyogabook speaker ALSA sink in user's PipeWire graph; Dummy Output or an external USB sink does not satisfy onboard audio routing support"


@pytest.mark.hardware(id="audio.desktop.microphone_source", component="audio.microphone", layer="userspace")
def test_audio_desktop_microphone(pipewire_nodes):
    assert any(item.get("media.class") == "Audio/Source" and
               item.get("api.alsa.path") == "hw:chtyogabook"
               for item in pipewire_nodes), "No chtyogabook microphone source in user's PipeWire graph; monitor/USB sources do not satisfy onboard microphone support"


@pytest.mark.hardware(id="services.desktop.failed_units", component="services.desktop", layer="userspace")
def test_desktop_failed_units(shell, require_program, desktop_session, evidence):
    require_program("systemctl")
    result = session_bus_call(shell, desktop_session, "systemctl", "--user", "--failed", "--no-legend", "--plain", "--no-pager", timeout=6)
    if result.returncode and any(token in result.stderr.lower() for token in ("failed to connect", "permission denied", "access denied")):
        pytest.skip(f"blocked: cannot query desktop user manager: {result.stderr.strip()}")
    assert result.returncode == 0, result.stderr
    evidence("failed_user_units", result.stdout)
    if result.stdout.strip():
        warnings.warn(f"Current desktop failed units (software health, not proof of broken hardware): {result.stdout.strip()}", HardwareWarning)
    evidence("limitations", "Read-only current failure list, including failures such as localsearch-3. No restart/reset-failed or inference of a hardware cause.")
