"""Read-only power telemetry and deterministic Linux ABI interpretation.

Executed as a bounded child by the hardware checks; never writes power policy.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import time


def cpu_set(value: str) -> set[int]:
    """Accept sysfs CPU lists (0-3,8), including space-separated policy masks."""
    result = set()
    for field in value.replace(",", " ").split():
        if not re.fullmatch(r"\d+(?:-\d+)?", field):
            raise ValueError(f"invalid CPU list {value!r}")
        ends = [int(part) for part in field.split("-")]
        first, last = ends[0], ends[-1]
        if last < first or last > 1048575:
            raise ValueError(f"invalid CPU range {field}")
        result.update(range(first, last + 1))
    if not result:
        raise ValueError("empty CPU list")
    return result


def sleep_contract(state: str, mem_sleep: str) -> dict:
    supported = state.split()
    modes = mem_sleep.split()
    selected = [mode[1:-1] for mode in modes if mode.startswith("[") and mode.endswith("]")]
    available = [mode.strip("[]") for mode in modes]
    if len(selected) != 1 or any(mode not in {"s2idle", "shallow", "deep"} for mode in available):
        raise ValueError(f"invalid selected mem_sleep state: {mem_sleep!r}")
    if "mem" not in supported or "freeze" not in supported:
        raise ValueError(f"missing CHT mem/freeze support: {state!r}")
    if available != ["s2idle"] or selected != ["s2idle"]:
        raise ValueError(f"expected s2idle-only Cherry Trail firmware, got {mem_sleep!r}")
    return {"supported": supported, "mem_available": available, "mem_selected": selected[0],
            "s3_expected": False}


def counter_delta(before: int, after: int, maximum: int | None = None) -> dict:
    if before < 0 or after < 0:
        raise ValueError("negative cumulative counter")
    if maximum is not None and (maximum <= 0 or before > maximum or after > maximum):
        raise ValueError("counter outside exposed wrap range")
    if after < before and maximum is None:
        raise ValueError("counter decreased (reset, hotplug or broken telemetry)")
    return {"delta": after - before if after >= before else maximum - before + after,
            "wrapped": after < before}


def runtime_findings(values: dict[str, str]) -> list[str]:
    """A suspended device is healthy; a persistent runtime error is not."""
    status = values.get("runtime_status")
    if status not in {"active", "suspended", "suspending", "resuming", "unsupported", "error"}:
        raise ValueError(f"invalid runtime status {status!r}")
    if values.get("control", "auto") not in {"auto", "on"}:
        raise ValueError("invalid runtime PM control")
    for name in ("runtime_active_time", "runtime_suspended_time", "runtime_usage"):
        if name in values and int(values[name]) < 0 and name != "runtime_usage":
            raise ValueError(f"negative {name}")
    if status == "error" or int(values.get("runtime_error", "0")) != 0 or int(values.get("runtime_usage", "0")) < 0:
        return ["runtime PM reports an error"]
    return []


def runtime_counter_deltas(before: dict[str, str], after: dict[str, str]) -> dict:
    """Empty wakeup counters mean no attached wakeup_source, not a zero count.

    drivers/base/power/sysfs.c emits a newline when dev->power.wakeup is NULL.
    Mandatory runtime counters still require valid integers.
    """
    deltas, unavailable = {}, {}
    for name in ("runtime_active_time", "runtime_suspended_time", "wakeup_count",
                 "wakeup_active_count", "wakeup_abort_count"):
        if name not in before or name not in after:
            continue
        if name.startswith("wakeup_") and (not before[name] or not after[name]):
            unavailable[name] = {"before": before[name], "after": after[name]}
            continue
        deltas[name] = counter_delta(int(before[name]), int(after[name]))["delta"]
    return {"deltas": deltas, "unavailable_wakeup_counters": unavailable}


def parse_properties(value: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in value.splitlines() if "=" in line)


def config_section(value: str, section: str) -> dict[str, str]:
    """Parse the ordered output of systemd-analyze cat-config, last assignment wins."""
    result = {}
    current = None
    for line in value.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == section and "=" in line:
            key, setting = line.split("=", 1)
            result[key.strip()] = setting.strip()
    return result


def sample_values(paths) -> dict:
    values, errors = {}, {}
    for path in paths:
        try:
            values[str(path)] = path.read_text().strip()
        except OSError as error:
            errors[str(path)] = {"errno": error.errno, "error": str(error)}
    return {"values": values, "errors": errors}


def cpu_sample(root: Path = Path("/sys/devices/system/cpu"), proc: Path = Path("/proc/stat")) -> dict:
    paths = [root / "online", proc]
    for policy in sorted((root / "cpufreq").glob("policy*")):
        paths.extend(policy / name for name in ("affected_cpus", "scaling_driver", "scaling_cur_freq",
                     "scaling_min_freq", "scaling_max_freq", "scaling_governor"))
        if (policy / "stats/time_in_state").exists():
            paths.append(policy / "stats/time_in_state")
    for state in sorted(root.glob("cpu[0-9]*/cpuidle/state*")):
        paths.extend(state / name for name in ("usage", "time", "disable"))
    return sample_values(paths)


def runtime_paths(bus_root: Path = Path("/sys/bus")) -> list[Path]:
    """Enumerate PM metadata only, without opening GPU/ISP/GPS functional nodes."""
    found = set()
    for bus in ("pci", "platform", "i2c", "spi", "usb", "sdio", "serial", "hid", "mmc"):
        for device in (bus_root / bus / "devices").glob("*"):
            power = device.resolve() / "power"
            if (power / "runtime_status").exists():
                found.add(power)
    return sorted(found)


def runtime_sample(paths: list[Path]) -> dict:
    names = ("runtime_status", "control", "runtime_active_time", "runtime_suspended_time",
             "runtime_usage", "runtime_error", "wakeup",
             "wakeup_count", "wakeup_active_count", "wakeup_abort_count")
    return sample_values(power / name for power in paths for name in names if (power / name).exists())


def energy_paths(root: Path = Path("/sys/class/powercap")) -> list[Path]:
    return sorted({path.resolve() for path in root.glob("intel-rapl*") if (path / "energy_uj").exists()} |
                  {path.parent.resolve() for controller in root.glob("intel-rapl*")
                   for path in controller.glob("**/energy_uj")})


def cpu_ticks(value: str) -> int:
    lines = value.splitlines()
    fields = lines[0].split() if lines else []
    if len(fields) < 5 or fields[0] != "cpu":
        raise ValueError("missing aggregate /proc/stat CPU counters")
    # guest and guest_nice already belong to user/nice: do not count twice.
    ticks = [int(field) for field in fields[1:9]]
    if any(tick < 0 for tick in ticks):
        raise ValueError("negative CPU tick counter")
    return sum(ticks)


def frequency_times(value: str) -> dict[int, int]:
    result = {}
    for line in value.splitlines():
        frequency, ticks = map(int, line.split())
        if frequency <= 0 or ticks < 0 or frequency in result:
            raise ValueError("invalid frequency residency counters")
        result[frequency] = ticks
    if not result:
        raise ValueError("empty frequency residency counters")
    return result


if __name__ == "__main__":
    kind = sys.argv[1]
    if kind == "cpu":
        print(json.dumps(cpu_sample()))
    elif kind == "runtime":
        paths = [Path(path) for path in sys.argv[2:]]
        print(json.dumps(runtime_sample(paths)))
    elif kind == "read":
        print(json.dumps(sample_values(Path(path) for path in sys.argv[2:])))
    elif kind == "activity":
        # One modest, bounded single-process burst; no performance/power floor.
        deadline = time.monotonic() + 0.05
        value = 1
        while time.monotonic() < deadline:
            value = (value * 1664525 + 1013904223) & 0xffffffff
        time.sleep(0.2)
        print(json.dumps({"activity_seconds": 0.05, "observation_seconds": 0.2}))
    else:
        raise ValueError(f"unknown snapshot kind {kind}")
