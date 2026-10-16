"""Parsers for the pen's desktop-mapping evidence (libwacom, Mutter, logind)."""
from __future__ import annotations

import re


def libwacom_devices(datafile: str) -> list[dict]:
    """Split `libwacom-list-local-devices --format=datafile` into matched entries."""
    devices = []
    for block in re.split(r"^-{10,}\s*$", datafile, flags=re.M):
        name = re.search(r"^Name=(.+)$", block, re.M)
        if not name:
            continue
        integrated = re.search(r"^IntegratedIn=(.*)$", block, re.M)
        devices.append({
            "name": name[1].strip(),
            "integrated_in": [item for item in (integrated[1] if integrated else "").split(";") if item],
            "nodes": re.findall(r"^#\s+-\s+(/dev/input/event\d+)\s*$", block, re.M),
        })
    return devices


def device_mapping(reply: str) -> tuple[int, int, int, int]:
    """Parse gdbus `GetDeviceMapping` reply `((x, y, width, height),)`."""
    match = re.fullmatch(r"\(\((-?\d+), (-?\d+), (\d+), (\d+)\),\)", reply.strip())
    if not match:
        raise ValueError(f"unexpected InputMapping reply: {reply!r}")
    x, y, width, height = map(int, match.groups())
    if width <= 0 or height <= 0:
        raise ValueError(f"empty mapped output rectangle: {reply!r}")
    return x, y, width, height


def graphical_session(show_session_outputs: list[str]) -> dict | None:
    """Pick the single active seat0 wayland/x11 user session from `loginctl show-session` blocks."""
    candidates = []
    for text in show_session_outputs:
        fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        if (fields.get("Class") == "user" and fields.get("Active") == "yes"
                and fields.get("Type") in ("wayland", "x11") and fields.get("Seat") == "seat0"):
            candidates.append({"uid": int(fields["User"]), "user": fields["Name"], "type": fields["Type"]})
    if len(candidates) > 1:
        raise ValueError(f"ambiguous active graphical sessions: {candidates}")
    return candidates[0] if candidates else None
