"""Inspect real deployed service contracts without restarting or rewriting them."""
from pathlib import Path
import warnings

import pytest

from checks.yogabook.platform import attribute, numeric, sensors
from ybcheck.plugin import HardwareWarning


def unit_state(shell, unit, evidence, key):
    result = shell("systemctl", "show", unit, "--no-pager", "--property=LoadState,ActiveState,SubState,Result,MainPID,ExecMainStatus,NRestarts,FragmentPath,ExecStart,WorkingDirectory", timeout=6)
    if result.returncode and ("Access denied" in result.stderr or "Failed to connect to bus" in result.stderr):
        pytest.skip(f"blocked: cannot query system manager: {result.stderr.strip()}")
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    evidence(key, {"unit": unit, "returncode": result.returncode, "properties": values, "stderr": result.stderr})
    assert result.returncode == 0, result.stderr
    assert values.get("LoadState") == "loaded", f"required {unit} missing/unloadable: {values}"
    assert values.get("ActiveState") != "failed", f"required {unit} failed: {values}"
    return values


@pytest.mark.parametrize("unit", [
    pytest.param("touch-keyboard-handler.service", marks=pytest.mark.hardware(id="services.touch_keyboard", component="touch.halo", layer="userspace")),
    pytest.param("yogabook-lid-watch.service", marks=pytest.mark.hardware(id="services.lid_watch", component="power.lid", layer="userspace")),
])
def test_required_daemon(unit, shell, evidence, require_program):
    require_program("systemctl")
    values = unit_state(shell, unit, evidence, "service")
    assert values.get("ActiveState") == "active" and int(values.get("MainPID", "0")) > 0, f"required persistent daemon is not running: {values}"
    assert values.get("Result") == "success" and values.get("ExecMainStatus") == "0", values
    if int(values.get("NRestarts", "0")):
        warnings.warn(f"{unit} has restarted {values['NRestarts']} times this activation; inspect current-boot journal", HardwareWarning)
    evidence("limitations", "Loaded/active daemon and process status only; no service modifications. A running handler does not prove typing, physical lid cancellation/wakeup or repeated resume.")


@pytest.mark.hardware(id="services.touch_keyboard.configuration", component="touch.halo", layer="userspace")
def test_touch_keyboard_configuration(shell, evidence, require_program):
    require_program("udevadm")
    source = Path("/dev/touch_keyboard")
    assert source.exists(), "mandatory /dev/touch_keyboard source alias missing"
    result = shell("udevadm", "info", "--query=property", f"--name={source}", timeout=5)
    assert result.returncode == 0, result.stderr
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    layout = Path("/etc/touch_keyboard/layout.csv")
    evidence("touch_keyboard_config", {"source": str(source.resolve()), "properties": properties, "layout": str(layout.resolve())})
    assert properties.get("TOUCH_KEYBOARD") == "1", "Goodix source lacks touch-keyboard udev classification"
    assert properties.get("LIBINPUT_IGNORE_DEVICE") == "1", "libinput must not also claim the Halo source touchscreen"
    assert "touch-keyboard-handler.service" in properties.get("SYSTEMD_WANTS", "").split(), properties
    assert layout.is_file(), "touch-keyboard layout.csv missing"
    contents = attribute(shell, layout)
    assert len(contents.splitlines()) > 1, "empty/invalid keyboard layout"
    # This physical US unit's accepted layout is pc104; name is not a parser ABI.
    if layout.resolve().name != "YB1-X9x-pc104.csv":
        warnings.warn("US Yoga Book previously required YB1-X9x-pc104.csv; current layout differs/customized. Verify Enter/backslash/Fn keys physically", HardwareWarning)
    evidence("limitations", "Actual udev properties and readable selected layout, not proof of key mapping or daemon consumption. Physical Enter/backslash/Fn and touchpad acceptance remains independent.")


@pytest.mark.hardware(id="services.sensor_proxy.acquisition", component="sensors.rotation", layer="userspace")
def test_sensor_proxy_acquisition(shell, evidence, require_program):
    require_program("systemctl")
    require_program("busctl")
    before = unit_state(shell, "iio-sensor-proxy.service", evidence, "sensor_proxy_before")
    # This system-bus query intentionally permits normal D-Bus activation.
    result = shell("busctl", "--system", "--timeout=5", "get-property", "net.hadess.SensorProxy", "/net/hadess/SensorProxy", "net.hadess.SensorProxy", "HasAccelerometer", timeout=7)
    if result.returncode and any(token in result.stderr.lower() for token in ("access denied", "permission denied", "not authorized")):
        pytest.skip(f"blocked: sensor-proxy D-Bus access: {result.stderr.strip()}")
    assert result.returncode == 0, f"sensor-proxy activation/property acquisition failed: {result.stderr}"
    assert result.stdout.strip() == "b true", f"sensor-proxy cannot see an accelerometer: {result.stdout}"
    claim = shell("busctl", "--system", "--timeout=5", "call", "net.hadess.SensorProxy", "/net/hadess/SensorProxy", "net.hadess.SensorProxy", "ClaimAccelerometer", timeout=7)
    if claim.returncode and any(token in claim.stderr.lower() for token in ("access denied", "permission denied", "not authorized")):
        pytest.skip(f"blocked: sensor-proxy claim policy: {claim.stderr.strip()}")
    assert claim.returncode == 0, f"sensor-proxy accelerometer claim failed: {claim.stderr}"
    after = unit_state(shell, "iio-sensor-proxy.service", evidence, "sensor_proxy_after")
    assert after.get("ActiveState") == "active", f"sensor proxy not active after D-Bus acquisition: {after}"
    evidence("activation", {"before": before.get("ActiveState"), "after": after.get("ActiveState"), "has_accelerometer": result.stdout.strip(), "claim_succeeded": True})
    evidence("limitations", "Inactive before D-Bus acquisition is normal, not failure. ClaimAccelerometer succeeds for the short-lived busctl owner and is released when it disconnects. No persistent claim/service change, physical tilt or Mutter auto-rotation proven. Legacy monitor-sensor/yogabook-modes-handler are not mandatory sleep owners.")


@pytest.mark.hardware(id="services.sensor_proxy.configuration", component="sensors.rotation", layer="userspace")
def test_sensor_proxy_configuration(shell, evidence, require_program):
    require_program("udevadm")
    accelerometers = [item for item in sensors() if item["kind"] == "accelerometer"]
    assert accelerometers, "no accelerometers available for userspace metadata inspection"
    properties = []
    for item in accelerometers:
        result = shell("udevadm", "info", "--query=property", f"--path={item['path']}", timeout=5)
        assert result.returncode == 0, result.stderr
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        properties.append({"path": item["path"], "resolved": item["resolved"],
                           "location": values.get("ACCEL_LOCATION"), "mount_matrix": values.get("ACCEL_MOUNT_MATRIX")})
    evidence("accelerometer_userspace_metadata", properties)
    assert {"base", "display"} <= {item["location"] for item in properties}, "udev must distinguish base/display accelerometers; do not infer them from instance numbers"
    for item in properties:
        if item["location"] == "base":
            matrix = item["mount_matrix"]
            assert matrix, f"base accelerometer mount matrix missing: {item}"
            assert len(numeric(matrix.replace(";", " "))) == 9, f"invalid base accelerometer mount matrix: {item}"
    evidence("limitations", "Actual udev base/display classification and finite base matrix only. Physical mapping, mounting correctness and GNOME tilt response require human stimulus; no boot-dependent sensor instance suffix is assumed.")
