"""Synthetic receiver data only: these positions do not describe a real device."""
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from checks.yogabook.gps import (LauncherResult, decode_sentence, extract_launcher_result,
                                 gps_uarts, parse_nmea, rfkill_devices, run_launcher)


# Deliberately fabricated 10 N, 20 E, not captured device output.
GGA_FIX = "GPGGA,120000.00,1000.0000,N,02000.0000,E,1,06,1.2,12.0,M,0.0,M,,"
GGA_NOFIX = "GPGGA,120000.00,,,,,0,00,99.9,,,,,,"
RMC_FIX = "GPRMC,120000.00,A,1000.0000,N,02000.0000,E,0.0,0.0,010100,,,A"
RMC_NOFIX = "GPRMC,120000.00,V,,,,,,,010100,,,N"
GSV = "GPGSV,1,1,02,02,20,040,28,04,10,300,"


def nmea(body):
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"${body}*{checksum:02X}\r\n"


@pytest.mark.parametrize("body,kind,fix", [
    (GGA_FIX, "GGA", True), (GGA_NOFIX, "GGA", False),
    (RMC_FIX, "RMC", True), (RMC_NOFIX, "RMC", False), (GSV, "GSV", False),
])
def test_decode_supported_sentences(body, kind, fix):
    result = decode_sentence(nmea(body).encode("ascii"))
    assert result["type"] == kind
    assert result["fix"] is fix
    if fix:
        assert result["latitude"] == 10
        assert result["longitude"] == 20


def test_checksum_is_not_optional_or_approximate():
    good = nmea(GGA_FIX).rstrip()
    bad = good[:-2] + ("00" if good[-2:] != "00" else "01")
    for value in (bad, good[:-3], good + "00", " " + good, good.replace("$", "!"),
                  good.replace("*", "**"), b"\xff" + good.encode()):
        with pytest.raises((ValueError, UnicodeError)):
            decode_sentence(value)
    assert decode_sentence(good[:-2] + good[-2:].lower())["fix"] is True


@pytest.mark.parametrize("body", [
    "GPGGA,120000.00,1000.0000,N,02000.0000,E,1",  # truncated
    GGA_FIX.replace(",1,06,", ",bad,06,"),
    GGA_FIX.replace(",1,06,", ",9,06,"),
    GGA_FIX.replace("1000.0000", "1060.0000"),
    GGA_FIX.replace("1000.0000", "9100.0000"),
    GGA_FIX.replace(",N,", ",Q,"),
    GGA_FIX.replace("120000.00", "250000.00"),
    GGA_FIX.replace(",1.2,", ",nan,"),
    GGA_FIX.replace(",12.0,M,", ",inf,M,"),
    GGA_FIX.replace(",12.0,M,", ",12.0,F,"),
    GGA_FIX.replace("1000.0000,N,02000.0000,E", ",,,"),
    RMC_FIX.replace(",A,", ",X,", 1),
    RMC_FIX.replace("010100", "310200"),
    RMC_FIX.replace(",0.0,0.0,", ",0.0,999.0,"),
    RMC_FIX.replace(",,,A", ",,,N"),
    RMC_FIX.replace("1000.0000,N,02000.0000,E", ",,,"),
    RMC_FIX.replace("010100", ""),
    "GPGSV,1,2,02,02,20,040,28",  # invalid message ordinal
    "GPGSV,1,1,02,02,20",  # partial satellite group
    "GPGSV,1,1,02,02,91,040,28",  # invalid elevation
    "GPGSV,1,1,02,02,20,360,28",  # invalid azimuth
    "GPGSV,1,1,00,02,20,040,28",  # observation contradicts total
    "GPGSV,1,1,02,02,20,040,28,16",  # invalid signal ID
    "GPgGA,garbage", "GPGGA,\x01", "GGA,garbage",
])
def test_rejects_checksum_valid_but_malformed_fields(body):
    with pytest.raises(ValueError):
        decode_sentence(nmea(body))
    result = parse_nmea(nmea(body))
    assert result["valid_sentences"] == result["fix_sentences"] == 0
    assert result["invalid_sentences"] == 1


def test_no_fix_receiver_and_visible_satellites_are_independent():
    result = parse_nmea(nmea(GGA_NOFIX) + nmea(RMC_NOFIX) + nmea(GSV))
    assert result["valid_sentences"] == 3
    assert result["fix_sentences"] == 0
    assert result["last_fix"] is None
    assert result["satellites_used"] == 0
    assert result["satellites_visible"] == 2
    assert result["satellites"][0]["satellites"] == [
        {"prn": 2, "elevation": 20, "azimuth": 40, "snr": 28},
        {"prn": 4, "elevation": 10, "azimuth": 300, "snr": None},
    ]


def test_real_fix_requires_decoded_valid_checksum_fields():
    result = parse_nmea("garbage\n" + nmea(GGA_FIX) + nmea(RMC_FIX))
    assert result["valid_sentences"] == 2
    assert result["fix_sentences"] == 2
    assert result["invalid_sentences"] == 1
    assert result["satellites_used"] == 6
    assert result["last_fix"]["status"] == "A"
    corrupt = nmea(GGA_FIX).replace("*", "*00")
    assert parse_nmea(corrupt)["fix_sentences"] == 0


def test_unknown_sentence_fields_cannot_prove_receiver_operation():
    result = parse_nmea(nmea("GPXYZ,garbage"))
    assert result["valid_sentences"] == 0
    assert result["unsupported_sentences"] == 1


def test_launcher_json_extraction_ignores_logs_and_unrelated_objects():
    summary = {"daemon_exit": 1, "fix_sentences": 0, "nmea_output": "/private/local.nmea"}
    text = 'starting {not-json}\n{"progress": 3}\n' + json.dumps(summary, indent=2) + "\nstopped\n"
    assert extract_launcher_result(text) == summary
    assert extract_launcher_result('{"daemon_exit": false}') is None
    assert extract_launcher_result("daemon exited successfully") is None


@pytest.mark.parametrize("returncode,daemon_exit,body,healthy", [
    (0, 0, GGA_FIX, True), (2, 0, GGA_NOFIX, True),
    (1, 1, GGA_FIX, False), (1, 1, GGA_NOFIX, False),
    (2, 2, GGA_NOFIX, False), (2, 0, GGA_FIX, False),
    (1, 0, GGA_FIX, False), (139, -11, GGA_FIX, False),
])
def test_daemon_status_not_masked_by_nmea_or_absent_fix(returncode, daemon_exit, body, healthy):
    result = LauncherResult(returncode, "", "", {"daemon_exit": daemon_exit})
    assert (result.daemon_error(parse_nmea(nmea(body))) is None) is healthy


def test_missing_launcher_summary_and_timeout_are_never_healthy():
    decoded = parse_nmea(nmea(GGA_FIX))
    assert LauncherResult(0, "", "", None).daemon_error(decoded)
    timed = LauncherResult(0, "", "", {"daemon_exit": 0}, True, True)
    assert "restoration is NOT assured" in timed.daemon_error(decoded)


class FakeProcess:
    def __init__(self, failures=0):
        self.failures = failures
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.returncode = 0
        self.actions = []

    def communicate(self, timeout):
        self.actions.append(("communicate", timeout))
        if self.failures:
            self.failures -= 1
            raise subprocess.TimeoutExpired("launcher", timeout,
                                            output=b'log\n{"daemon_exit": 1}\n', stderr=b"diagnostic")
        return '{"daemon_exit": 0}', ""

    def terminate(self):
        self.actions.append("terminate")

    def kill(self):
        self.actions.append("kill")
        self.returncode = -9

    def wait(self, timeout):
        self.actions.append(("wait", timeout))
        return self.returncode


def test_launcher_delegates_and_gracefully_handles_its_own_timeout(monkeypatch):
    process = FakeProcess(failures=1)
    invocation = {}

    def popen(argv, **kwargs):
        invocation.update(argv=argv, kwargs=kwargs)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    result = run_launcher(Path("/configured/run_gps.py"), Path("/private/runtime"), 20, Path("/local/out"))
    assert invocation["argv"] == [sys.executable, "/configured/run_gps.py", "/private/runtime",
                                  "--seconds", "20", "--output", "/local/out"]
    assert process.actions == [("communicate", 40), "terminate", ("communicate", 10)]
    assert result.timed_out and not result.forced_kill
    assert result.daemon_error(parse_nmea(nmea(GGA_FIX)))


def test_forced_launcher_timeout_retains_partial_result_and_restoration_limit(monkeypatch):
    process = FakeProcess(failures=2)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    result = run_launcher(Path("/launcher"), Path("/runtime"), 1, Path("/out"))
    assert process.actions == [("communicate", 21), "terminate", ("communicate", 10), "kill", ("wait", 5)]
    assert result.returncode == -9
    assert result.summary == {"daemon_exit": 1}
    assert "diagnostic" in result.stderr
    assert result.forced_kill and result.timed_out
    assert process.stdout.closed and process.stderr.closed


def _acpi(root, name, handle):
    node = root / "bus/acpi/devices" / name
    node.mkdir(parents=True)
    (node / "path").write_text(handle)
    return node


def _tty(root, name, firmware):
    device = root / "devices" / name
    device.mkdir(parents=True)
    (device / "firmware_node").symlink_to(firmware, target_is_directory=True)
    tty = root / "class/tty" / name
    tty.mkdir(parents=True)
    (tty / "device").symlink_to(device, target_is_directory=True)


def test_uart_discovery_uses_firmware_not_serial_index(tmp_path):
    _acpi(tmp_path, "BCM4752:00", "\\_SB.URT2.GPS0")
    controller = _acpi(tmp_path, "8086228A:01", "\\_SB.URT2")
    bluetooth = _acpi(tmp_path, "8086228A:00", "\\_SB.URT1")
    _tty(tmp_path, "ttyS7", controller)
    _tty(tmp_path, "ttyS2", bluetooth)
    result = gps_uarts(tmp_path)
    assert len(result) == 1
    assert result[0]["tty"] == "ttyS7"
    assert result[0]["association"] == "firmware"


def test_uart_fallback_is_explicitly_weaker_and_requires_real_gps_acpi(tmp_path):
    controller = _acpi(tmp_path, "8086228A:01", "\\_SB.URT2")
    _tty(tmp_path, "ttyS9", controller)
    assert gps_uarts(tmp_path) == []
    _acpi(tmp_path, "BCM4752:00", "\\_SB.GPS0")
    assert gps_uarts(tmp_path)[0]["association"] == "documented-board-UART2"


def test_rfkill_identity_requires_both_chip_name_and_radio_type(tmp_path):
    for name, chip, kind in [("rfkill5", "BCM4752:00", "gps"),
                             ("rfkill1", "BCM4752:00", "wlan"),
                             ("rfkill0", "other", "gps")]:
        entry = tmp_path / "class/rfkill" / name
        entry.mkdir(parents=True)
        (entry / "name").write_text(chip)
        (entry / "type").write_text(kind)
    assert [path.name for path in rfkill_devices(tmp_path)] == ["rfkill5"]


def test_shared_acpi_root_is_not_a_gps_serial_association(tmp_path):
    _acpi(tmp_path, "BCM4752:00", "\\_SB.PCI0.GPS0")
    controller = _acpi(tmp_path, "8086228A:00", "\\_SB.PCI0.URT1")
    ancestor = _acpi(tmp_path, "PNP0A08:00", "\\_SB.PCI0")
    _tty(tmp_path, "ttyS2", controller)
    (tmp_path / "devices/firmware_node").symlink_to(ancestor, target_is_directory=True)
    assert gps_uarts(tmp_path) == []


def test_disabled_alternate_gps_node_cannot_identify_the_required_receiver(tmp_path):
    _acpi(tmp_path, "BCM4752:01", "\\_SB.URT3.GPS1")
    controller = _acpi(tmp_path, "8086228A:02", "\\_SB.URT3")
    _tty(tmp_path, "ttyS2", controller)
    assert gps_uarts(tmp_path) == []


def test_no_fix_is_warning_not_receiver_failure():
    from checks.yogabook import test_gps as hardware_gps
    from ybcheck.plugin import HardwareWarning

    capture = {"source": "synthetic", "path": "/not-read/synthetic.nmea",
               "launcher": LauncherResult(0, "", "", {"daemon_exit": 0}), "read_error": None,
               "nmea": parse_nmea(nmea(GGA_NOFIX) + nmea(RMC_NOFIX))}
    evidence = lambda key, value: None
    hardware_gps.test_gps_nmea(capture, evidence)
    with pytest.warns(HardwareWarning, match="no validated"):
        hardware_gps.test_gps_fix(capture, evidence)
    capture["nmea"] = parse_nmea(nmea(GGA_FIX))
    # A real decoded fix needs no environmental warning.
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", HardwareWarning)
        hardware_gps.test_gps_fix(capture, evidence)
    capture["nmea"] = parse_nmea("garbage")
    with pytest.raises(AssertionError, match="without valid NMEA"):
        hardware_gps.test_gps_fix(capture, evidence)


def test_gsv_cannot_claim_visible_satellites_without_observations():
    with pytest.raises(ValueError, match="lack observations"):
        decode_sentence(nmea("GPGSV,1,1,02"))


@pytest.mark.parametrize("body", [
    GGA_FIX.replace(",1.2,", ",1_2,"),
    GGA_FIX + "bad",
    GGA_FIX[:-1] + "-1,",
    RMC_FIX + ",bad",
])
def test_rejects_malformed_optional_and_decimal_fields(body):
    with pytest.raises(ValueError):
        decode_sentence(nmea(body))


@pytest.mark.parametrize("function_name", ["test_gps_nmea", "test_gps_fix"])
def test_historical_capture_cannot_report_clean_current_hardware_pass(function_name):
    from checks.yogabook import test_gps as hardware_gps
    from ybcheck.plugin import HardwareWarning
    import warnings

    capture = {"source": "read-only capture", "path": "/not-read/synthetic.nmea",
               "launcher": None, "read_error": None,
               "nmea": parse_nmea(nmea(GGA_FIX))}
    check = getattr(hardware_gps, function_name)
    with pytest.warns(HardwareWarning, match="historical NMEA capture"):
        check(capture, lambda key, value: None)
    capture["nmea"] = parse_nmea("garbage")
    # Invalid historical evidence still fails, rather than becoming a warning.
    with warnings.catch_warnings():
        warnings.simplefilter("error", HardwareWarning)
        with pytest.raises(AssertionError):
            check(capture, lambda key, value: None)
