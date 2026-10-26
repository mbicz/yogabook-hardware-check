"""Small Linux media helpers; no device-number assumptions or import-time I/O."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import signal
import struct
import subprocess
import time
import wave


def camera_inventory(root: Path = Path("/sys/class/video4linux")) -> list[dict]:
    found = []
    for entry in sorted(root.glob("*")):
        name = entry / "name"
        if not name.is_file():
            continue
        driver = entry / "device/driver"
        found.append({"node": f"/dev/{entry.name}", "name": name.read_text().strip(),
                      "driver": driver.resolve().name if driver.exists() else None})
    return found


def parse_camera_result(text: str, sensor: str) -> dict:
    """Reject incomplete/unsafe capture reports, independently of native exit status."""
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("camera report must be an object")
    for field in ("width", "height", "stride", "bytes"):
        if type(value.get(field)) is not int or value[field] <= 0:
            raise ValueError(f"missing/nonpositive integer negotiated {field}")
    if value["stride"] < value["width"] * 2:
        raise ValueError("YUYV stride is smaller than visible row width")
    if value["bytes"] < value["stride"] * value["height"]:
        raise ValueError("sizeimage is smaller than negotiated stride times height")
    for field in ("frames", "buffers", "minimum_bytesused", "maximum_bytesused"):
        if type(value.get(field)) is not int:
            raise ValueError(f"missing/noninteger {field}")
    if value["frames"] < 40 or value["buffers"] != 8:
        raise ValueError("capture requires at least forty frames and eight MMAP buffers")
    if not (value["stride"] * value["height"] <= value["minimum_bytesused"]
            <= value["maximum_bytesused"] <= value["bytes"]):
        raise ValueError("captured byte counts violate negotiated buffer bounds")
    if value.get("sensor") != sensor:
        raise ValueError("wrong sensor captured")
    if not isinstance(value.get("inputs"), list) or not any(
            isinstance(name, str) and sensor in name.lower() for name in value["inputs"]):
        raise ValueError("requested sensor absent from enumerated V4L2 inputs")
    if not re.fullmatch(r"/dev/video[0-9]+", str(value.get("node", ""))):
        raise ValueError("invalid capture node identity")
    return value


def parse_alsa_cards(text: str) -> dict[str, int]:
    cards = {}
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)\s+\[([^]]+)\]\s*:", line)
        if match:
            identity = match[2].strip()
            if identity in cards:
                raise ValueError(f"ambiguous ALSA card identity {identity}")
            cards[identity] = int(match[1])
    return cards


def parse_pcm_status(text: str) -> dict:
    status = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            status[key.strip()] = value.strip()
    if "state" not in status:
        raise ValueError(f"PCM status has no state: {text!r}")
    for key in ("hw_ptr", "appl_ptr"):
        if key in status:
            try:
                status[key] = int(status[key])
            except ValueError as error:
                raise ValueError(f"invalid PCM {key}: {status[key]!r}") from error
    return status


def running_pointer_advanced(samples: list[dict]) -> bool:
    # Compare adjacent RUNNING observations, never closed/DRAINING status or appl_ptr.
    for previous, current in zip(samples, samples[1:]):
        if (previous.get("state") == current.get("state") == "RUNNING"
                and type(previous.get("hw_ptr")) is int
                and type(current.get("hw_ptr")) is int
                and current["hw_ptr"] > previous["hw_ptr"]):
            return True
    return False


def sustained_pcm_progress(samples: list[dict], minimum_seconds: float = 2) -> bool:
    first = previous = None
    advanced = False
    for sample in samples:
        if (sample.get("state") != "RUNNING" or type(sample.get("hw_ptr")) is not int
                or not isinstance(sample.get("elapsed_seconds"), (int, float))):
            first = previous = None
            advanced = False
            continue
        if previous is None or sample["hw_ptr"] < previous["hw_ptr"]:
            first = sample
            advanced = False
        else:
            advanced = advanced or sample["hw_ptr"] > previous["hw_ptr"]
        if advanced and sample["elapsed_seconds"] - first["elapsed_seconds"] >= minimum_seconds:
            return True
        previous = sample
    return False


def parse_ucm_names(text: str) -> list[str]:
    return [match[1].strip().strip('"') for line in text.splitlines()
            if (match := re.match(r"\s*\d+:\s*(\S.*?)\s*$", line))]


def parse_ucm_pcm(text: str, card: str) -> list[str]:
    """Physical hw: PCMs of `card` named in alsaucm `get` output.

    alsaucm prints either the bare value or `Identifier=value`, and alsa-lib
    1.2.11+ prefixes UCM-defined PCM names with its private configuration
    namespace (`_ucm0001.hw:chtyogabook`); both refer to the same hw: device.
    """
    pattern = (rf"(?m)^\s*(?:[^=\s]+=)?(?:_ucm\d+\.)?"
               rf"(hw:(?:CARD=)?{re.escape(card)}(?:,(?:DEV=)?\d+)?)\s*$")
    return re.findall(pattern, text)


@dataclass(frozen=True)
class CaptureParameters:
    format: str
    rate: int
    channels: int
    sample_bytes: int


def parse_capture_parameters(text: str) -> CaptureParameters:
    """Choose only parameters explicitly contained in ALSA's advertised constraints."""
    fields = dict(re.findall(r"^\s*(FORMAT|RATE|CHANNELS):\s*(.+)$", text, re.MULTILINE))
    formats = set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", fields.get("FORMAT", "")))
    chosen = next(((name, size) for name, size in (("S16_LE", 2), ("S32_LE", 4), ("S24_LE", 4))
                   if name in formats), None)
    if not chosen:
        raise ValueError("no supported signed little-endian PCM format advertised")

    def choose(field: str, preferred: tuple[int, ...]) -> int:
        value = fields.get(field, "").strip()
        if re.fullmatch(r"\d+", value):
            number = int(value)
            if number > 0:
                return number
        match = re.fullmatch(r"\[\s*(\d+)\s+(\d+)\s*\]", value)
        if match:
            low, high = map(int, match.groups())
            for candidate in preferred:
                if low <= candidate <= high:
                    return candidate
        # Open ALSA intervals and complex constraints aren't guessed.
        raise ValueError(f"unsupported {field} constraint: {value!r}")

    return CaptureParameters(chosen[0], choose("RATE", (48000, 44100, 16000, 8000)),
                             choose("CHANNELS", (2, 1)), chosen[1])


def write_tone(path: Path, seconds: int = 8) -> None:
    """Stereo 48kHz S16_LE, -36dBFS 440Hz; generated once before playback."""
    rate = 48000
    amplitude = int(32767 * 10 ** (-36 / 20))
    # One-second block repeats phase-continuously at an integer frequency.
    block = b"".join(struct.pack("<hh", sample, sample)
                     for frame in range(rate)
                     for sample in [round(amplitude * math.sin(2 * math.pi * 440 * frame / rate))])
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        for _ in range(seconds):
            audio.writeframesraw(block)


@contextmanager
def owned_process(argv: list[str], diagnostics: Path):
    """Own only this process group; TERM then bounded KILL and always reap."""
    with diagnostics.open("w+") as output:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                   start_new_session=True)
        try:
            yield process
        finally:
            if process.poll() is None:
                try:
                    import os
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=3)
            else:
                process.wait()


def observe_pcm(process: subprocess.Popen, status_path: Path, timeout: float = 4) -> list[dict]:
    samples = []
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            sample = parse_pcm_status(status_path.read_text())
        except (FileNotFoundError, ValueError):
            time.sleep(0.1)
            continue
        sample["elapsed_seconds"] = round(time.monotonic() - started, 3)
        samples.append(sample)
        if sustained_pcm_progress(samples):
            return samples
        time.sleep(0.2)
    return samples


def blocked_audio_error(text: str) -> bool:
    return any(word in text.lower() for word in
               ("device or resource busy", "permission denied", "operation not permitted"))
