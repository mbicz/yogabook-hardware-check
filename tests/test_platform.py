"""Deterministic identity/parsing regressions; no real hardware access."""
from pathlib import Path

import pytest

from checks.yogabook.platform import bitmap_has, block_leaves, drivers, numeric, root_mount, sensor_kind, sensors, unescape_mount


def test_bitmap_native_words_preserve_omitted_padding():
    assert bitmap_has("1 0", 64)
    assert not bitmap_has("1 0", 4)
    assert bitmap_has("1 0", 32, word_bits=32)
    assert not bitmap_has("0", 0)
    assert bitmap_has("21", 5)


def test_sensor_identity_prefers_compass_over_auxiliary_rotation():
    assert sensor_kind("magn_3d", ["in_rot_from_north_magnetic_tilt_comp_raw", "in_magn_x_raw"]) == "compass"
    assert sensor_kind("future-name", ["in_anglvel_x_raw"]) == "gyroscope"
    assert sensor_kind("future-name", ["in_angl0_raw", "in_angl1_raw"]) == "hinge"
    assert sensor_kind("future-name", ["in_accel_x_raw", "in_accel_scale"]) == "accelerometer"
    assert sensor_kind("unknown", ["in_timestamp_raw"]) is None


def test_sensor_discovery_preserves_actual_channels_and_labels(tmp_path):
    node = tmp_path / "iio:device83"
    node.mkdir()
    (node / "name").write_text("hinge\n")
    for index, label in enumerate(("hinge", "screen", "keyboard")):
        (node / f"in_angl{index}_raw").write_text("0\n")
        (node / f"in_angl{index}_label").write_text(label + "\n")
    found = sensors(tmp_path)
    assert len(found) == 1
    assert found[0]["kind"] == "hinge"
    assert "in_angl0_raw" in found[0]["channels"]
    assert "in_angl_raw" not in found[0]["channels"]
    assert found[0]["labels"]["in_angl2_label"] == "keyboard"


def test_driver_discovery_walks_gpio_keys_ancestor_not_tablet_parent(tmp_path):
    driver = tmp_path / "drivers/gpio-keys"
    driver.mkdir(parents=True)
    platform = tmp_path / "devices/platform/gpio-keys.79.auto"
    node = platform / "input/input159"
    node.mkdir(parents=True)
    (platform / "driver").symlink_to(driver, target_is_directory=True)
    (node / "driver").symlink_to(tmp_path / "nonexistent-driver", target_is_directory=True)
    assert drivers(node) == {str(platform): "gpio-keys"}


def test_numeric_iio_vector_and_nonfinite_rejection():
    assert numeric("[1, -2, 0.25, 4]") == [1, -2, 0.25, 4]
    for invalid in ("", "nan", "1 inf", "not-a-sample"):
        with pytest.raises((AssertionError, ValueError)):
            numeric(invalid)


def test_root_mount_understands_optional_fields_and_escaped_source():
    mountinfo = "21 1 0:19 / /proc rw,nosuid - proc proc rw\n42 1 253:9 / / rw,relatime shared:2 - ext4 /dev/mapper/yoga\\040root rw\n"
    assert root_mount(mountinfo) == {"device": "253:9", "mountpoint": "/", "options": ["rw", "relatime"], "fstype": "ext4", "source": "/dev/mapper/yoga root"}
    assert unescape_mount(r"a\134b\011c") == "a\\b\tc"
    with pytest.raises(AssertionError, match="mounted root"):
        root_mount("21 1 0:19 / /proc rw - proc proc rw\n")


def test_root_storage_follows_device_mapper_slaves(tmp_path):
    physical = tmp_path / "devices/mmc-card/block/mmcblk87/mmcblk87p3"
    physical.mkdir(parents=True)
    mapper = tmp_path / "virtual/block/dm-19"
    (mapper / "slaves").mkdir(parents=True)
    (mapper / "slaves/mmcblk87p3").symlink_to(physical, target_is_directory=True)
    assert block_leaves(mapper) == [physical]


def test_block_dependency_cycle_is_not_infinite_recursion(tmp_path):
    block = tmp_path / "dm-3"
    (block / "slaves").mkdir(parents=True)
    (block / "slaves/dm-3").symlink_to(block, target_is_directory=True)
    with pytest.raises(AssertionError, match="cycle"):
        block_leaves(block)


def test_thermal_failures_do_not_hide_other_sensors_or_cpu_hwmon(tmp_path, monkeypatch):
    import subprocess
    from checks.yogabook import test_platform as platform_checks

    thermal, hwmon = tmp_path / "thermal", tmp_path / "hwmon"
    for index, (name, value) in enumerate((("STR0", "-273150"), ("STR2", "-273150"), ("PNIT", "42000"))):
        zone = thermal / f"thermal_zone{index}"
        zone.mkdir(parents=True)
        (zone / "type").write_text(name)
        (zone / "temp").write_text(value)
    core = hwmon / "hwmon9"
    core.mkdir(parents=True)
    (core / "name").write_text("coretemp")
    (core / "temp2_label").write_text("Core 0")
    (core / "temp2_input").write_text("38000")
    roots = {"/sys/class/thermal": thermal, "/sys/class/hwmon": hwmon}
    monkeypatch.setattr(platform_checks, "Path", lambda name: roots.get(str(name), Path(name)))
    evidence = {}
    with pytest.raises(AssertionError, match="invalid temperature channels"):
        platform_checks.test_thermal_read(
            lambda *argv, **kwargs: subprocess.run(argv, capture_output=True, text=True, **kwargs),
            lambda key, value: evidence.update({key: value}))
    assert {item["type"] for item in evidence["invalid_temperature_channels"]} == {"STR0", "STR2"}
    measured = {item["type"]: item["millidegrees_c"] for item in evidence["temperatures"]}
    assert measured["PNIT"] == 42000
    assert measured["coretemp/Core 0"] == 38000
