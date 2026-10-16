"""Non-disruptive power contracts; these tests never request sleep or change PM."""
import errno
import json
from pathlib import Path
import sys
import warnings

import pytest

from checks.yogabook.platform import identity, text
from checks.yogabook.power import (config_section, counter_delta, cpu_set, cpu_ticks,
                                  energy_paths, frequency_times, parse_properties,
                                  runtime_counter_deltas, runtime_findings, runtime_paths, sleep_contract)
from ybcheck.plugin import HardwareWarning


CPU = Path("/sys/devices/system/cpu")


def snapshot(shell, evidence, kind, *paths, label="current"):
    result = shell(sys.executable, str(Path(__file__).with_name("power.py")), kind,
                   *(str(path) for path in paths), timeout=15)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    evidence(f"snapshot_{kind}_{label}", data)
    return data


def readable(data):
    errors = data["errors"]
    unexpected = {path: error for path, error in errors.items()
                  if error["errno"] not in {errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENODEV, errno.EBUSY, errno.EAGAIN}}
    assert not unexpected, f"power telemetry read errors: {unexpected}"
    if errors:
        pytest.skip(f"blocked: required power telemetry unavailable: {errors}")
    assert data["values"], "empty power snapshot"
    return data["values"]


def cpu_inventory(evidence):
    policies = sorted((CPU / "cpufreq").glob("policy*"))
    states = sorted(CPU.glob("cpu[0-9]*/cpuidle/state*"))
    evidence("cpu_power", {"policies": [{"path": str(path), "driver": text(path / "scaling_driver")} for path in policies],
                           "idle_states": [{"path": str(path), "name": text(path / "name")} for path in states],
                           "idle_driver": text(CPU / "cpuidle/current_driver")})
    assert policies, "CPU frequency policies missing"
    assert states, "CPU idle states missing"
    assert text(CPU / "cpuidle/current_driver") == "intel_idle", "expected Cherry Trail intel_idle"
    online = cpu_set(text(CPU / "online"))
    coverage = set()
    for path in policies:
        assert text(path / "scaling_driver") == "intel_cpufreq", f"unexpected cpufreq driver: {path}"
        coverage.update(cpu_set(text(path / "affected_cpus")))
    assert online <= coverage, f"online CPUs without cpufreq policy: {online - coverage}"
    idle_cpus = {int(path.parent.parent.name[3:]) for path in states}
    assert online <= idle_cpus, f"online CPUs without cpuidle states: {online - idle_cpus}"
    return policies, states


@pytest.mark.hardware(id="power.cpu.enumeration", component="power.cpu", layer="enumeration")
def test_cpu_power_enumeration(evidence):
    cpu_inventory(evidence)


@pytest.mark.hardware(id="power.cpu.state_read", component="power.cpu", layer="headless")
def test_cpu_power_read(shell, evidence):
    policies, states = cpu_inventory(evidence)
    values = readable(snapshot(shell, evidence, "cpu"))
    for path in policies:
        get = lambda name: values[str(path / name)]
        assert int(get("scaling_cur_freq")) > 0, path
        assert 0 < int(get("scaling_min_freq")) <= int(get("scaling_max_freq")), path
        assert get("scaling_governor"), path
    for path in states:
        assert int(values[str(path / "usage")]) >= 0, path
        assert int(values[str(path / "time")]) >= 0, path
        assert int(values[str(path / "disable")]) in (0, 1), path
    evidence("limitations", "Policy and counter validity only; no frequency target, deep-state requirement or power floor.")


@pytest.mark.hardware(id="power.cpu.counter_delta", component="power.cpu", layer="headless")
def test_cpu_counter_delta(shell, evidence):
    cpu_inventory(evidence)
    before = readable(snapshot(shell, evidence, "cpu", label="before"))
    snapshot(shell, evidence, "activity")
    after = readable(snapshot(shell, evidence, "cpu", label="after"))
    if before[str(CPU / "online")] != after[str(CPU / "online")] or before.keys() != after.keys():
        pytest.skip("blocked: CPU topology changed during counter observation")
    deltas = {name: counter_delta(int(value), int(after[name]))["delta"]
              for name, value in before.items() if name.endswith(("/usage", "/time"))}
    evidence("idle_counter_deltas", {"units": {"time": "microseconds", "usage": "entries"}, "deltas": deltas})
    ticks = counter_delta(cpu_ticks(before["/proc/stat"]), cpu_ticks(after["/proc/stat"]))["delta"]
    evidence("aggregate_cpu_tick_delta", ticks)
    assert ticks > 0, "aggregate CPU accounting did not advance during bounded activity"
    if not any(deltas.values()):
        warnings.warn("No idle residency observed in this short busy-machine window; not proof of broken idle or suspend.", HardwareWarning)
    evidence("limitations", "50 ms one-process arithmetic plus 200 ms observation; no affinity/governor writes. Zero deep-state residency is allowed. This does not test suspend/resume.")


@pytest.mark.hardware(id="power.cpu.frequency_counters", component="power.cpu", layer="headless")
def test_frequency_counters(shell, evidence):
    policies, _ = cpu_inventory(evidence)
    paths = [path / "stats/time_in_state" for path in policies]
    if not all(path.exists() for path in paths):
        pytest.skip("blocked: cpufreq time_in_state telemetry not exposed for every policy")
    before = readable(snapshot(shell, evidence, "read", *paths, label="before"))
    snapshot(shell, evidence, "activity")
    after = readable(snapshot(shell, evidence, "read", *paths, label="after"))
    if before.keys() != after.keys():
        pytest.skip("blocked: cpufreq telemetry changed during observation")
    deltas = {}
    for path in before:
        first, second = frequency_times(before[path]), frequency_times(after[path])
        if first.keys() != second.keys():
            pytest.skip("blocked: cpufreq frequency table changed during observation")
        deltas[path] = {frequency: counter_delta(ticks, second[frequency])["delta"] for frequency, ticks in first.items()}
    evidence("frequency_residency_delta", {"time_unit": "10 ms (cpufreq stats ABI)", "frequency_unit": "kHz", "deltas": deltas})
    if not any(ticks for policy in deltas.values() for ticks in policy.values()):
        warnings.warn("Exposed cpufreq residency counters did not advance; window/driver accounting may be insufficient.", HardwareWarning)


@pytest.mark.hardware(id="power.sleep.states", component="power.sleep", layer="headless")
def test_sleep_state_contract(shell, evidence):
    values = readable(snapshot(shell, evidence, "read", Path("/sys/power/state"), Path("/sys/power/mem_sleep")))
    try:
        contract = sleep_contract(values["/sys/power/state"], values["/sys/power/mem_sleep"])
    except ValueError as error:
        pytest.fail(str(error))
    evidence("sleep_contract", contract)
    evidence("limitations", "Supported/selected state only. CHT has no expected S3; freeze is another s2idle entry, not proof of resume.")


def query(shell, evidence, *argv):
    result = shell(*argv, timeout=10)
    evidence("policy_query:" + " ".join(argv), {"argv": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
    absent_unit = argv[:2] == ("systemctl", "show") and parse_properties(result.stdout).get("LoadState") == "not-found"
    if result.returncode and not absent_unit:
        pytest.skip(f"blocked: cannot query effective power policy: {' '.join(argv)}: {result.stderr}")
    return result.stdout


@pytest.mark.hardware(id="power.sleep.policy", component="power.sleep", layer="userspace")
def test_sleep_policy(shell, evidence, require_program):
    for program in ("systemctl", "systemd-analyze", "systemd-inhibit", "busctl"):
        require_program(program)
    for unit in ("sleep.target", "suspend.target", "hibernate.target", "hybrid-sleep.target", "suspend-then-hibernate.target"):
        properties = parse_properties(query(shell, evidence, "systemctl", "show", unit, "--no-pager",
                                             "--property=LoadState,UnitFileState,ActiveState"))
        if properties.get("LoadState") == "masked" or properties.get("UnitFileState") in {"masked", "masked-runtime"}:
            warnings.warn(f"{unit} is masked; supported kernel sleep does not mean userspace can request it.", HardwareWarning)
    query(shell, evidence, "systemd-inhibit", "--list", "--no-pager")
    # Query the live manager, not just files that may have changed since startup.
    for name in ("BlockInhibited", "DelayInhibited", "HandleLidSwitch", "HandleLidSwitchExternalPower", "HandleLidSwitchDocked"):
        value = query(shell, evidence, "busctl", "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
                      "org.freedesktop.login1.Manager", name).strip()
        if name.endswith("Inhibited") and any(what in value for what in ("sleep", "handle-lid-switch")):
            warnings.warn(f"Live logind {name}: {value}; an inhibitor is policy, not hardware resume failure.", HardwareWarning)
        elif name.startswith("HandleLidSwitch") and value != 's "ignore"':
            warnings.warn(f"Live logind {name}={value}; switch-only watcher expects ignore to avoid two sleep owners.", HardwareWarning)
    config = config_section(query(shell, evidence, "systemd-analyze", "cat-config", "systemd/sleep.conf"), "Sleep")
    evidence("sleep_configuration_on_disk", config)
    if config.get("SuspendState", "mem standby freeze").split() != ["mem"]:
        warnings.warn("SuspendState is not mem-only: fallback freeze can retry a wake-cancelled s2idle request.", HardwareWarning)
    evidence("limitations", "Query-only live inhibitors/masks/logind and on-disk sleep configuration. No inhibitor removed, service restarted or sleep requested; policy does not prove physical wake reliability.")


@pytest.mark.hardware(id="power.runtime.state_delta", component="power.runtime", layer="headless")
def test_runtime_pm(shell, evidence):
    paths = runtime_paths()
    if not paths:
        pytest.skip("blocked: no device runtime-PM telemetry exposed")
    evidence("runtime_devices", [identity(path.parent) for path in paths])
    before = readable(snapshot(shell, evidence, "runtime", *paths, label="before"))
    snapshot(shell, evidence, "activity")
    after = readable(snapshot(shell, evidence, "runtime", *paths, label="after"))
    if before.keys() != after.keys():
        pytest.skip("blocked: runtime-PM telemetry changed during observation")
    persistent, transient, deltas, unavailable = [], [], {}, {}
    for path in paths:
        first = {Path(name).name: value for name, value in before.items() if Path(name).parent == path}
        second = {Path(name).name: value for name, value in after.items() if Path(name).parent == path}
        if not first or not second:
            pytest.skip(f"blocked: runtime telemetry disappeared: {path}")
        initial, final = runtime_findings(first), runtime_findings(second)
        if initial and final:
            persistent.append(str(path))
        elif initial or final:
            transient.append(str(path))
        counters = runtime_counter_deltas(first, second)
        deltas.update({str(path / name): value for name, value in counters["deltas"].items()})
        unavailable.update({str(path / name): value for name, value in counters["unavailable_wakeup_counters"].items()})
    evidence("runtime_deltas", {"runtime_time_unit": "milliseconds", "deltas": deltas,
                                "unavailable_wakeup_counters": unavailable,
                                "persistent_errors": persistent, "transient_errors": transient})
    assert not persistent, f"persistent exposed runtime-PM errors: {persistent}"
    if transient:
        warnings.warn(f"Runtime-PM error in one snapshot (not persistent): {transient}", HardwareWarning)
    if not any(name.endswith(("runtime_active_time", "runtime_suspended_time")) for name in deltas):
        pytest.skip("blocked: runtime-PM residency counters not exposed")
    evidence("limitations", "Active, autosuspended and unsupported devices are valid snapshots. No device power/control writes, functional GPU/ISP/GPS access, or forced autosuspend. Time accounting does not establish energy efficiency.")


@pytest.mark.hardware(id="power.wakeup.sources", component="power.wakeup", layer="headless")
def test_wakeup_sources(shell, evidence):
    sources = sorted(Path("/sys/class/wakeup").glob("wakeup*"))
    if not sources:
        pytest.skip("blocked: kernel wakeup-source class telemetry unavailable")
    names = ("name", "event_count", "active_count", "wakeup_count", "expire_count", "total_time_ms", "prevent_suspend_time_ms")
    paths = [source / name for source in sources for name in names if (source / name).exists()]
    before = readable(snapshot(shell, evidence, "read", *paths, label="before"))
    snapshot(shell, evidence, "activity")
    after = readable(snapshot(shell, evidence, "read", *paths, label="after"))
    if before.keys() != after.keys():
        pytest.skip("blocked: wakeup-source telemetry changed during observation")
    deltas = {name: counter_delta(int(value), int(after[name]))["delta"]
              for name, value in before.items() if Path(name).name != "name"}
    evidence("wakeup_source_deltas", {"sources": [identity(path) for path in sources], "deltas": deltas})
    if not deltas:
        pytest.skip("blocked: wakeup-source counters missing")
    evidence("limitations", "No wake event forced; zero deltas are valid. Header-only /proc/acpi/wakeup is normal on CHT, not absence of GPIO wake. Source registration/counters do not prove a physical lid wake.")


@pytest.mark.hardware(id="power.rapl.enumeration", component="power.rapl", layer="enumeration")
def test_rapl_enumeration(evidence):
    domains = energy_paths()
    evidence("rapl_domains", [{"path": str(path), "name": text(path / "name")} for path in domains])
    assert domains, "expected Intel RAPL powercap energy domains missing"


@pytest.mark.hardware(id="power.rapl.energy_delta", component="power.rapl", layer="headless")
def test_rapl_energy(shell, evidence):
    domains = energy_paths()
    if not domains:
        pytest.skip("blocked: RAPL energy telemetry unavailable (see enumeration)")
    paths = [path / name for path in domains for name in ("name", "energy_uj", "max_energy_range_uj")]
    before = readable(snapshot(shell, evidence, "read", *paths, label="before"))
    snapshot(shell, evidence, "activity")
    after = readable(snapshot(shell, evidence, "read", *paths, label="after"))
    if before.keys() != after.keys():
        pytest.skip("blocked: RAPL telemetry changed during observation")
    deltas = []
    for path in domains:
        maximum = int(before[str(path / "max_energy_range_uj")])
        if maximum != int(after[str(path / "max_energy_range_uj")]):
            pytest.skip("blocked: RAPL energy range changed during observation")
        delta = counter_delta(int(before[str(path / "energy_uj")]), int(after[str(path / "energy_uj")]), maximum)
        deltas.append({"path": str(path), "name": before[str(path / "name")], "microjoules": delta["delta"], "wrapped": delta["wrapped"]})
    evidence("rapl_energy_deltas", deltas)
    if any(item["wrapped"] for item in deltas):
        warnings.warn("RAPL counter decreased: delta assumes one wrap at exposed max_energy_range_uj; a reset cannot be distinguished from wrap by two samples.", HardwareWarning)
    if not any(item["microjoules"] for item in deltas):
        warnings.warn("RAPL counters readable but no energy change observed; no numerical power floor is required.", HardwareWarning)
    evidence("limitations", "Microjoule energy deltas, not whole-system power, calibrated battery runtime or deep-idle efficiency. No power limit changed.")
