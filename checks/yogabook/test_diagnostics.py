"""Current-boot faults and CPU security readback; historical captures never pass."""
import json
from pathlib import Path
import re
import sys
import warnings

import pytest

from checks.yogabook.diagnostics import classify
from checks.yogabook.platform import attribute, text
from ybcheck.plugin import HardwareWarning


@pytest.mark.hardware(id="kernel.current_boot_diagnostics", component="kernel", layer="headless")
def test_current_boot_diagnostics(shell, evidence):
    result = shell(sys.executable, "-c", "import json, sys; sys.path.insert(0, sys.argv[1]); from checks.yogabook.diagnostics import collect_logs; print(json.dumps(collect_logs()))", str(Path(__file__).resolve().parents[2]), timeout=22)
    assert result.returncode == 0, result.stderr
    capture = json.loads(result.stdout)
    evidence("collection", {key: value for key, value in capture.items() if key != "records"})
    records = capture["records"]
    if not records:
        pytest.skip("blocked: neither boot kernel journal nor dmesg is readable/nonempty; see collection attempts (journal permissions/dmesg_restrict)")
    boot_id = text(Path("/proc/sys/kernel/random/boot_id")).replace("-", "")
    journal_boots = {item["boot_id"] for item in records if item.get("boot_id")}
    assert not journal_boots or journal_boots == {boot_id}, "kernel journal is not from this boot"
    findings = classify(records)
    evidence("kernel_findings", findings)
    evidence("log_window", {"records": len(records), "first": records[0], "last": records[-1], "current_boot_id": boot_id})
    evidence("limitations", "Bounded current-boot kernel-log smoke only. Journal includes info and higher severity, excluding debug chatter; dmesg fallback includes all priorities. Unmapped and unresolved diagnostics stay WARN; operational board checks are independent. Firmware retry recovery proves firmware loading, not audible audio. No current hardware result is inferred from prior notes; CHV has no DMC requirement.")
    incomplete = capture["truncated"] or capture["malformed"]
    try:
        first_seconds = float(records[0]["timestamp"]) / (1000000 if capture["source"] == "journal" else 1)
        incomplete = incomplete or first_seconds > 5
    except (TypeError, ValueError):
        incomplete = True
    if incomplete:
        warnings.warn("Kernel-log window is bounded/incomplete or lacks early boot; absence of earlier faults is unproven", HardwareWarning)
    for component in sorted({item["component"] for item in findings if item["status"] == "warn"}):
        count = sum(item["status"] == "warn" and item["component"] == component for item in findings)
        warnings.warn(f"{component}: {count} unresolved current-boot diagnostic(s); original timestamp/severity/text in kernel_findings, inspect independent operational outcome", HardwareWarning)
    fatal = [item for item in findings if item["status"] == "fail"]
    assert not fatal, f"critical current-boot kernel faults: {fatal}"


@pytest.mark.hardware(id="cpu.microcode_security.readback", component="cpu.microcode", layer="headless")
def test_microcode_security(shell, evidence):
    cpuinfo = attribute(shell, Path("/proc/cpuinfo"))
    fields = []
    for block in cpuinfo.split("\n\n"):
        values = dict(re.findall(r"^(processor|vendor_id|cpu family|model|stepping|microcode|model name)\s*:\s*(.+)$", block, re.M))
        if values:
            fields.append(values)
    evidence("cpu_identity_microcode", fields)
    assert fields and all(item.get("microcode") and int(item["microcode"], 0) > 0 for item in fields), "CPU microcode revision not exposed/nonzero on every enumerated CPU"
    paths = sorted(Path("/sys/devices/system/cpu/vulnerabilities").glob("*"))
    if not paths:
        pytest.skip("blocked: kernel does not expose CPU vulnerability status")
    statuses = {path.name: attribute(shell, path) for path in paths}
    evidence("cpu_vulnerabilities", statuses)
    evidence("limitations", "Identity/revision and kernel mitigation statements only. No claim that the installed microcode is the latest/fixed revision, or that firmware/mitigations eliminate every security exposure.")
    for name, status in statuses.items():
        if re.search(r"vulnerable|unknown", status, re.I):
            warnings.warn(f"CPU security exposure {name}: {status}", HardwareWarning)
