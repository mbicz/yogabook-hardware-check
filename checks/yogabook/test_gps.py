"""BCM4752 identity, real NMEA acquisition, daemon health, and outdoor fix."""
from pathlib import Path
import os
import shutil
import stat
import warnings

import pytest

from checks.yogabook.gps import gps_uarts, parse_nmea, rfkill_devices, run_launcher
from ybcheck.plugin import HardwareWarning


@pytest.fixture(scope="session")
def gps_capture(request, tmp_path_factory):
    """Acquire once across all GPS consumers, or read an explicitly supplied file."""
    config = request.config
    supplied = config.getoption("gps_nmea")
    launch = None
    if supplied:
        output = Path(supplied).expanduser()
        source = "read-only capture (not evidence of current hardware health)"
    else:
        active = config.getoption("active") or []
        if not ({"gps", "all"} & set(active)):
            pytest.skip("blocked: use --active gps with --gps-launcher PATH --gps-runtime PATH, "
                        "or --gps-nmea PATH for read-only capture analysis")
        launcher_option = config.getoption("gps_launcher")
        runtime_option = config.getoption("gps_runtime")
        if not launcher_option or not runtime_option:
            pytest.skip("blocked: supply --gps-launcher PATH to the functioning stock run_gps.py "
                        "and --gps-runtime PATH to its privately prepared Android runtime")
        launcher = Path(launcher_option).expanduser().resolve()
        runtime = Path(runtime_option).expanduser().resolve()
        if not launcher.is_file() or not runtime.is_dir():
            pytest.skip("blocked: --gps-launcher must be an existing Python file and --gps-runtime "
                        "an existing prepared runtime directory")
        required = [runtime / name for name in ("system/bin/glgps_intelx86android", "vendor/etc/gps-debian.xml")]
        if any(not path.is_file() for path in required):
            pytest.skip("blocked: prepare the stock runtime with its original daemon and gps-debian.xml; "
                        "proprietary binaries are intentionally not distributed here")
        if not all((runtime / name).is_dir() for name in ("dev", "proc", "data/gps")):
            pytest.skip("blocked: prepared GPS runtime needs dev, proc and data/gps directories")
        if os.geteuid() != 0:
            pytest.skip("blocked: run the configured GPS launcher check as root")
        for name in ("unshare", "mount", "chroot", "fuser"):
            if shutil.which(name) is None:
                pytest.skip(f"blocked: install required launcher program {name}")
        seconds = config.getoption("gps_seconds")
        if type(seconds) is not int or seconds <= 0:
            pytest.skip("blocked: --gps-seconds must be a positive integer")
        try:
            uarts = gps_uarts()
        except OSError as error:
            pytest.skip(f"blocked: cannot inspect GPS UART firmware association: {error}")
        assert len(uarts) == 1, f"expected one firmware-identified GPS UART, found {uarts}"
        if uarts[0]["tty"] != "ttyS2":
            pytest.skip(f"blocked: existing stock launcher is wired to /dev/ttyS2 but firmware identifies "
                        f"/dev/{uarts[0]['tty']}; adapt that launcher before active acquisition")
        output = tmp_path_factory.mktemp("gps") / "receiver.nmea"
        source = "active stock launcher"
        try:
            launch = run_launcher(launcher, runtime, seconds, output)
        except OSError as error:
            pytest.skip(f"blocked: cannot execute configured GPS launcher: {error}")
    read_error = None
    data = b""
    try:
        if not stat.S_ISREG(output.stat().st_mode):
            raise OSError("NMEA capture must be a regular file, not a FIFO/device")
        with output.open("rb") as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise OSError("NMEA capture exceeds the 16 MiB analysis bound")
    except OSError as error:
        if supplied:
            pytest.skip(f"blocked: cannot read --gps-nmea capture: {error}")
        read_error = str(error)
        data = b""
    return {"source": source, "path": str(output), "nmea": parse_nmea(data),
            "launcher": launch, "read_error": read_error}


def _capture_evidence(capture, evidence):
    evidence("gps_source", capture["source"])
    evidence("gps_capture", capture["path"])
    evidence("gps_nmea", capture["nmea"])
    evidence("gps_capture_read_error", capture["read_error"])
    launch = capture["launcher"]
    if launch:
        evidence("gps_launcher", {"returncode": launch.returncode, "stdout": launch.stdout,
                                  "stderr": launch.stderr, "result": launch.summary,
                                  "timed_out": launch.timed_out, "forced_kill": launch.forced_kill})


@pytest.mark.hardware(id="gps.rfkill", component="gps", layer="enumeration")
def test_gps_rfkill(evidence):
    try:
        entries = rfkill_devices()
    except OSError as error:
        pytest.skip(f"blocked: cannot read GPS rfkill identity: {error}")
    evidence("gps_rfkill", [str(entry) for entry in entries])
    assert len(entries) == 1, "expected one rfkill with name BCM4752:00 and type gps"
    try:
        state = (entries[0] / "state").read_text().strip()
    except OSError as error:
        pytest.skip(f"blocked: cannot read GPS rfkill state: {error}")
    evidence("gps_rfkill_state", state)
    assert state in {"0", "1"}, f"invalid rfkill state {state!r}"


@pytest.mark.hardware(id="gps.uart", component="gps", layer="enumeration")
def test_gps_uart(evidence):
    try:
        uarts = gps_uarts()
    except OSError as error:
        pytest.skip(f"blocked: cannot inspect GPS UART firmware links: {error}")
    evidence("gps_uarts", uarts)
    assert len(uarts) == 1, f"expected one ACPI-associated GPS UART, found {uarts}"
    device = Path("/dev") / uarts[0]["tty"]
    assert device.exists(), f"firmware-identified GPS UART is missing: {device}"
    assert stat.S_ISCHR(device.stat().st_mode), f"GPS UART is not a character device: {device}"
    if uarts[0]["association"] != "firmware":
        warnings.warn("GPS UART identity uses this board's documented ACPI controller 8086228A:01 "
                      "(UART2); sysfs does not expose a direct BCM4752 SerialBus association. "
                      "Enumeration is not receiver-operation proof.", HardwareWarning)


@pytest.mark.hardware(id="gps.nmea", component="gps", layer="headless")
def test_gps_nmea(gps_capture, evidence):
    _capture_evidence(gps_capture, evidence)
    assert gps_capture["read_error"] is None, f"launcher produced no readable NMEA capture: {gps_capture['read_error']}"
    nmea = gps_capture["nmea"]
    assert nmea["valid_sentences"] > 0, "no checksum-valid, structurally valid NMEA sentences received"
    if gps_capture["launcher"] is None:
        warnings.warn("Read-only historical NMEA capture validates recorded receiver output only; "
                      "it is not proof of current hardware operation after a kernel upgrade. "
                      "Repeat with --active gps and the stock launcher/runtime.", HardwareWarning)
    if nmea["invalid_sentences"]:
        warnings.warn(f"NMEA capture contains {nmea['invalid_sentences']} rejected/corrupt sentences "
                      "alongside valid receiver output", HardwareWarning)


@pytest.mark.hardware(id="gps.daemon", component="gps", layer="userspace")
def test_gps_daemon(gps_capture, evidence):
    _capture_evidence(gps_capture, evidence)
    launch = gps_capture["launcher"]
    if launch is None:
        pytest.skip("blocked: --gps-nmea contains no daemon lifecycle status; run --active gps "
                    "with the stock launcher/runtime to check daemon shutdown")
    error = launch.daemon_error(gps_capture["nmea"])
    assert error is None, error


@pytest.mark.hardware(id="gps.fix", component="gps", layer="headless")
def test_gps_fix(gps_capture, evidence):
    _capture_evidence(gps_capture, evidence)
    nmea = gps_capture["nmea"]
    assert nmea["valid_sentences"] > 0, "cannot assess positioning without valid NMEA receiver output"
    if gps_capture["launcher"] is None:
        warnings.warn("Read-only historical NMEA capture assesses recorded positioning only; "
                      "it is not proof of a current hardware position fix after a kernel upgrade. "
                      "Repeat with --active gps and the stock launcher/runtime.", HardwareWarning)
    if not nmea["fix_sentences"]:
        warnings.warn("GPS receiver emitted valid NMEA but no validated GGA quality>0 or active RMC fix. "
                      "Indoor/obstructed sky or acquisition time can prevent positioning; repeat outdoors "
                      "with a longer --gps-seconds interval. Satellite visibility is not a fix.", HardwareWarning)
