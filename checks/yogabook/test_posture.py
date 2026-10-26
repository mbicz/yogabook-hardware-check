"""Converted hinge diagnostics and current mode policy, never sleep decisions."""
import errno
import json
from pathlib import Path
import sys
import time
import warnings

import pytest

from checks.yogabook.platform import sensors, text
from checks.yogabook.posture import (UnitsUnavailable, conversion_attributes, hinge_frame,
                                    hub_agreement, labeled_channels, planar_agreement)
from checks.yogabook.power import parse_properties
from ybcheck.plugin import HardwareWarning


# Broad diagnostic disagreement band, NOT a pose boundary or accuracy spec.
# The two hub estimates and projected half-angles need not exactly coincide.
AGREEMENT_WARNING_DEGREES = 30


def hinge_devices(evidence):
    found = [item for item in sensors() if item["kind"] == "hinge"]
    evidence("hinge_instances", found)
    assert len(found) >= 2, "expected both sensor-hub hinge instances"
    for item in found:
        labeled_channels(item["labels"])
    return found


def hinge_snapshot(shell, paths):
    result = shell(sys.executable, str(Path(__file__).with_name("power.py")), "read",
                   *(str(path) for path in paths), timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.hardware(id="posture.hinge.converted", component="sensors.hinge", layer="headless")
def test_hinge_converted_readback(shell, evidence):
    devices = hinge_devices(evidence)
    paths, channels = [], {}
    for item in devices:
        # Only this source-reviewed driver's in_angl ABI establishes radians.
        names = {name.replace("-", "_") for name in item["drivers"].values()}
        if "hid_sensor_custom_intel_hinge" not in names:
            pytest.skip(f"blocked: angular unit contract unavailable for driver set {names}")
        path = Path(item["path"])
        channels[str(path)] = labeled_channels(item["labels"])
        available = set(item["channels"])
        try:
            for channel in channels[str(path)].values():
                scale, offset = conversion_attributes(channel, available)
                paths.extend(path / name for name in (f"{channel}_raw", scale, offset))
        except UnitsUnavailable as error:
            pytest.skip(f"blocked: {path}: {error}")
    paths = sorted(set(paths))
    samples = []
    for index in range(2):
        sample = hinge_snapshot(shell, paths)
        samples.append(sample)
        evidence("hinge_raw_snapshots", samples)
        errors = sample["errors"]
        assert not {name: error for name, error in errors.items()
                    if error["errno"] not in {errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENODEV, errno.EBUSY, errno.EAGAIN}}, errors
        if errors:
            pytest.skip(f"blocked: hinge telemetry unavailable: {errors}")
        if index == 0:
            time.sleep(0.1)
    converted = []
    for sample in samples:
        frames = {}
        for path, mapping in channels.items():
            values = {Path(name).name: value for name, value in sample["values"].items() if str(Path(name).parent) == path}
            try:
                frame = hinge_frame(mapping, values, "radians")
            except UnitsUnavailable as error:
                pytest.skip(f"blocked: {path}: {error}")
            except ValueError as error:
                pytest.fail(f"{path}: invalid hinge angle/conversion: {error}")
            frames[path] = frame
            agreement = planar_agreement(frame)
            evidence(f"projected_agreement:{path}:{len(converted)}", agreement)
            if agreement["difference_degrees"] > AGREEMENT_WARNING_DEGREES:
                warnings.warn(f"{path}: hinge/projected-half disagreement {agreement['difference_degrees']:.1f} degrees; 2-D projections, orientation and sequential motion can differ. Not an exact-match requirement.", HardwareWarning)
        converted.append(frames)
        evidence("hinge_converted_snapshots", converted)
        reference, *others = frames.items()
        for path, frame in others:
            differences = hub_agreement(reference[1], frame)
            evidence(f"hub_disagreement:{path}:{len(converted)}", differences)
            if max(differences.values()) > AGREEMENT_WARNING_DEGREES:
                warnings.warn(f"Hinge hubs disagree by more than {AGREEMENT_WARNING_DEGREES} degrees: {differences}; check motion/calibration rather than treating one estimate as truth.", HardwareWarning)
    evidence("position_interpretation", {
        "classification": "not assigned: no live physical pose/calibration observation",
        "unit_basis": "source-reviewed HID hinge IIO_ANGL ABI; degrees=(raw+offset)*scale*180/pi",
        "agreement_warning_degrees": AGREEMENT_WARNING_DEGREES,
        "limits": "Full-turn circular diagnostics, not calibrated 3-D pose. Historical laptop/closed/fold/flat samples are references, not thresholds. Near-zero hinge cannot distinguish closed from 360-fold. Physical SW_LID is independently queried by input.lid.headless; virtual SW_TABLET_MODE is software rotation policy, not physical posture.",
    })


def policy_query(shell, evidence, *argv):
    result = shell(*argv, timeout=10)
    evidence("policy_query:" + " ".join(argv), {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
    absent_unit = argv[:2] == ("systemctl", "show") and parse_properties(result.stdout).get("LoadState") == "not-found"
    if result.returncode and not absent_unit:
        pytest.skip(f"blocked: cannot query posture policy: {' '.join(argv)}: {result.stderr}")
    return result.stdout


@pytest.mark.hardware(id="posture.mode.services", component="posture.policy", layer="userspace")
def test_posture_services(shell, evidence, require_program):
    require_program("systemctl")
    services = {}
    for service in ("yogabook-lid-watch.service", "yogabook-modes-handler.service", "touch-keyboard-handler.service", "yogabook-hinge-watch.service"):
        props = parse_properties(policy_query(shell, evidence, "systemctl", "show", service, "--no-pager",
                                              "--property=LoadState,ActiveState,SubState,UnitFileState,ExecStart,WorkingDirectory"))
        services[service] = props
        active = props.get("ActiveState") == "active"
        if service == "yogabook-hinge-watch.service":
            if active:
                warnings.warn("Obsolete angle-based hinge watcher is active; current policy must use physical SW_LID only.", HardwareWarning)
        elif not active:
            reason = " (pen mode may intentionally remove the Halo source)" if service == "touch-keyboard-handler.service" else ""
            warnings.warn(f"{service} is not active{reason}; readable hardware does not prove userspace mode/lid behavior.", HardwareWarning)
        if service == "yogabook-lid-watch.service" and active and "/usr/local/sbin/yogabook-lid-watch" not in props.get("ExecStart", ""):
            warnings.warn("Active lid watcher uses an unexpected executable; recorded physical-switch policy cannot be assumed.", HardwareWarning)
    evidence("mode_services", services)
    layout = Path("/etc/touch_keyboard/layout.csv")
    evidence("keyboard_layout", {"path": str(layout), "resolved": str(layout.resolve()), "exists": layout.is_file()})
    if services["touch-keyboard-handler.service"].get("ActiveState") == "active":
        assert layout.is_file(), "active touch-keyboard handler has no selected layout file"
        working_directory = services["touch-keyboard-handler.service"].get("WorkingDirectory")
        if working_directory != "/etc/touch_keyboard":
            warnings.warn(f"Unexpected touch-keyboard WorkingDirectory={working_directory!r}; relative layout lookup may differ.", HardwareWarning)
    evidence("limitations", "Live service/configuration readback only. SW_LID ioctl stays in input.lid.headless, separate from converted hinge. No events injected or service restarted; no switch>=angle threshold, keyboard recovery, touchscreen rebind or physical suspend/resume conclusion.")


@pytest.mark.hardware(id="posture.tablet_switch.policy", component="posture.policy", layer="userspace")
def test_tablet_switch_policy(shell, evidence, require_program):
    require_program("udevadm")
    devices = {}
    for event in Path("/sys/class/input").glob("event*"):
        name = text(event / "device/name")
        if name in {"Lenovo Yoga Book Tablet Mode Switch", "Lenovo Yoga Book Posture Switch",
                    "virtual-tablet-switch"}:
            devices[name] = event
    evidence("tablet_mode_devices", {name: str(path) for name, path in devices.items()})
    assert "virtual-tablet-switch" not in devices, (
        "a synthetic virtual-tablet-switch is present: it claims tablet mode in every pose "
        "and competes with the posture switch")
    if "Lenovo Yoga Book Tablet Mode Switch" not in devices:
        pytest.skip("blocked: lenovo-yogabook tablet mode switch not present (kernel without the patch)")
    # Desktops follow the last event of any tablet mode switch, so libinput must
    # see exactly one: yogabook-modes-handler's posture switch (folded, flat or
    # tent), not the kernel's hall-sensor switch (folded only).
    assert "Lenovo Yoga Book Posture Switch" in devices, (
        "yogabook-modes-handler's posture switch is missing: desktops get no tablet mode "
        "while the machine lies flat or stands in tent pose")
    kernel = parse_properties(policy_query(shell, evidence, "udevadm", "info", "--query=property",
                                           "--path", str(devices["Lenovo Yoga Book Tablet Mode Switch"])))
    posture = parse_properties(policy_query(shell, evidence, "udevadm", "info", "--query=property",
                                            "--path", str(devices["Lenovo Yoga Book Posture Switch"])))
    assert kernel.get("LIBINPUT_IGNORE_DEVICE") == "1", (
        f"libinput uses the kernel hall-sensor switch next to the posture switch: {kernel}")
    assert posture.get("ID_INPUT_SWITCH") == "1" and posture.get("LIBINPUT_IGNORE_DEVICE") != "1", (
        f"posture switch is not a libinput switch: {posture}")
    evidence("limitations", "Udev classification only. The posture value follows the hinge angle and the fold sensor; desktop auto-rotation is observed separately.")
