"""Pen orientation is Mutter's built-in-panel mapping, not a udev rotation.

Mutter 43.8 maps the libwacom-integrated pen to the built-in DSI panel and applies
the panel-orientation + display-rotation transform. An explicit per-user `output`
value can never match this EDID-less panel and leaves the pen unmapped; a udev
LIBINPUT_CALIBRATION_MATRIX adds a second rotation outside GNOME. See docs/05.
"""
import os
from pathlib import Path
import warnings

import pytest

from checks.yogabook.pen import device_mapping, graphical_session, libwacom_devices
from ybcheck.plugin import HardwareWarning

PEN = "Wacom HID 169 Pen"
SCHEMA = "org.gnome.desktop.peripherals.tablet:/org/gnome/desktop/peripherals/tablets/056a:0169/"


def pen_nodes():
    return [f"/dev/input/{path.name}" for path in sorted(Path("/sys/class/input").glob("event*"))
            if (path / "device/name").is_file() and (path / "device/name").read_text().strip() == PEN
            and (path / "device/phys").read_text().strip() == "i2c-wacom"]


def required_pen(evidence):
    nodes = pen_nodes()
    evidence("pen_nodes", nodes)
    assert len(nodes) == 1, f"expected one bound i2c-wacom pen event node, found {nodes}"
    return nodes[0]


@pytest.mark.hardware(id="input.pen.libwacom_integration", component="input.pen", layer="userspace")
def test_pen_libwacom_integration(shell, require_program, evidence):
    node = required_pen(evidence)
    result = shell(require_program("libwacom-list-local-devices"), "--format=datafile", timeout=10)
    assert result.returncode == 0, result.stderr
    devices = [item for item in libwacom_devices(result.stdout) if node in item["nodes"]]
    evidence("libwacom_match", devices)
    assert len(devices) == 1, f"libwacom does not recognize {node}; install device/libwacom/wacom-hid-169-pen.tablet"
    assert "System" in devices[0]["integrated_in"], (
        "pen must be IntegratedIn=System so Mutter maps it to the built-in panel")


@pytest.mark.hardware(id="input.pen.no_udev_rotation", component="input.pen", layer="userspace")
def test_pen_has_no_competing_udev_rotation(shell, evidence):
    node = required_pen(evidence)
    result = shell("udevadm", "info", "-q", "property", "-n", node, timeout=5)
    assert result.returncode == 0, result.stderr
    calibration = [line for line in result.stdout.splitlines() if line.startswith("LIBINPUT_CALIBRATION_MATRIX=")]
    evidence("udev_calibration", calibration)
    if calibration:
        warnings.warn(f"{node} carries {calibration[0]}; Mutter overrides it for this integrated pen, "
                      "but other compositors would rotate twice. Remove the pen udev rule.", HardwareWarning)


def session_bus_call(shell, session, *argv, timeout=10):
    environment = ["env", f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{session['uid']}/bus"]
    if os.geteuid() == session["uid"]:
        return shell(*environment, *argv, timeout=timeout)
    if os.geteuid() != 0:
        pytest.skip(f"blocked: run as root or as session user {session['user']} to query its desktop mapping")
    return shell("runuser", "-u", session["user"], "--", *environment, *argv, timeout=timeout)


@pytest.fixture
def desktop_session(shell, require_program, evidence):
    loginctl = require_program("loginctl")
    listed = shell(loginctl, "list-sessions", "--no-legend", timeout=5)
    assert listed.returncode == 0, listed.stderr
    blocks = []
    for line in listed.stdout.splitlines():
        session_id = line.split()[0] if line.split() else None
        if session_id:
            shown = shell(loginctl, "show-session", session_id, "-p", "User", "-p", "Name", "-p", "Type",
                          "-p", "Class", "-p", "Active", "-p", "Seat", timeout=5)
            if shown.returncode == 0:
                blocks.append(shown.stdout)
    session = graphical_session(blocks)
    evidence("graphical_session", session)
    if session is None:
        pytest.skip("blocked: no active graphical user session; log in to GNOME to check pen mapping")
    return session


@pytest.mark.hardware(id="input.pen.no_output_override", component="input.pen", layer="userspace")
def test_pen_has_no_unmatchable_output_override(shell, require_program, desktop_session, evidence):
    require_program("dconf")
    result = session_bus_call(shell, desktop_session, "dconf", "read",
                              "/org/gnome/desktop/peripherals/tablets/056a:0169/output")
    assert result.returncode == 0, result.stderr
    evidence("user_output_value", result.stdout.strip())
    assert not result.stdout.strip(), (
        f"user {desktop_session['user']} has output={result.stdout.strip()}; it can never match the EDID-less "
        f"panel and disables automatic mapping. Run as that user: gsettings reset {SCHEMA} output")


@pytest.mark.hardware(id="input.pen.mapped_to_panel", component="input.pen", layer="userspace")
def test_pen_mapped_to_builtin_panel(shell, require_program, desktop_session, evidence):
    node = required_pen(evidence)
    require_program("gdbus")
    mapping = session_bus_call(
        shell, desktop_session, "gdbus", "call", "--session", "--dest", "org.gnome.Mutter.InputMapping",
        "--object-path", "/org/gnome/Mutter/InputMapping",
        "--method", "org.gnome.Mutter.InputMapping.GetDeviceMapping", node)
    evidence("mutter_mapping", {"stdout": mapping.stdout.strip(), "stderr": mapping.stderr.strip()})
    if "ServiceUnknown" in mapping.stderr:
        pytest.skip("blocked: Mutter InputMapping service is not available in this session")
    assert mapping.returncode == 0, f"Mutter has not mapped the pen to any output: {mapping.stderr.strip()}"
    rectangle = device_mapping(mapping.stdout)
    evidence("mapped_rectangle", rectangle)
    evidence("limitations", "Mapping state only. Physical corner accuracy and rotation-following "
                            "remain the human.input.pen.mapping observation.")
