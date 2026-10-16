"""Read-only NMEA decoding and delegation to the existing stock GPS launcher.

No UART initialization or serial reads live here. Coordinates in returned evidence
are private runtime data; the source and regression fixtures contain none from a
real receiver.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


_HEADER = re.compile(r"[A-Z]{2}[A-Z]{3}\Z")
_TIME = re.compile(r"([0-9]{2})([0-9]{2})([0-9]{2})(?:\.[0-9]+)?\Z")


def _integer(value: str, minimum: int, maximum: int) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError("expected an unsigned integer")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError("integer out of range")
    return number


def _number(value: str, minimum: float = 0) -> float:
    if not re.fullmatch(r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", value):
        raise ValueError("invalid decimal field")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise ValueError("invalid numeric field")
    return number


def _time(value: str, required: bool) -> str | None:
    if not value and not required:
        return None
    match = _TIME.fullmatch(value)
    if not match or not (int(match[1]) < 24 and int(match[2]) < 60 and int(match[3]) <= 60):
        raise ValueError("invalid UTC time")
    return value


def _position(value: str, hemisphere: str, latitude: bool) -> float:
    digits = 2 if latitude else 3
    if not re.fullmatch(rf"[0-9]{{{digits + 2}}}(?:\.[0-9]+)?", value):
        raise ValueError("invalid coordinate format")
    degrees, minutes = int(value[:digits]), float(value[digits:])
    limit = 90 if latitude else 180
    if degrees > limit or minutes >= 60 or (degrees == limit and minutes):
        raise ValueError("coordinate out of range")
    if hemisphere not in ("NS" if latitude else "EW"):
        raise ValueError("invalid hemisphere")
    return (degrees + minutes / 60) * (-1 if hemisphere in "SW" else 1)


def _coordinates(fields: list[str], offset: int, required: bool) -> dict[str, float]:
    values = fields[offset:offset + 4]
    if not any(values) and not required:
        return {}
    return {"latitude": _position(values[0], values[1], True),
            "longitude": _position(values[2], values[3], False)}


def decode_sentence(line: bytes | str) -> dict[str, Any]:
    """Reject corrupt framing/checksums and malformed supported sentence fields."""
    if isinstance(line, bytes):
        line = line.decode("ascii")
    line = line.rstrip("\r\n")
    if not line.startswith("$") or line.count("*") != 1:
        raise ValueError("invalid NMEA framing")
    body, supplied = line[1:].split("*")
    if not re.fullmatch(r"[0-9A-Fa-f]{2}", supplied):
        raise ValueError("invalid checksum field")
    if not body or any(not 32 <= ord(char) <= 126 for char in body):
        raise ValueError("non-ASCII NMEA body")
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    if checksum != int(supplied, 16):
        raise ValueError("checksum mismatch")
    fields = body.split(",")
    if not _HEADER.fullmatch(fields[0]) or len(fields) < 2:
        raise ValueError("invalid sentence header")
    kind = fields[0][-3:]
    decoded: dict[str, Any] = {"talker": fields[0][:2], "type": kind, "fix": False}
    if kind == "GGA":
        if len(fields) != 15:
            raise ValueError("GGA requires 14 fields")
        quality = _integer(fields[6], 0, 8)
        satellites = _integer(fields[7], 0, 99)
        decoded.update(quality=quality, satellites_used=satellites, fix=quality > 0,
                       utc=_time(fields[1], quality > 0))
        decoded.update(_coordinates(fields, 2, quality > 0))
        for index in (8, 9, 11):
            if fields[index]:
                _number(fields[index], -math.inf if index in (9, 11) else 0)
        if quality and (not fields[8] or not fields[9] or fields[10] != "M"):
            raise ValueError("fixed GGA lacks dilution/altitude units")
        if fields[11] and fields[12] != "M":
            raise ValueError("invalid geoid units")
        if fields[9] and fields[10] != "M":
            raise ValueError("invalid altitude units")
        if fields[13]:
            _number(fields[13])
        if fields[14]:
            _integer(fields[14], 0, 1023)
    elif kind == "RMC":
        if not 12 <= len(fields) <= 14 or fields[2] not in ("A", "V"):
            raise ValueError("invalid RMC fields/status")
        active = fields[2] == "A"
        decoded.update(status=fields[2], fix=active, utc=_time(fields[1], active))
        decoded.update(_coordinates(fields, 3, active))
        if fields[9]:
            if not re.fullmatch(r"[0-9]{6}", fields[9]):
                raise ValueError("invalid RMC date")
            datetime.strptime(fields[9], "%d%m%y")
        elif active:
            raise ValueError("active RMC lacks date")
        for index in (7, 8, 10):
            if fields[index]:
                number = _number(fields[index])
                if index == 8 and number > 360:
                    raise ValueError("invalid course")
        if fields[10] and fields[11] not in ("E", "W"):
            raise ValueError("invalid magnetic variation direction")
        if len(fields) >= 13 and fields[12] not in ("", "A", "D", "E", "F", "M", "N", "P", "R", "S"):
            raise ValueError("invalid RMC mode")
        if active and len(fields) >= 13 and fields[12] == "N":
            raise ValueError("active RMC contradicts invalid mode")
        if len(fields) == 14 and fields[13] not in ("", "S", "C", "U", "V"):
            raise ValueError("invalid RMC navigation status")
    elif kind == "GSV":
        if len(fields) < 4:
            raise ValueError("incomplete GSV")
        messages = _integer(fields[1], 1, 99)
        message = _integer(fields[2], 1, messages)
        visible = _integer(fields[3], 0, 999)
        satellites = fields[4:]
        if len(satellites) % 4 == 1:  # NMEA 4.10 optional signal ID
            _integer(satellites[-1], 0, 15)
            satellites = satellites[:-1]
        if len(satellites) % 4 or len(satellites) > 16:
            raise ValueError("incomplete GSV satellite group")
        observations = []
        for offset in range(0, len(satellites), 4):
            prn, elevation, azimuth, snr = satellites[offset:offset + 4]
            observations.append({"prn": _integer(prn, 1, 999),
                                 "elevation": _integer(elevation, 0, 90) if elevation else None,
                                 "azimuth": _integer(azimuth, 0, 359) if azimuth else None,
                                 "snr": _integer(snr, 0, 99) if snr else None})
        if len(observations) > visible:
            raise ValueError("GSV observations exceed visible satellite count")
        if visible and not observations:
            raise ValueError("GSV visible satellites lack observations")
        decoded.update(messages=messages, message=message, satellites_visible=visible,
                       satellites=observations)
    return decoded


def parse_nmea(data: bytes | str) -> dict[str, Any]:
    lines = data.splitlines()
    summary: dict[str, Any] = {"valid_sentences": 0, "invalid_sentences": 0,
                               "fix_sentences": 0, "unsupported_sentences": 0, "types": {}, "satellites_used": 0,
                               "satellites_visible": 0, "satellites": [], "last_fix": None}
    for line in lines:
        if not line:
            continue
        try:
            sentence = decode_sentence(line)
        except (ValueError, UnicodeError, OverflowError):
            summary["invalid_sentences"] += 1
            continue
        kind = sentence["type"]
        if kind not in {"GGA", "RMC", "GSV"}:
            # A checksum is not enough to validate an unknown sentence's fields.
            summary["unsupported_sentences"] += 1
            continue
        summary["valid_sentences"] += 1
        summary["types"][kind] = summary["types"].get(kind, 0) + 1
        if sentence["fix"]:
            summary["fix_sentences"] += 1
            summary["last_fix"] = sentence
        summary["satellites_used"] = max(summary["satellites_used"], sentence.get("satellites_used", 0))
        summary["satellites_visible"] = max(summary["satellites_visible"], sentence.get("satellites_visible", 0))
        if kind == "GSV":
            summary["satellites"].append({"talker": sentence["talker"], "message": sentence["message"],
                                           "satellites": sentence["satellites"]})
    return summary


def extract_launcher_result(stdout: str) -> dict[str, Any] | None:
    """Find the last daemon summary even when stdout also contains log lines."""
    decoder = json.JSONDecoder()
    result = None
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(stdout, index)
        except ValueError:
            continue
        if isinstance(candidate, dict) and type(candidate.get("daemon_exit")) is int:
            result = candidate
    return result


@dataclass(frozen=True)
class LauncherResult:
    returncode: int
    stdout: str
    stderr: str
    summary: dict[str, Any] | None
    timed_out: bool = False
    forced_kill: bool = False

    def daemon_error(self, nmea: dict[str, Any]) -> str | None:
        if self.timed_out:
            return ("launcher exceeded its deadline; graceful shutdown requested; "
                    + ("launcher forcibly killed: separate daemon process groups may remain and "
                       "termios/rfkill/PM restoration is NOT assured" if self.forced_kill else
                       "launcher exited after TERM; inspect its cleanup evidence"))
        if self.summary is None:
            return f"launcher returned {self.returncode} without a daemon-exit JSON summary"
        if self.summary["daemon_exit"] != 0:
            return f"stock daemon exited {self.summary['daemon_exit']} (launcher {self.returncode})"
        # The existing launcher deliberately returns 2 for healthy acquisition without a fix.
        if self.returncode == 0 or (self.returncode == 2 and nmea["fix_sentences"] == 0):
            return None
        return f"launcher failed with status {self.returncode} despite daemon_exit=0"


def run_launcher(launcher: Path, runtime: Path, seconds: int, output: Path) -> LauncherResult:
    """Give the launcher time to restore itself; never signal unrelated processes.

    unshare execs the launcher's inside process; its daemon starts a *separate*
    session. Killing this wrapper's process group would not clean that daemon up.
    TERM is therefore sent only to the owned launcher, whose own finally owns
    daemon shutdown and hardware restoration. Forced termination is reported as
    a restoration limitation, never a successful acquisition.
    """
    process = subprocess.Popen([sys.executable, str(launcher), str(runtime), "--seconds", str(seconds),
                                "--output", str(output)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, errors="replace",
                               start_new_session=True)
    timed_out = forced = False
    try:
        stdout, stderr = process.communicate(timeout=seconds + 20)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired as expired:
            forced = True
            process.kill()
            # Descendants can retain inherited pipes after the wrapper is killed.
            # Close them instead of an unbounded communicate() or unsafe group kill.
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
            stdout = expired.output or ""
            stderr = expired.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            stderr += "\nForced launcher termination; inherited output pipes closed."
    return LauncherResult(process.returncode, stdout, stderr, extract_launcher_result(stdout), timed_out, forced)


def rfkill_devices(root: Path = Path("/sys")) -> list[Path]:
    return [path for path in sorted((root / "class/rfkill").glob("rfkill*"))
            if (path / "type").read_text().strip() == "gps"
            and (path / "name").read_text().strip() == "BCM4752:00"]


def firmware_nodes(device: Path) -> list[Path]:
    nodes = []
    resolved = device.resolve()
    for ancestor in (resolved, *resolved.parents):
        link = ancestor / "firmware_node"
        if link.exists():
            node = link.resolve()
            if node not in nodes:
                nodes.append(node)
    return nodes


def gps_uarts(root: Path = Path("/sys")) -> list[dict[str, Any]]:
    """Enumerate tty names by firmware identity, not by a fixed tty index.

    Some kernels expose GPS as platform rfkill plus a plain UART, without an
    exported SerialBus connection. Keep that known-board association explicitly
    weaker than a firmware ancestry link to the actual BCM4752 ACPI node.
    """
    gps_nodes = [node for node in (root / "bus/acpi/devices").glob("BCM4752:*")
                 if node.name == "BCM4752:00"]
    handles = {node.resolve() for node in gps_nodes}
    gps_paths = [(node / "path").read_text().strip() for node in gps_nodes if (node / "path").exists()]
    candidates = []
    for tty in sorted((root / "class/tty").glob("ttyS*")):
        nodes = firmware_nodes(tty / "device")
        direct = bool(handles.intersection(nodes))
        node_paths = [(node / "path").read_text().strip() for node in nodes if (node / "path").exists()]
        # A shared PCI/root ACPI ancestor is not a serial connection. Only the
        # nearest firmware node can establish this relationship.
        nearest_paths = [(node / "path").read_text().strip() for node in nodes[:1]
                         if (node / "path").exists()]
        nested = any(gps.startswith(path + ".") for gps in gps_paths for path in nearest_paths)
        board_uart = any(node.name == "8086228A:01" for node in nodes)
        if direct or nested or (handles and board_uart):
            candidates.append({"tty": tty.name, "firmware_nodes": [str(node) for node in nodes],
                               "gps_acpi_nodes": [str(node) for node in gps_nodes],
                               "association": "firmware" if direct or nested else "documented-board-UART2",
                               "firmware_paths": node_paths})
    return candidates
