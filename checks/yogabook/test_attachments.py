"""Read-only USB roles, optional removable media and HDMI attachment evidence."""
from pathlib import Path
import warnings

import pytest

from checks.yogabook.attachments import hdmi_connectors, sd_cards, usb_controllers, usb_peripherals
from checks.yogabook.platform import attribute, drivers, identity, text
from ybcheck.plugin import HardwareWarning


@pytest.mark.hardware(id="usb.device.controller_support", component="usb.device", layer="enumeration")
def test_usb_device_support(evidence):
    controllers = usb_controllers()
    device = [item for item in controllers if item["function"] == "device"]
    dwc3 = [identity(path) for path in Path("/sys/bus/platform/devices").glob("*")
            if "dwc3" in path.name.lower() or any("dwc3" in name for name in drivers(path).values())]
    udcs = [identity(path) for path in Path("/sys/class/udc").glob("*")]
    evidence("usb_device_support", {"pci_functions": controllers, "xdci": device, "dwc3": dwc3, "udc": udcs})
    evidence("limitations", "Device-mode support is independent of xHCI host operation. No gadget is configured, bound, connected or detached; UDC enumeration is not host-to-gadget transfer acceptance.")
    if not device and not dwc3:
        evidence("usb_device_controller_exposure", "No xDCI/DWC3 exposed this boot; neutral/no-cable state alone is not a missing-driver diagnosis")
    if device and not any(item["drivers"] for item in device):
        warnings.warn("xDCI PCI function present but unbound; device/OTG support gap", HardwareWarning)
    if not udcs:
        warnings.warn("No USB device controller registered in /sys/class/udc; gadget/device-mode acceptance is unavailable in this boot, regardless of compiled support. xHCI root hubs do not prove it; do not infer a driver failure merely from role=none with no cable.", HardwareWarning)


@pytest.mark.hardware(id="usb.otg.role_read", component="usb.otg", layer="headless")
def test_usb_role_read(shell, evidence):
    records = []
    for role in sorted(Path("/sys/class/usb_role").glob("*")):
        value = attribute(shell, role / "role")
        records.append({"kind": "role-switch", **identity(role), "role": value})
        assert value in {"none", "host", "device"}, records
    for extcon in sorted(Path("/sys/class/extcon").glob("*")):
        values = {"name": text(extcon / "name")}
        if (extcon / "state").exists():
            # Empty state is valid when no connectors are asserted.
            result = shell("cat", str(extcon / "state"), timeout=5)
            if result.returncode and "Permission denied" in result.stderr:
                pytest.skip(f"blocked: cannot read {extcon}/state")
            assert result.returncode == 0, result.stderr
            values["state"] = result.stdout.strip()
        values["cables"] = [{"name": text(cable / "name"), "state": attribute(shell, cable / "state")}
                            for cable in sorted(extcon.glob("cable.*")) if (cable / "state").exists()]
        records.append({"kind": "extcon", **identity(extcon), **values})
    for udc in sorted(Path("/sys/class/udc").glob("*")):
        values = {name: attribute(shell, udc / name) for name in ("state", "current_speed", "maximum_speed")
                  if (udc / name).exists()}
        records.append({"kind": "udc", **identity(udc), **values})
    evidence("usb_role_telemetry", records)
    evidence("limitations", "Current role/extcon/UDC state only; charger VBUS detection is not proof of a working data role or ID-pin transition. No role, VBUS, authorization, driver, or configfs writes.")
    usb_cables = [cable for item in records if item["kind"] == "extcon" for cable in item["cables"]
                  if cable["name"].upper() in {"USB", "USB-HOST"}]
    if not any(item["kind"] == "role-switch" for item in records) and not usb_cables:
        warnings.warn("No USB role-switch or USB/USB-HOST extcon telemetry; automatic OTG role selection is not established", HardwareWarning)


@pytest.mark.hardware(id="usb.peripheral.attached_read", component="usb.host", layer="headless")
def test_usb_attached_read(shell, evidence):
    devices = usb_peripherals()
    evidence("usb_attached_devices", devices)
    if not devices:
        pytest.skip("attachment: no non-hub USB peripheral enumerated; attach known-good OTG data adapter/peripheral")
    reads = []
    for item in devices:
        path = Path(item["path"])
        values = {name: attribute(shell, path / name) for name in ("authorized", "speed", "bConfigurationValue")}
        interfaces = [{**identity(interface), "class": text(interface / "bInterfaceClass")}
                      for interface in sorted(path.glob(path.name + ":*"))]
        reads.append({"device": item, "values": values, "interfaces": interfaces})
        evidence("usb_peripheral_reads", reads)
        assert float(values["speed"]) > 0, values
        if values["authorized"] != "1" or values["bConfigurationValue"] == "0":
            warnings.warn(f"USB peripheral {path.name} is not authorized/configured; no policy changed", HardwareWarning)
        if interfaces and not any(interface["drivers"] for interface in interfaces):
            warnings.warn(f"USB peripheral {path.name} has no bound interface driver; inspect whether userspace owns it", HardwareWarning)
    evidence("limitations", "Enumerated non-hub devices may be internal. No traffic or physical external-port identity inferred; normal operation, actual unplug/replug and both roles remain manual checks.")


@pytest.mark.hardware(id="storage.microsd.attached_read", component="storage.microsd", layer="headless")
def test_microsd_read(shell, evidence):
    cards = sd_cards()
    evidence("microsd_cards", [{**identity(path), "name": text(path / "name")} for path in cards])
    if not cards:
        pytest.skip("attachment: no SD card detected; insert a known-good microSD card (no mounting or writes are performed)")
    records = []
    for card in cards:
        assert drivers(card), f"SD card not bound: {card}"
        disks = [path for path in Path("/sys/class/block").glob("*") if not (path / "partition").exists()
                 and card.resolve() in path.resolve().parents]
        assert disks, f"SD card has no block endpoint: {card}"
        for disk in disks:
            values = {name: attribute(shell, disk / name) for name in ("size", "ro", "stat")}
            records.append({"card": identity(card), "disk": identity(disk), **values})
            evidence("microsd_readback", records)
            assert int(values["size"]) > 0 and values["ro"] in {"0", "1"}, values
            assert len(values["stat"].split()) >= 11 and all(int(value) >= 0 for value in values["stat"].split()), values
    evidence("limitations", "Card identity and geometry/stat readback only. No mount, filesystem access, block read, write, eject or media-integrity claim.")


@pytest.mark.hardware(id="storage.microsd.host_support", component="storage.microsd", layer="enumeration")
def test_microsd_host_support(evidence):
    hosts = [identity(path) for path in sorted(Path("/sys/class/mmc_host").glob("*"))]
    evidence("mmc_hosts", hosts)
    # Android-edition board firmware associates 80860F14:02 with the SD slot;
    # :00 is eMMC and :01 is unused non-removable SDIO. Never assume mmcN.
    slots = [item for item in hosts if any(Path(node).name == "80860F14:02" for node in item["firmware_nodes"])]
    assert slots, "microSD slot's firmware-associated MMC host missing (80860F14:02); an absent card is a separate attachment check"
    assert all("sdhci-acpi" in item["drivers"].values() for item in slots), f"microSD host not bound to sdhci-acpi: {slots}"
    evidence("limitations", "Bound SD-slot host, not card detection or I/O. The unused non-removable SDIO controller failing initialization is not evidence that microSD failed.")


@pytest.mark.hardware(id="display.hdmi.enumeration", component="display.hdmi", layer="enumeration")
def test_hdmi_support(evidence):
    connectors = hdmi_connectors()
    evidence("hdmi_connectors", [identity(path) for path in connectors])
    assert connectors, "required HDMI DRM connector missing"
    assert any("i915" in drivers(path).values() for path in connectors), "HDMI connector not bound to i915"


@pytest.mark.hardware(id="display.hdmi.attached_read", component="display.hdmi", layer="headless")
def test_hdmi_attached(shell, evidence):
    connectors = hdmi_connectors()
    assert connectors, "HDMI connector missing; see display.hdmi.enumeration"
    reads = [{"identity": identity(path), "status": attribute(shell, path / "status")} for path in connectors]
    evidence("hdmi_status", reads)
    assert all(item["status"] in {"connected", "disconnected", "unknown"} for item in reads), reads
    connected = [path for path, item in zip(connectors, reads) if item["status"] == "connected"]
    if not connected:
        pytest.skip("attachment: no HDMI display connected; no modeset or hotplug forced")
    for path in connected:
        evidence("hdmi_" + path.name, {"modes": attribute(shell, path / "modes"),
                                      "enabled": text(path / "enabled")})
    evidence("limitations", "Connector status and advertised modes only; not visible scanout, correct EDID contents, hotplug recovery or HDMI audio.")


@pytest.mark.hardware(id="audio.hdmi.enumeration", component="audio.hdmi", layer="enumeration")
def test_hdmi_audio_support(shell, evidence):
    cards = attribute(shell, Path("/proc/asound/cards"))
    evidence("alsa_cards", cards)
    assert "HdmiLpeAudio" in cards, "onboard HDMI LPE ALSA card missing (internal RT5677 or external USB audio is independent)"
    pcm = attribute(shell, Path("/proc/asound/pcm"))
    evidence("alsa_pcm_registry", pcm)
    assert any("HdmiLpeAudio" in line and "playback" in line for line in pcm.splitlines()), "HDMI LPE card has no playback PCM endpoint"
    elds = []
    for path in sorted(Path("/proc/asound").glob("card*/eld*")):
        elds.append({"path": str(path), "values": attribute(shell, path)})
    evidence("hdmi_eld", elds)
    evidence("limitations", "HDMI ALSA enumeration and existing ELD only. No receiver attachment, PCM playback, desktop routing or audible acceptance implied.")
