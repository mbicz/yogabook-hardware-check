"""Controller/link queries only: never scan, connect, disconnect, or change rfkill."""
import json
from pathlib import Path
import re
import shutil
import warnings

import pytest

from checks.yogabook.platform import attribute, identity, text
from ybcheck.plugin import HardwareWarning


def wifi(evidence):
    inventory = [{**identity(path), "interface": path.name} for path in sorted(Path("/sys/class/net").glob("*"))
                 if (path / "wireless").exists() or (path / "phy80211").exists()]
    evidence("wireless_interfaces", inventory)
    found = [item for item in inventory if "brcmfmac" in item["drivers"].values()]
    assert found, "required BCM4356/brcmfmac wireless interface is missing or unbound"
    return found


@pytest.mark.hardware(id="network.wifi.enumeration", component="network.wifi", layer="enumeration")
def test_wifi_bound(evidence):
    wifi(evidence)


@pytest.mark.hardware(id="network.wifi.link_read", component="network.wifi", layer="headless")
def test_wifi_link_read(shell, require_program, evidence):
    found = wifi(evidence)
    ip = require_program("ip")
    records = []
    for item in found:
        path = Path(item["path"])
        result = shell(ip, "-j", "-s", "link", "show", "dev", item["interface"], timeout=5)
        assert result.returncode == 0, result.stderr
        links = json.loads(result.stdout)
        assert len(links) == 1 and links[0]["ifname"] == item["interface"], links
        data = {"identity": item, "link": links[0], "operstate": attribute(shell, path / "operstate"),
                "rx_bytes": attribute(shell, path / "statistics/rx_bytes"), "tx_bytes": attribute(shell, path / "statistics/tx_bytes")}
        assert int(data["rx_bytes"]) >= 0 and int(data["tx_bytes"]) >= 0, data
        records.append(data)
        evidence("wifi_link_reads", records)
        if "UP" not in links[0].get("flags", []):
            warnings.warn(f"{item['interface']} is administratively down; no state changed", HardwareWarning)
    evidence("limitations", "Kernel link state and counters queried; no packets generated, throughput, connectivity, RF sensitivity, or Internet service proven.")


@pytest.mark.hardware(id="network.wifi.association", component="network.wifi", layer="headless")
def test_wifi_optional_association(shell, require_program, evidence):
    found = wifi(evidence)
    iw = require_program("iw")
    records = []
    connected = False
    for item in found:
        result = shell(iw, "dev", item["interface"], "link", timeout=5)
        assert result.returncode == 0, result.stderr
        records.append({"interface": item["interface"], "link": result.stdout})
        connected |= bool(re.search(r"^Connected to [0-9a-f:]{17}", result.stdout, re.I | re.M))
    evidence("wifi_association", records)
    evidence("limitations", "Existing association only; no scan, association changes, paired devices or Internet requirement.")
    if not connected:
        assert all("Not connected" in item["link"] for item in records), records
        pytest.skip("attachment: no access point currently associated; association is optional")


def bluetooth(evidence):
    inventory = [{**identity(path), "name": path.name, "address": text(path / "address")}
                 for path in sorted(Path("/sys/class/bluetooth").glob("hci*"))
                 if re.fullmatch(r"hci\d+", path.name)]
    evidence("bluetooth_controllers", inventory)
    found = [item for item in inventory if "hci_uart_bcm" in item["drivers"].values()
             or "BCM2E1A" in (item["resolved"] + " ".join(item["firmware_nodes"])).upper()]
    assert found, "required onboard BCM4356/BCM2E1A UART Bluetooth controller is missing"
    for item in found:
        assert item["drivers"], f"Bluetooth controller has no bound ancestor: {item}"
        if item["address"]:
            assert re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", item["address"]), item
    return found


@pytest.mark.hardware(id="network.bluetooth.enumeration", component="network.bluetooth", layer="enumeration")
def test_bluetooth_bound(evidence):
    bluetooth(evidence)


@pytest.mark.hardware(id="network.bluetooth.controller_read", component="network.bluetooth", layer="headless")
def test_bluetooth_controller_read(shell, evidence):
    found = bluetooth(evidence)
    bluetoothctl = shutil.which("bluetoothctl")
    hciconfig = shutil.which("hciconfig")
    if not bluetoothctl and not hciconfig:
        pytest.skip("blocked: missing bluetoothctl or hciconfig for read-only controller query")
    results = []
    for item in found:
        if bluetoothctl and item["address"]:
            result = shell(bluetoothctl, "--timeout", "5", "show", item["address"], timeout=7)
            output = result.stdout + result.stderr
            if any(message in output for message in ("No default controller", "not available", "Failed to connect", "Waiting to connect", "org.bluez.Error.NotReady", "org.freedesktop.DBus.Error")):
                pytest.skip(f"blocked: Bluetooth management service unavailable: {output}")
            assert result.returncode == 0, output
            assert item["address"].lower() in result.stdout.lower(), result.stdout
            match = re.search(r"Powered:\s*(yes|no)", result.stdout)
            assert match, f"missing powered state: {result.stdout}"
            powered = match[1] == "yes"
        elif hciconfig:
            result = shell(hciconfig, item["name"], timeout=5)
            if result.returncode and "Operation not permitted" in result.stderr:
                pytest.skip("blocked: permission required for hciconfig controller query")
            assert result.returncode == 0, result.stderr
            assert re.search(rf"^{re.escape(item['name'])}:", result.stdout, re.M), result.stdout
            address = re.search(r"BD Address:\s*([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b", result.stdout)
            assert address, f"indexed HCI query returned no valid controller address: {result.stdout}"
            assert address[1] != "00:00:00:00:00:00", "HCI controller returned an uninitialized address"
            if item["address"]:
                assert address[1].lower() == item["address"].lower(), result.stdout
            powered = bool(re.search(r"\bUP\b", result.stdout))
        else:
            pytest.skip("blocked: kernel exposes no Bluetooth address attribute; install hciconfig (bluez) for an indexed HCI query")
        results.append({"identity": item, "powered": powered, "output": result.stdout})
        evidence("bluetooth_queries", results)
        if not powered:
            warnings.warn(f"Bluetooth controller {item['name']} is powered off; no state changed", HardwareWarning)
    evidence("limitations", "Controller management/powered-state query only; no discovery, pairing, traffic, or external device required.")
