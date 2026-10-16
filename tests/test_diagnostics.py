"""Deterministic log classification/collection regressions; no live device needed."""
import json
import sys

import pytest

from checks.yogabook import diagnostics
from checks.yogabook.diagnostics import bounded_command, classify, parse_dmesg, parse_journal


def record(message, severity=6, timestamp="1"):
    return {"message": message, "severity": severity, "timestamp": timestamp, "original": f"[{timestamp}] {message}"}


def test_journal_preserves_priority_timestamp_boot_and_original_text():
    line = json.dumps({"MESSAGE": "intel-spi: probe failed -22", "PRIORITY": "3", "__MONOTONIC_TIMESTAMP": "179001", "_BOOT_ID": "abc"})
    records, malformed = parse_journal(line + "\nnot json\n")
    assert malformed == 1
    assert records == [{"message": "intel-spi: probe failed -22", "severity": 3,
                        "timestamp": "179001", "original": line, "boot_id": "abc"}]
    assert classify(records)[0]["component"] == "spi"


def test_journal_binary_message_and_missing_priority_are_not_empty_success():
    records, malformed = parse_journal(json.dumps({"MESSAGE": list(b"BUG: test fault")}))
    assert malformed == 0
    assert records[0]["severity"] is None
    assert classify(records)[0]["status"] == "fail"


def test_dmesg_preserves_original_facility_priority_and_continuations():
    lines = "<11>[   12.040000] Bluetooth: hci0: firmware missing\n  Call Trace:\n"
    records = parse_dmesg(lines)
    assert records[0]["severity"] == 3
    assert records[0]["timestamp"] == "12.040000"
    assert records[0]["original"] == lines.splitlines()[0]
    assert [finding["status"] for finding in classify(records)] == ["warn", "fail"]


@pytest.mark.parametrize("message", [
    "BUG: unable to handle page fault", "Oops: 0000", "Kernel panic - not syncing",
    "general protection fault", "GPF in driver", "INFO: task kworker blocked for more than 122 seconds.",
    "hung task detected", "Call Trace:", "runtime PM usage count underflow!",
    "Unbalanced runtime-PM usage count", "i915: GPU HANG: ecode 8", "WARNING: unpatched return thunk in use",
    "rcu: INFO: rcu_preempt detected expedited stalls on CPUs/tasks: { P3283 } 6 jiffies",
])
def test_critical_faults_fail_even_when_printed_at_info_priority(message):
    finding, = classify([record(message)])
    assert finding["status"] == "fail"
    assert finding["original"] == f"[1] {message}"


@pytest.mark.parametrize(("message", "component"), [
    ("intel-spi intel-spi: probe failed with error -22", "spi"),
    ("i915: [drm] *ERROR* Failed to get the SoC PWM", "graphics.i915"),
    ("i915: panel payload error", "graphics.i915"),
    ("rfkill_gpio BCM4752:00: probe failed -16", "gps"),
    ("Bluetooth: hci0: BCM: firmware Patch file not found: brcm/BCM4356A2.hcd", "bluetooth"),
    ("brcmfmac: Direct firmware load for brcm/brcmfmac4356-pcie.board.bin failed with error -2", "wifi"),
    ("pcspkr: probe failed -16", "pcspeaker"),
    ("ASoC: failed to init backend: -517", "audio.sst"),
    ("new-driver: unexpected error -5", "kernel.unmapped"),
])
def test_prior_board_errors_are_visible_not_globally_ignored(message, component):
    finding, = classify([record(message, severity=3)])
    assert finding["status"] == "warn"
    assert finding["component"] == component


def test_sst_initial_missing_firmware_requires_later_success_evidence():
    failed = record("intel_sst_acpi 808622A8:00: Direct firmware load for intel/fw_sst_22a8.bin failed with error -2", 3)
    assert classify([failed])[0]["status"] == "warn"
    success = record("intel_sst_acpi 808622A8:00: FW Version 01.0b.02.02", timestamp="568")
    findings = classify([failed, success])
    assert findings[0]["status"] == "recovered"
    assert findings[0]["recovery"] == success
    assert findings[0]["original"] == failed["original"]
    assert classify([success, failed])[0]["status"] == "warn"


def test_sst_success_does_not_excuse_other_firmware_or_backend_fault():
    messages = [record("intel_sst_acpi: firmware other_dsp.bin failed -2", 3),
                record("ASoC: failed to initialize backend", 3),
                record("intel_sst_acpi: FW Version 01.0b.02.02")]
    assert [finding["status"] for finding in classify(messages)] == ["warn", "warn"]


def test_other_component_health_does_not_recover_firmware_error():
    findings = classify([record("Bluetooth: BCM4356A2.hcd not found", 3),
                         record("brcmfmac: firmware version 7.35 running"),
                         record("Bluetooth: hci0 up")])
    assert findings[0]["status"] == "warn"


def test_benign_chv_no_dmc_notice_is_not_a_firmware_requirement():
    assert classify([record("i915: Cherryview has no DMC support"), record("i915: initialized")]) == []
    assert classify([record("i915: DMC firmware failed with error -2", 3)])[0]["status"] == "warn"


def test_severity_only_unknown_error_remains_visible():
    finding, = classify([record("new component reported bad state", 3)])
    assert finding["status"] == "warn"
    assert finding["component"] == "kernel.unmapped"


def test_collector_falls_back_after_inaccessible_journal(monkeypatch):
    replies = iter([
        {"returncode": 1, "stdout": "", "stderr": "Permission denied", "limited": False},
        {"returncode": 0, "stdout": "<3>[1.0] pcspkr probe failed -16\n", "stderr": "", "limited": False},
    ])
    monkeypatch.setattr(diagnostics, "bounded_command", lambda argv: {"argv": argv, **next(replies)})
    capture = diagnostics.collect_logs()
    assert capture["source"] == "dmesg"
    assert len(capture["attempts"]) == 2
    assert classify(capture["records"])[0]["status"] == "warn"


def test_unreadable_buffers_do_not_fabricate_empty_pass(monkeypatch):
    monkeypatch.setattr(diagnostics, "bounded_command", lambda argv: {
        "argv": argv, "returncode": 1, "stdout": "", "stderr": "Operation not permitted", "limited": False})
    capture = diagnostics.collect_logs()
    assert capture["source"] is None and not capture["records"]
    assert all(item["returncode"] for item in capture["attempts"])


def test_truncated_capture_retains_fault_and_explicit_uncertainty(monkeypatch):
    line = json.dumps({"MESSAGE": "BUG: captured before limit", "PRIORITY": "3"})
    monkeypatch.setattr(diagnostics, "bounded_command", lambda argv: {
        "argv": argv, "returncode": -9, "stdout": line + "\n{incomplete", "stderr": "", "limited": True})
    capture = diagnostics.collect_logs()
    assert capture["truncated"] and capture["malformed"] == 1
    assert classify(capture["records"])[0]["status"] == "fail"


def test_command_output_bound_is_explicit_and_retains_bytes():
    result = bounded_command([sys.executable, "-c", "print('x' * 10000)"], max_bytes=128)
    assert result["limited"]
    assert len(result["stdout"].encode()) + len(result["stderr"].encode()) == 128


def test_command_deadline_returns_bounded_uncertainty():
    result = bounded_command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
    assert result["limited"] and result["returncode"] != 0
