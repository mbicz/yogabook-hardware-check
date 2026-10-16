"""HID hinge conversion, not an angle-based lid/sleep policy.

Kernel Documentation/ABI/testing/sysfs-bus-iio defines in_angl after offset
and scale as radians. hid-sensor-custom-intel-hinge.c exposes three labels
and shared scale/offset. Raw counts are *not* assumed to be radians/degrees.
The current firmware's documented scale converts degree counts to radians.
"""
from __future__ import annotations

import math
import re


class UnitsUnavailable(ValueError):
    """The sample cannot be interpreted without a known unit/conversion."""


LABELS = {"hinge", "screen", "keyboard"}


def scalar(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"nonfinite angle/conversion: {value!r}")
    return result


def angle_degrees(raw: str, scale: str | None, offset: str | None, unit: str) -> dict:
    if unit != "radians" or scale is None or offset is None:
        raise UnitsUnavailable("known IIO angular units, scale and offset are required")
    count, multiplier, shift = scalar(raw), scalar(scale), scalar(offset)
    if multiplier <= 0:
        raise ValueError("angle scale must be positive")
    radians = (count + shift) * multiplier
    degrees = math.degrees(radians)
    # This firmware reports unsigned full-turn angles. Allow only rounding at
    # the 0/360 boundary; modulo must not hide invalid multi-turn telemetry.
    if not math.isfinite(degrees) or not -0.1 <= degrees <= 360.1:
        raise ValueError(f"angle outside documented one-turn range: {degrees} degrees")
    return {"raw": count, "scale": multiplier, "offset": shift, "radians": radians,
            "degrees": degrees % 360.0}


def labeled_channels(labels: dict[str, str]) -> dict[str, str]:
    result = {}
    for filename, label in labels.items():
        if label not in LABELS:
            continue
        if not re.fullmatch(r"in_angl\d+_label", filename) or label in result:
            raise ValueError(f"ambiguous hinge channel label: {filename}={label}")
        result[label] = filename.removesuffix("_label")
    if result.keys() != LABELS:
        raise ValueError(f"missing labeled hinge channels: {LABELS - result.keys()}")
    return result


def conversion_attributes(channel: str, available: set[str]) -> tuple[str, str]:
    names = []
    for suffix in ("scale", "offset"):
        specific, shared = f"{channel}_{suffix}", f"in_angl_{suffix}"
        if specific in available:
            names.append(specific)
        elif shared in available:
            names.append(shared)
        else:
            raise UnitsUnavailable(f"missing {suffix} for {channel}")
    return names[0], names[1]


def hinge_frame(channels: dict[str, str], values: dict[str, str], unit: str) -> dict:
    result = {}
    for label, channel in channels.items():
        scale, offset = conversion_attributes(channel, set(values))
        result[label] = angle_degrees(values[f"{channel}_raw"], values[scale], values[offset], unit)
    return result


def circular_distance(first: float, second: float) -> float:
    if not math.isfinite(first) or not math.isfinite(second):
        raise ValueError("nonfinite circular angle")
    return abs((first - second + 180.0) % 360.0 - 180.0)


def planar_agreement(frame: dict) -> dict:
    """Diagnostic only: projected half-angles are not a calibrated 3-D pose."""
    hinge = circular_distance(frame["hinge"]["degrees"], 0)
    halves = circular_distance(frame["screen"]["degrees"], frame["keyboard"]["degrees"])
    return {"hinge_unsigned_degrees": hinge, "projected_halves_degrees": halves,
            "difference_degrees": abs(hinge - halves)}


def hub_agreement(first: dict, second: dict) -> dict[str, float]:
    return {label: circular_distance(first[label]["degrees"], second[label]["degrees"]) for label in LABELS}
