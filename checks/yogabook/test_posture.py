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
    require_program("libinput")
    devices = {}
    for event in Path("/sys/class/input").glob("event*"):
        name = text(event / "device/name")
        if name in {"virtual-tablet-switch", "virtual-touchpad"}:
            devices[name] = event
    evidence("virtual_mode_devices", {name: str(path) for name, path in devices.items()})
    if devices.keys() != {"virtual-tablet-switch", "virtual-touchpad"}:
        pytest.skip("blocked: virtual switch/touchpad pair not currently available; Halo pen mode can intentionally remove it")
    for name, event in devices.items():
        properties = parse_properties(policy_query(shell, evidence, "udevadm", "info", "--query=property", "--path", str(event)))
        expected = "ID_INPUT_SWITCH" if name == "virtual-tablet-switch" else "ID_INPUT_TOUCHPAD"
        assert properties.get(expected) == "1", f"{name} missing {expected}: {properties}"
        if name == "virtual-tablet-switch":
            assert properties.get("ID_INPUT_TOUCHPAD") != "1", "dedicated switch must not be dispatched as a touchpad"
    node = str(Path("/dev/input") / devices["virtual-touchpad"].name)
    quirks = parse_properties(policy_query(shell, evidence, "libinput", "quirks", "list", node))
    if quirks.get("ModelTabletModeNoSuspend") != "1":
        warnings.warn("Effective libinput ModelTabletModeNoSuspend=1 quirk missing; synthetic tablet pulses may suppress the touchpad. A snapshot does not prove pointer freeze or recovery.", HardwareWarning)
    evidence("limitations", "Udev dispatch tags/effective libinput quirk queried only. Software SW_TABLET_MODE OFF→ON pulses keep Mutter touch mode; their current state is not physical fold or lid evidence. Observe desktop auto-rotation and >40-second pointer behavior separately.")
