"""Power ABI regressions using local temporary files, not real device access."""
import errno

import pytest

from checks.yogabook.power import (config_section, counter_delta, cpu_sample, cpu_set,
                                  cpu_ticks, energy_paths, frequency_times,
                                  parse_properties, runtime_findings, runtime_paths,
                                  runtime_sample, sample_values, sleep_contract)


@pytest.mark.parametrize(("value", "expected"), [
    ("0-3,8,10-11", {0, 1, 2, 3, 8, 10, 11}),
    ("0 1 4-5", {0, 1, 4, 5}), ("3", {3}), ("0-1,1", {0, 1}),
])
def test_online_and_policy_cpu_lists(value, expected):
    assert cpu_set(value) == expected


@pytest.mark.parametrize("value", ["", "3-1", "-1", "1-2-3", "0,word", "1-1048576"])
def test_cpu_list_rejects_unknown_topology(value):
    with pytest.raises(ValueError):
        cpu_set(value)


def test_sleep_contract_s2idle_not_s3():
    assert sleep_contract("freeze mem disk", "[s2idle]") == {
        "supported": ["freeze", "mem", "disk"], "mem_available": ["s2idle"],
        "mem_selected": "s2idle", "s3_expected": False,
    }


@pytest.mark.parametrize(("state", "mem"), [
    ("freeze", "[s2idle]"), ("mem", "[s2idle]"), ("freeze mem", "s2idle"),
    ("freeze mem", "s2idle [deep]"), ("freeze mem", "[s2idle] deep"),
    ("freeze mem", "[s2idle] [deep]"), ("freeze mem", "[unknown]"),
])
def test_sleep_contract_rejects_wrong_support_or_selection(state, mem):
    with pytest.raises(ValueError):
        sleep_contract(state, mem)


@pytest.mark.parametrize(("before", "after", "maximum", "expected"), [
    (8, 12, None, {"delta": 4, "wrapped": False}),
    (8, 8, None, {"delta": 0, "wrapped": False}),
    (990, 5, 1000, {"delta": 15, "wrapped": True}),
    (1000, 0, 1000, {"delta": 0, "wrapped": True}),
    (0, 1000, 1000, {"delta": 1000, "wrapped": False}),
])
def test_cumulative_delta_and_exposed_energy_wrap(before, after, maximum, expected):
    assert counter_delta(before, after, maximum) == expected


@pytest.mark.parametrize(("before", "after", "maximum"), [
    (9, 2, None), (-1, 2, None), (1, -2, 100), (1001, 2, 1000),
    (1, 1001, 1000), (0, 0, 0), (0, 0, -1),
])
def test_counter_reset_negative_and_invalid_wrap_range_fail(before, after, maximum):
    with pytest.raises(ValueError):
        counter_delta(before, after, maximum)


@pytest.mark.parametrize("status", ["active", "suspended", "suspending", "resuming", "unsupported"])
def test_autosuspended_and_unsupported_runtime_devices_are_not_failures(status):
    assert runtime_findings({"runtime_status": status, "control": "auto", "runtime_active_time": "0",
                             "runtime_suspended_time": "123", "runtime_error": "0", "runtime_usage": "0"}) == []


@pytest.mark.parametrize("values", [
    {"runtime_status": "error"}, {"runtime_status": "active", "runtime_error": "-5"},
    {"runtime_status": "suspended", "runtime_error": "16"},
    {"runtime_status": "active", "runtime_usage": "-1"},
])
def test_exposed_runtime_error_and_usage_underflow_are_findings(values):
    assert runtime_findings(values)


@pytest.mark.parametrize("values", [
    {"runtime_status": "unknown"}, {"runtime_status": "active", "control": "off"},
    {"runtime_status": "active", "runtime_active_time": "-1"},
    {"runtime_status": "active", "runtime_suspended_time": "nan"},
    {"runtime_status": "active", "runtime_error": "error"},
])
def test_invalid_runtime_telemetry_is_not_coerced_to_zero(values):
    with pytest.raises(ValueError):
        runtime_findings(values)


def test_sleep_config_uses_last_active_assignment_not_commented_example():
    config = "# /usr/lib/systemd/sleep.conf\n[Sleep]\n#SuspendState=mem standby freeze\nSuspendState=mem freeze\n[Other]\nSuspendState=wrong\n# /etc/systemd/sleep.conf.d/yogabook.conf\n[Sleep]\nSuspendState=mem\n"
    assert config_section(config, "Sleep") == {"SuspendState": "mem"}
    assert config_section("[Sleep]\nSuspendState=mem\nSuspendState=\n", "Sleep") == {"SuspendState": ""}
    assert parse_properties("LoadState=masked\nActiveState=inactive\nFragmentPath=/a=b\n") == {
        "LoadState": "masked", "ActiveState": "inactive", "FragmentPath": "/a=b"}


def test_snapshot_distinguishes_missing_file_from_exposed_zero(tmp_path):
    zero, missing = tmp_path / "zero", tmp_path / "missing"
    zero.write_text("0\n")
    sample = sample_values([zero, missing])
    assert sample["values"] == {str(zero): "0"}
    assert sample["errors"][str(missing)]["errno"] == errno.ENOENT
    assert str(missing) not in sample["values"]


def test_runtime_discovery_follows_real_topology_and_deduplicates(tmp_path):
    device = tmp_path / "devices/arbitrary-controller"
    power = device / "power"
    power.mkdir(parents=True)
    (power / "runtime_status").write_text("suspended\n")
    (power / "runtime_suspended_time").write_text("19\n")
    for bus in ("pci", "platform"):
        directory = tmp_path / "bus" / bus / "devices"
        directory.mkdir(parents=True)
        (directory / "arbitrary-name").symlink_to(device, target_is_directory=True)
    unused = tmp_path / "bus/platform/devices/no-runtime"
    unused.mkdir()
    assert runtime_paths(tmp_path / "bus") == [power]
    assert runtime_sample([power])["values"] == {
        str(power / "runtime_status"): "suspended", str(power / "runtime_suspended_time"): "19"}


def test_rapl_discovery_resolves_nested_domain_symlink_duplicates(tmp_path):
    package = tmp_path / "devices/intel-rapl:7"
    core = package / "intel-rapl:7:2"
    core.mkdir(parents=True)
    for node in (package, core):
        (node / "energy_uj").write_text("1\n")
    root = tmp_path / "class/powercap"
    root.mkdir(parents=True)
    (root / "intel-rapl:7").symlink_to(package, target_is_directory=True)
    (root / "intel-rapl:7:2").symlink_to(core, target_is_directory=True)
    assert energy_paths(root) == sorted([package, core])


def test_cpu_sample_reads_discovered_policy_and_idle_counters(tmp_path):
    root = tmp_path / "cpu"
    policy = root / "cpufreq/policy9"
    idle = root / "cpu9/cpuidle/state4"
    policy.mkdir(parents=True)
    idle.mkdir(parents=True)
    (root / "online").write_text("9\n")
    proc = tmp_path / "stat"
    proc.write_text("cpu 1 2 3 4 5 6 7 8 100 100\n")
    for name, value in {"affected_cpus": "9", "scaling_driver": "intel_cpufreq", "scaling_cur_freq": "1200000",
                        "scaling_min_freq": "480000", "scaling_max_freq": "1440000", "scaling_governor": "schedutil"}.items():
        (policy / name).write_text(value)
    for name in ("usage", "time", "disable"):
        (idle / name).write_text("0")
    sample = cpu_sample(root, proc)
    assert not sample["errors"]
    assert sample["values"][str(idle / "usage")] == "0"
    assert sample["values"][str(policy / "affected_cpus")] == "9"
    assert not any("time_in_state" in path for path in sample["values"])
    assert cpu_ticks(sample["values"][str(proc)]) == 36


@pytest.mark.parametrize("value", ["", "cpu0 1 2 3 4\n", "cpu 1 2\n", "cpu 1 2 -3 4\n", "cpu one 2 3 4\n"])
def test_invalid_cpu_accounting_rejected(value):
    with pytest.raises(ValueError):
        cpu_ticks(value)


def test_frequency_residency_keeps_per_frequency_counters():
    assert frequency_times("480000 0\n1440000 12\n") == {480000: 0, 1440000: 12}


@pytest.mark.parametrize("value", ["", "480000 -1", "0 1", "480000 1\n480000 2", "480000 1 2", "word 1"])
def test_invalid_frequency_residency_is_not_success(value):
    with pytest.raises(ValueError):
        frequency_times(value)


def test_missing_wakeup_source_is_not_a_zero_counter_or_a_runtime_failure():
    from checks.yogabook.power import runtime_counter_deltas
    result = runtime_counter_deltas(
        {"runtime_active_time": "100", "wakeup_count": ""},
        {"runtime_active_time": "125", "wakeup_count": ""})
    assert result["deltas"] == {"runtime_active_time": 25}
    assert result["unavailable_wakeup_counters"] == {"wakeup_count": {"before": "", "after": ""}}


@pytest.mark.parametrize("name,value", [("runtime_active_time", ""), ("wakeup_count", "bad")])
def test_malformed_exposed_runtime_counters_still_rejected(name, value):
    from checks.yogabook.power import runtime_counter_deltas
    with pytest.raises(ValueError):
        runtime_counter_deltas({name: value}, {name: "10"})
