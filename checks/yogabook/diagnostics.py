"""Bounded current-boot diagnostics and conservative, evidence-linked classification."""
from __future__ import annotations

import json
import re
import selectors
import subprocess
import time


MAX_RECORDS = 12000
MAX_BYTES = 4 * 1024 * 1024


def bounded_command(argv, timeout=8, max_bytes=MAX_BYTES):
    """Drain both pipes with a byte/deadline bound; never silently drop overflow."""
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        return {"argv": argv, "returncode": None, "stdout": "", "stderr": str(exc), "limited": False}
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limited = False
    deadline = time.monotonic() + timeout
    try:
        for name in buffers:
            selector.register(getattr(process, name), selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                limited = True
                break
            for key, _ in selector.select(min(remaining, 0.2)):
                chunk = key.fileobj.read1(65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                available = max_bytes - sum(map(len, buffers.values()))
                buffers[key.data].extend(chunk[:available])
                if len(chunk) > available:
                    limited = True
                    break
            if limited:
                break
        if not limited:
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                limited = True
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return {"argv": argv, "returncode": process.returncode, "limited": limited,
            **{name: value.decode("utf-8", errors="replace") for name, value in buffers.items()}}


def parse_journal(content):
    records, malformed = [], 0
    for line in content.splitlines():
        try:
            item = json.loads(line)
            message = item["MESSAGE"]
            if isinstance(message, list):
                message = bytes(message).decode("utf-8", errors="replace")
            if not isinstance(message, str):
                raise ValueError("non-text message")
            priority = int(item["PRIORITY"]) if "PRIORITY" in item else None
            records.append({"timestamp": item.get("__MONOTONIC_TIMESTAMP"), "severity": priority,
                            "message": message, "original": line, "boot_id": item.get("_BOOT_ID")})
        except (ValueError, TypeError, KeyError):
            malformed += 1
    return records, malformed


def parse_dmesg(content):
    records = []
    for line in content.splitlines():
        if not line.strip():
            continue
        match = re.match(r"^(?:<(\d+)>)?\s*(?:\[\s*([0-9.]+)\]\s*)?(.*)$", line)
        records.append({"timestamp": match[2], "severity": int(match[1]) % 8 if match[1] else None,
                        "message": match[3], "original": line})
    return records


def collect_logs():
    """Prefer boot-scoped info-and-higher journal; fall back to the current ring."""
    attempts = []
    for source, argv in (
        ("journal", ["journalctl", "-k", "--boot=0", "--priority=info", "--no-pager", "--output=json", f"--lines={MAX_RECORDS + 1}"]),
        ("dmesg", ["dmesg", "--raw"]),
    ):
        result = bounded_command(argv)
        records, malformed = parse_journal(result["stdout"]) if source == "journal" else (parse_dmesg(result["stdout"]), 0)
        attempts.append({key: value for key, value in result.items() if key != "stdout"})
        if records and (result["returncode"] == 0 or result["limited"]):
            return {"source": source, "records": records[-MAX_RECORDS:], "attempts": attempts,
                    "truncated": result["limited"] or len(records) > MAX_RECORDS,
                    "malformed": malformed}
    return {"source": None, "records": [], "attempts": attempts, "truncated": False, "malformed": 0}


COMPONENTS = (
    ("audio.sst", r"intel_sst|fw_sst|sst[-_ ]|cht.*(?:audio|yogabook)|ALSA.*(?:backend|BE)|ASoC|rt5677"),
    ("graphics.i915", r"i915|drm|SoC PWM|panel.*payload"),
    ("gps", r"BCM4752|rfkill.*GPS|GPS.*rfkill"),
    ("bluetooth", r"Bluetooth|BCM4356A2\.hcd|hci\d|hci_uart"),
    ("wifi", r"brcmfmac|brcm/.*(?:bin|txt|clm_blob)"),
    ("spi", r"intel[-_]spi"),
    ("pcspeaker", r"pcspkr"),
    ("camera", r"atomisp|ov2740|ov8858|shisp"),
    ("touch.halo", r"goodix|9110_cfg"),
    ("touch.display", r"hideep"),
    ("haptics", r"drv260"),
    ("sensors", r"hid.sensor|iio.sensor"),
    ("power", r"bq27|bq258|ACPI|PM:|runtime.?PM"),
    ("cpu.microcode", r"microcode"),
)
CRITICAL = re.compile(
    r"\bBUG(?::| at\b)|\bOops:|kernel panic|fatal exception|general protection fault|\bGPF\b|"
    r"blocked for more than \d+ seconds|\bhung task\b|Call Trace:|rcu.*detected.*stall|"
    r"runtime.?PM.*(?:underflow|unbalanced|usage count|failed|error)|"
    r"(?:underflow|unbalanced).*runtime.?PM|"
    r"runtime_(?:suspend|resume).*?(?:failed|error|returns? -\d+)|unpatched return thunk|"
    r"(?:GPU HANG|wedged GPU|GPU.*reset.*failed)", re.I)
PROBLEM = re.compile(r"\berror\b|\bfailed\b|\bfailure\b|\bwarning\b|\btimeout\b|\btimed out\b|"
                     r"\bNACK\b|\bnot found\b|\bmissing\b|\bunable\b|\bcannot\b|\bcan't\b|"
                     r"\bno backend\b|\bprobe.*(?:-\d+|defer)\b", re.I)
FIRMWARE_FAILURE = re.compile(r"firmware|\.hcd\b|\.bin\b|clm_blob|fw_sst", re.I)


def component_for(message):
    for name, pattern in COMPONENTS:
        if re.search(pattern, message, re.I):
            return name
    return "kernel.unmapped"


def classify(records):
    """A prior failure is not erased by enumeration or a broad driver allowlist.

    Only a later SST firmware version message demonstrates its known retry.
    Other failures remain visible until their separate operational checks pass.
    """
    findings = []
    for index, record in enumerate(records):
        message = record["message"]
        critical = bool(CRITICAL.search(message))
        problem = critical or (record["severity"] is not None and record["severity"] <= 4) or bool(PROBLEM.search(message))
        if not problem:
            continue
        component = component_for(message)
        finding = {**record, "component": component, "status": "fail" if critical else "warn",
                   "reason": "critical kernel fault" if critical else "unresolved kernel diagnostic; inspect component operational checks"}
        if not critical and component == "audio.sst" and "fw_sst_22a8.bin" in message and FIRMWARE_FAILURE.search(message) and re.search(r"failed|not found|missing|-2\b", message, re.I):
            recovery = next((later for later in records[index + 1:]
                             if component_for(later["message"]) == component and
                             re.search(r"\bFW Version\s+[0-9a-f]", later["message"], re.I)), None)
            if recovery:
                finding.update(status="recovered", reason="later SST firmware version confirms firmware retry succeeded",
                               recovery=recovery)
        findings.append(finding)
    return findings


def va_profiles(content):
    """Match profile/entrypoint pairs, not driver chatter or a profile count."""
    return sorted(set(re.findall(r"\b(VAProfile[A-Za-z0-9_]+)\s*:\s*(VAEntrypoint[A-Za-z0-9_]+)\b", content)))


def va_decode_missing(profiles):
    decode = {profile for profile, entrypoint in profiles if entrypoint == "VAEntrypointVLD"}
    missing = []
    if not decode.intersection({"VAProfileH264Baseline", "VAProfileH264ConstrainedBaseline", "VAProfileH264Main", "VAProfileH264High"}):
        missing.append("H.264 VLD decode")
    if "VAProfileHEVCMain" not in decode:
        missing.append("HEVC Main VLD decode")
    return missing


def intel_render_nodes(root):
    """Discover Intel render nodes by PCI vendor/driver, not card numbering."""
    nodes = []
    for path in sorted(root.glob("renderD*")):
        try:
            vendor = (path / "device/vendor").read_text().strip()
            device_id = (path / "device/device").read_text().strip()
        except FileNotFoundError:
            continue
        if vendor.lower() != "0x8086":
            continue
        driver = path / "device/driver"
        nodes.append({"name": path.name, "sysfs": str(path.resolve()), "vendor": vendor,
                      "device_id": device_id,
                      "driver": driver.resolve().name if driver.is_symlink() and driver.exists() else None})
    return nodes


def i915_query(node):
    """Read driver version and chipset ID only; no buffers, rendering or load."""
    import ctypes
    import fcntl
    import os

    class Version(ctypes.Structure):
        _fields_ = [("major", ctypes.c_int), ("minor", ctypes.c_int), ("patch", ctypes.c_int),
                    ("name_len", ctypes.c_size_t), ("name", ctypes.c_void_p),
                    ("date_len", ctypes.c_size_t), ("date", ctypes.c_void_p),
                    ("desc_len", ctypes.c_size_t), ("desc", ctypes.c_void_p)]

    class GetParam(ctypes.Structure):
        _fields_ = [("param", ctypes.c_int32), ("value", ctypes.c_void_p)]

    fd = os.open(node, os.O_RDONLY | os.O_CLOEXEC)
    try:
        name = ctypes.create_string_buffer(128)
        version = Version(name_len=127, name=ctypes.addressof(name))
        buffer = bytearray(version)
        fcntl.ioctl(fd, 0xC0006400 | (ctypes.sizeof(Version) << 16), buffer, True)
        version = Version.from_buffer_copy(buffer)
        chipset = ctypes.c_int()
        query = GetParam(param=4, value=ctypes.addressof(chipset))
        fcntl.ioctl(fd, 0xC0006446 | (ctypes.sizeof(GetParam) << 16), bytearray(query), True)
        return {"driver": name.value.decode(), "version": [version.major, version.minor, version.patch],
                "chipset_id": chipset.value}
    finally:
        os.close(fd)
