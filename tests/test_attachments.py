"""Offline regressions for actual board-check decisions, never hardware proof."""
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from checks.yogabook import test_attachments as checks
from checks.yogabook import test_network as network
from checks.yogabook import test_platform as platform_checks
from checks.yogabook import test_sensors as sensor_checks
from checks.yogabook.attachments import firmware_files, sd_cards, usb_controllers, usb_function, usb_peripherals
from ybcheck.plugin import HardwareWarning


def test_usb_pci_class_distinguishes_host_and_xdci(tmp_path):
    for name, value in (("0000:00:14.0", "0x0c0330"), ("0000:00:15.0", "0x0c03fe"), ("other", "0x010100")):
        node = tmp_path / name
        node.mkdir()
        (node / "class").write_text(value)
    assert {item["function"] for item in usb_controllers(tmp_path)} == {"host", "device"}
    assert usb_function("0x0c03fe") == "device"
    assert usb_function("0x0c0330") == "host"
    assert usb_function("0x010100") is None
    assert usb_function("") is None


def test_xdci_alone_does_not_satisfy_host_support(monkeypatch):
    monkeypatch.setattr(platform_checks, "usb_controllers", lambda: [{"path": "/xdci", "function": "device"}])
    with pytest.raises(AssertionError, match="not a host"):
        platform_checks.usb_hosts(lambda *args: None)


def test_usb_external_discovery_excludes_root_hubs_and_interfaces(tmp_path):
    for name, device_class in (("usb7", "09"), ("7-1", "09"), ("7-1.3", "00"), ("7-1.3:1.0", "03")):
        node = tmp_path / name
        node.mkdir()
        (node / "bDeviceClass").write_text(device_class)
    assert [Path(item["path"]).name for item in usb_peripherals(tmp_path)] == ["7-1.3"]


def test_no_peripheral_is_attachment_skip_not_a_fake_pass(monkeypatch):
    monkeypatch.setattr(checks, "usb_peripherals", lambda: [])
    with pytest.raises(pytest.skip.Exception, match="attachment:"):
        checks.test_usb_attached_read(None, lambda *args: None)


def test_sd_discovery_never_confuses_emmc_or_sdio_with_removable_card(tmp_path):
    for name, kind in (("mmc82:0001", "MMC"), ("mmc7:0001", "SDIO"), ("mmc39:aaaa", "SD")):
        card = tmp_path / name
        card.mkdir()
        (card / "type").write_text(kind)
    assert [path.name for path in sd_cards(tmp_path)] == ["mmc39:aaaa"]


def test_no_microsd_is_attachment_skip(monkeypatch):
    monkeypatch.setattr(checks, "sd_cards", lambda: [])
    with pytest.raises(pytest.skip.Exception, match="attachment:"):
        checks.test_microsd_read(None, lambda *args: None)


def test_disconnected_hdmi_does_not_fail_or_claim_external_scanout(monkeypatch):
    monkeypatch.setattr(checks, "hdmi_connectors", lambda: [Path("/connector")])
    monkeypatch.setattr(checks, "identity", lambda path: {"path": str(path)})
    with pytest.raises(pytest.skip.Exception, match="attachment:"):
        checks.test_hdmi_attached(lambda *args, **kwargs: CompletedProcess(args, 0, "disconnected\n", ""), lambda *args: None)


def test_firmware_discovery_supports_updates_and_compression(tmp_path):
    firmware = tmp_path / "updates/brcm/BCM4356A2.hcd.zst"
    firmware.parent.mkdir(parents=True)
    firmware.write_bytes(b"fixture bytes, not real firmware")
    assert firmware_files(("brcm/BCM4356A2.hcd",), (tmp_path,)) == [str(firmware)]


def test_radio_patch_request_and_rom_build_do_not_prove_patch_loaded(monkeypatch):
    messages = ["brcmfmac: Firmware: BCM4356 version 7 FWID 01", "Bluetooth: hci0: BCM4356A2 build 0000", "Bluetooth: hci0: BCM4356A2 'brcm/BCM4356A2.hcd' Patch"]
    capture = {"records": [{"message": message} for message in messages], "truncated": False, "malformed": 0}
    monkeypatch.setattr(network, "collect_logs", lambda: capture)
    with pytest.warns(HardwareWarning, match="No nonzero"):
        network.test_radio_firmware_boot(lambda *args: None)
    capture["records"].append({"message": "Bluetooth: hci0: BCM4356A2 build 0252"})
    evidence = {}
    network.test_radio_firmware_boot(lambda key, value: evidence.update({key: value}))
    assert evidence["radio_firmware_loaded"]["wifi_version_seen"] is True
    assert any(evidence["radio_firmware_loaded"]["bluetooth_builds"])


def test_zero_als_reads_warn_instead_of_clean_acceptance(monkeypatch):
    monkeypatch.setattr(sensor_checks, "selected", lambda *args: [{"path": "/als1", "channels": ["in_illuminance_raw"]}, {"path": "/als9", "channels": ["in_illuminance_raw"]}])
    monkeypatch.setattr(sensor_checks, "attribute", lambda *args: "0")
    monkeypatch.setattr(sensor_checks.time, "sleep", lambda *args: None)
    with pytest.warns(HardwareWarning, match="only zero"):
        sensor_checks.test_iio_read_channels("ambientlight", None, lambda *args: None)


def test_microsd_host_uses_firmware_association_not_mmc_index(tmp_path, monkeypatch):
    host = tmp_path / "mmc87"
    host.mkdir()
    monkeypatch.setattr(checks, "Path", lambda value: tmp_path if str(value) == "/sys/class/mmc_host" else Path(value))
    monkeypatch.setattr(checks, "identity", lambda path: {"firmware_nodes": ["/acpi/80860F14:02"], "drivers": {"/controller": "sdhci-acpi"}})
    checks.test_microsd_host_support(lambda *args: None)
    monkeypatch.setattr(checks, "identity", lambda path: {"firmware_nodes": ["/acpi/80860F14:01"], "drivers": {"/controller": "sdhci-acpi"}})
    with pytest.raises(AssertionError, match="host missing"):
        checks.test_microsd_host_support(lambda *args: None)


def test_external_audio_or_monitor_nodes_do_not_accept_onboard_audio():
    from checks.yogabook import test_desktop as desktop

    unrelated = [{"media.class": "Audio/Sink", "node.name": "alsa_output.usb-card", "api.alsa.path": "hw:USB"},
                 {"media.class": "Audio/Source", "node.name": "speaker.monitor"}]
    with pytest.raises(AssertionError, match="chtyogabook"):
        desktop.test_audio_desktop_sink(unrelated)
    with pytest.raises(AssertionError, match="chtyogabook"):
        desktop.test_audio_desktop_microphone(unrelated)
    desktop.test_audio_desktop_sink([{"media.class": "Audio/Sink", "api.alsa.path": "hw:chtyogabook"}])
    desktop.test_audio_desktop_microphone([{"media.class": "Audio/Source", "api.alsa.path": "hw:chtyogabook"}])


def test_failed_desktop_software_is_reported_not_reset_or_called_hardware_failure(monkeypatch):
    from checks.yogabook import test_desktop as desktop

    calls = []
    def query(shell, session, *argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, "localsearch-3.service loaded failed failed Tracker indexer\n", "")
    monkeypatch.setattr(desktop, "session_bus_call", query)
    evidence = {}
    with pytest.warns(HardwareWarning, match="software health"):
        desktop.test_desktop_failed_units(None, lambda name: name, {},
                                          lambda key, value: evidence.update({key: value}))
    assert "localsearch-3" in evidence["failed_user_units"]
    assert calls == [("systemctl", "--user", "--failed", "--no-legend", "--plain", "--no-pager")]
