"""Small sysfs identity/parsing helpers shared by ordinary hardware tests."""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest


def text(path: Path) -> str:
    """Read discovery metadata only; operational reads use bounded ``attribute``."""
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""
    except PermissionError:
        pytest.skip(f"blocked: cannot read {path}")


def drivers(path: Path) -> dict[str, str]:
    """Find real bound drivers, not a fabricated device/driver symlink target."""
    node = path.resolve()
    result = {}
    for parent in (node, *node.parents):
        link = parent / "driver"
        if link.is_symlink() and link.exists():
            result[str(parent)] = link.resolve().name
    return result


def identity(path: Path) -> dict:
    node = path.resolve()
    firmware = [str((parent / "firmware_node").resolve()) for parent in (node, *node.parents)
                if (parent / "firmware_node").is_symlink()]
    return {"path": str(path), "resolved": str(node), "drivers": drivers(path), "firmware_nodes": firmware}


def attribute(shell, path: Path) -> str:
    result = shell("cat", str(path), timeout=5)
    if result.returncode and "Permission denied" in result.stderr:
        pytest.skip(f"blocked: permission to read {path}")
    assert result.returncode == 0, f"cannot read {path}: {result.stderr}"
    value = result.stdout.strip()
    assert value, f"empty attribute {path}"
    return value


def numeric(value: str) -> list[float]:
    """IIO scalar/vector values (including quaternion syntax) must be finite."""
    tokens = value.translate(str.maketrans({"[": " ", "]": " ", ",": " ", "(": " ", ")": " "})).split()
    assert tokens, "empty numeric sample"
    result = [float(token) for token in tokens]
    assert all(math.isfinite(item) for item in result), f"nonfinite sample: {value}"
    return result


def bitmap_has(value: str, bit: int, word_bits: int = 64) -> bool:
    """Linux sysfs bitmaps print high native-word chunks first, with padding omitted."""
    words = value.split()
    word = bit // word_bits
    return word < len(words) and bool(int(words[-1 - word], 16) & (1 << (bit % word_bits)))


SENSOR_CHANNELS = {
    "accelerometer": "in_accel_",
    "gyroscope": "in_anglvel_",
    "compass": "in_magn_",
    "ambientlight": "in_illuminance",
    "hinge": "in_angl",
    "inclination": "in_incli_",
    "rotation": "in_rot_",
}
SENSOR_NAMES = {"accelerometer": "accel_3d", "gyroscope": "gyro_3d", "compass": "magn_3d", "ambientlight": "als", "hinge": "hinge", "inclination": "incli_3d", "rotation": "dev_rotation"}


def sensor_kind(name: str, channels: list[str]) -> str | None:
    # A compass also exports tilt-compensated rotation. Prefer its actual name.
    for kind, expected in SENSOR_NAMES.items():
        if name == expected:
            return kind
    for kind, prefix in SENSOR_CHANNELS.items():
        if any(channel.startswith(prefix) and channel.endswith(("_raw", "_input")) for channel in channels):
            return kind
    return None


def sensors(root: Path = Path("/sys/bus/iio/devices")) -> list[dict]:
    found = []
    for path in sorted(root.glob("iio:device*")):
        channels = sorted(p.name for p in path.glob("in_*"))
        name = text(path / "name")
        found.append({**identity(path), "name": name, "kind": sensor_kind(name, channels), "channels": channels,
                      "labels": {p.name: text(p) for p in path.glob("in_*_label")}})
    return found


def unescape_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def root_mount(mountinfo: str) -> dict:
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[4] != "/":
            continue
        separator = fields.index("-")
        return {"device": fields[2], "mountpoint": unescape_mount(fields[4]), "options": fields[5].split(","),
                "fstype": fields[separator + 1], "source": unescape_mount(fields[separator + 2])}
    raise AssertionError("mounted root filesystem is missing from /proc/self/mountinfo")


def block_leaves(path: Path, seen: set[Path] | None = None) -> list[Path]:
    """Follow device-mapper/MD slaves to physical block devices without index guesses."""
    seen = set() if seen is None else seen
    node = path.resolve()
    assert node not in seen, f"cycle in block dependencies at {node}"
    seen.add(node)
    children = sorted((node / "slaves").glob("*"))
    if not children:
        return [node]
    result = []
    for child in children:
        result.extend(block_leaves(child, seen.copy()))
    return result
