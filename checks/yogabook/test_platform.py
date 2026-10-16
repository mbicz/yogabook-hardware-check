"""Safe platform enumeration and bounded readback, without power/state changes."""
import json
from pathlib import Path
import re
import sys
import warnings

import pytest

from checks.yogabook.platform import attribute, bitmap_has, block_leaves, drivers, identity, root_mount, text
from ybcheck.plugin import HardwareWarning


def haptics(evidence):
    found = [path for path in sorted(Path("/sys/bus/i2c/devices").glob("*"))
             if "drv260" in text(path / "name").lower() or "drv260" in path.name.lower()]
    records = [{**identity(path), "name": text(path / "name")} for path in found]
    evidence("haptic_controllers", records)
    assert len({path.resolve() for path in found}) >= 2, "both onboard DRV2604L controllers are required"
    for path in found:
        assert any("drv260" in value for value in drivers(path).values()), f"unbound DRV260x controller {path}"
    return found


@pytest.mark.hardware(id="haptics.controllers.enumeration", component="haptics", layer="enumeration")
def test_haptics_bound(evidence):
    haptics(evidence)


@pytest.mark.hardware(id="haptics.controllers.readback", component="haptics", layer="headless")
def test_haptics_readback(shell, evidence):
    controllers = haptics(evidence)
    queries = []
    for controller in controllers:
        events = [path for path in Path("/sys/class/input").glob("event*")
                  if controller.resolve() in (path / "device").resolve().parents]
        assert events, f"bound controller has no input endpoint: {controller}"
        for event in events:
            caps = attribute(shell, event / "device/capabilities/ev")
            assert bitmap_has(caps, 21), f"missing EV_FF for {controller}"
            result = shell(sys.executable, "-c", '''import array, fcntl, json, os, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
except PermissionError:
    print('blocked: permission to open ' + sys.argv[1]); sys.exit(78)
try:
    count = array.array('i', [0])
    fcntl.ioctl(fd, 0x80044584, count, True)
    print(json.dumps({'effect_slots': count[0]}))
finally:
    os.close(fd)
''', str(Path("/dev/input") / event.name), timeout=5)
            if result.returncode == 78:
                pytest.skip(result.stdout.strip())
            assert result.returncode == 0, result.stderr
            data = json.loads(result.stdout)
            assert data["effect_slots"] > 0, data
            queries.append({"controller": str(controller), "event": event.name, **data})
            evidence("haptic_queries", queries)
    evidence("limitations", "EVIOCGEFFECTS read only. No effect uploaded or played; successful controller probe/force-feedback API does not prove tactile output, left/right location or power-cycle recovery.")


LIGHTS = ("lcd", "halo", "edge", "pen_icon")


def light_devices(kind, evidence):
    root = Path("/sys/class/backlight" if kind == "lcd" else "/sys/class/leds")
    if kind == "lcd":
        found = [path for path in root.glob("*") if "lp855x" in drivers(path).values()]
    else:
        names = {"halo": "yogabook::kbd_backlight", "edge": "platform::charging", "pen_icon": "platform::indicator"}
        found = [path for path in root.glob("*") if path.name == names[kind]]
    evidence("light_devices", [identity(path) for path in found])
    assert found, f"required {kind} light endpoint missing or unbound"
    for path in found:
        assert drivers(path), f"unbound {kind} light: {path}"
    return found


def light_cases(layer):
    return [pytest.param(kind, id=kind, marks=pytest.mark.hardware(
        id=f"lights.{kind}.{layer}", component=f"lights.{kind}", layer=layer)) for kind in LIGHTS]


@pytest.mark.parametrize("kind", light_cases("enumeration"))
def test_light_bound(kind, evidence):
    light_devices(kind, evidence)


@pytest.mark.parametrize("kind", light_cases("headless"))
def test_light_readback(kind, shell, evidence):
    records = []
    for path in light_devices(kind, evidence):
        values = {name: attribute(shell, path / name) for name in ("brightness", "max_brightness")}
        assert 0 <= int(values["brightness"]) <= int(values["max_brightness"]), values
        assert int(values["max_brightness"]) > 0, values
        for name in ("actual_brightness", "bl_power", "trigger"):
            if (path / name).exists():
                values[name] = attribute(shell, path / name)
        if "actual_brightness" in values:
            assert 0 <= int(values["actual_brightness"]) <= int(values["max_brightness"]), values
        records.append({"identity": identity(path), "values": values})
        evidence("light_readback", records)
    evidence("limitations", "Readback only; zero brightness can be intentional. No write or optical output proven. platform::charging is the edge LED; platform::indicator is the Halo pen icon, not the edge status LED.")


def supplies(kind, evidence):
    paths = sorted(Path("/sys/class/power_supply").glob("*"))
    inventory = [{**identity(path), "type": text(path / "type"), "model": text(path / "model_name")} for path in paths]
    evidence("power_supplies", inventory)
    if kind == "battery":
        found = [path for path in paths if "bq27542" in path.name.lower() and text(path / "type") == "Battery"]
    else:
        found = [path for path in paths if text(path / "model_name").upper() == "BQ25892"]
    assert found, f"required {'bq27542 battery' if kind == 'battery' else 'BQ25892 charger'} missing"
    for path in found:
        assert drivers(path), f"unbound power supply {path}"
    return found


def supply_cases(layer):
    return [pytest.param(kind, id=kind, marks=pytest.mark.hardware(
        id=f"power.{kind}.{layer}", component=f"power.{kind}", layer=layer)) for kind in ("battery", "charger")]


@pytest.mark.parametrize("kind", supply_cases("enumeration"))
def test_supply_bound(kind, evidence):
    supplies(kind, evidence)


@pytest.mark.parametrize("kind", supply_cases("headless"))
def test_supply_status(kind, shell, evidence):
    records = []
    for path in supplies(kind, evidence):
        required = ("status", "present", "capacity", "voltage_now", "current_now") if kind == "battery" else ("status", "online")
        values = {name: attribute(shell, path / name) for name in required}
        assert values["status"] in {"Charging", "Discharging", "Full", "Not charging"}, values
        if kind == "battery":
            assert values["present"] == "1", values
            assert 0 <= int(values["capacity"]) <= 100 and int(values["voltage_now"]) > 0, values
            int(values["current_now"])
        else:
            assert values["online"] in {"0", "1"}, values
        for name in ("health", "temp", "charge_type", "constant_charge_current", "input_current_limit", "voltage_now", "current_now"):
            if name not in values and (path / name).exists():
                values[name] = attribute(shell, path / name)
        records.append({"identity": identity(path), "values": values})
        evidence("power_status", records)
        if values.get("health", "Good") not in {"Good", "Unknown"}:
            warnings.warn(f"{path.name} reports health {values['health']}", HardwareWarning)
    evidence("limitations", "Present status/telemetry only; no discharge cycle, charging negotiation, battery capacity calibration or unplugged runtime proven. Disconnected charger is allowed.")


@pytest.mark.hardware(id="thermal.zones.enumeration", component="thermal", layer="enumeration")
def test_thermal_enumeration(evidence):
    zones = sorted(Path("/sys/class/thermal").glob("thermal_zone*"))
    evidence("thermal_zones", [{**identity(path), "type": text(path / "type")} for path in zones])
    assert zones, "required thermal monitoring zones missing"
    assert any("soc_dts" in text(path / "type") for path in zones), "Cherry Trail SoC thermal zones missing"


@pytest.mark.hardware(id="thermal.temperature_read", component="thermal", layer="headless")
def test_thermal_read(shell, evidence):
    zones = sorted(Path("/sys/class/thermal").glob("thermal_zone*"))
    assert zones, "no thermal zones"
    records = []
    for path in zones:
        temperature = int(attribute(shell, path / "temp"))
        records.append({"path": str(path), "type": text(path / "type"), "millidegrees_c": temperature})
        evidence("temperatures", records)
        assert -40000 <= temperature <= 150000, records[-1]
        if temperature >= 95000:
            warnings.warn(f"{path.name} temperature {temperature / 1000:g} C", HardwareWarning)
    evidence("limitations", "Current thermal samples, not load/cooling effectiveness or trip-action validation.")




def panels(evidence):
    paths = [path for path in Path("/sys/class/drm").glob("card*-*") if re.search(r"-(DSI|eDP|LVDS)-", path.name)]
    evidence("internal_panels", [identity(path) for path in paths])
    found = [path for path in paths if "i915" in drivers(path).values()]
    assert found, "required internal i915 DRM panel connector missing/unbound"
    return found


@pytest.mark.hardware(id="display.panel.enumeration", component="display.panel", layer="enumeration")
def test_panel_bound(evidence):
    panels(evidence)


@pytest.mark.hardware(id="display.panel.connected_read", component="display.panel", layer="headless")
def test_panel_status(shell, evidence):
    records = []
    for path in panels(evidence):
        values = {name: attribute(shell, path / name) for name in ("status", "modes")}
        assert values["status"] == "connected", values
        for name in ("enabled", "dpms"):
            if (path / name).exists():
                values[name] = attribute(shell, path / name)
        records.append({"identity": identity(path), **values})
    evidence("panel_status", records)
    evidence("limitations", "Internal connector connection/modes read; no image, backlight visibility, tearing or external HDMI attachment proven.")


def mmc_cards(evidence):
    cards = [path for path in Path("/sys/bus/mmc/devices").glob("*") if text(path / "type") in ("MMC", "SD")]
    evidence("mmc_cards", [{**identity(path), "type": text(path / "type"), "name": text(path / "name")} for path in cards])
    assert any(text(path / "type") == "MMC" for path in cards), "required internal eMMC card missing"
    for path in cards:
        assert drivers(path), f"unbound MMC/SD card {path}"
    return cards


@pytest.mark.hardware(id="storage.mmc.enumeration", component="storage", layer="enumeration")
def test_mmc_enumeration(evidence):
    mmc_cards(evidence)


@pytest.mark.hardware(id="storage.root_and_mmc.read", component="storage", layer="headless")
def test_storage_read(shell, evidence):
    cards = mmc_cards(evidence)
    mount = root_mount(attribute(shell, Path("/proc/self/mountinfo")))
    evidence("mounted_root", mount)
    assert "rw" in mount["options"], f"root is not mounted read-write: {mount}"
    block = Path("/sys/dev/block") / mount["device"]
    assert block.exists(), f"root block identity not exported: {mount}"
    leaves = block_leaves(block)
    evidence("root_block_leaves", [str(path) for path in leaves])
    assert any(card.resolve() in (leaf, *leaf.parents) for card in cards if text(card / "type") == "MMC" for leaf in leaves), "mounted root does not resolve to internal eMMC"
    disks = [path for path in Path("/sys/class/block").glob("*")
             if not (path / "partition").exists() and any(card.resolve() in path.resolve().parents for card in cards)]
    assert disks, "MMC cards have no whole-disk block endpoints"
    records = []
    for path in disks:
        values = {name: attribute(shell, path / name) for name in ("size", "ro", "stat")}
        assert int(values["size"]) > 0 and values["ro"] in {"0", "1"}, values
        stats = [int(value) for value in values["stat"].split()]
        assert len(stats) >= 11 and all(value >= 0 for value in stats), values
        for name in ("device/state", "device/ioerr_cnt", "device/timeout_count"):
            if (path / name).exists():
                values[name] = attribute(shell, path / name)
                if name.endswith("state"):
                    assert values[name].lower() not in {"dead", "offline", "error"}, values
                elif int(values[name]) > 0:
                    warnings.warn(f"{path.name} {name}={values[name]} (cumulative)", HardwareWarning)
        records.append({"identity": identity(path), "values": values})
    evidence("mmc_readback", records)
    evidence("limitations", "Mounted root backing chain, block geometry, I/O statistics and exposed error state only. No disk writes or surface/media integrity test; block stat does not contain an I/O-error count. Unexported error counters are not assumed zero. External SD is optional.")


def usb_hosts(evidence):
    hosts = [path for path in Path("/sys/bus/pci/devices").glob("*") if text(path / "class").startswith("0x0c03")]
    evidence("usb_hosts", [identity(path) for path in hosts])
    assert hosts, "required PCI USB host controller missing"
    for path in hosts:
        assert (path / "driver").is_symlink(), f"unbound USB host {path}"
    return hosts


@pytest.mark.hardware(id="usb.host.enumeration", component="usb.host", layer="enumeration")
def test_usb_host_enumeration(evidence):
    usb_hosts(evidence)


@pytest.mark.hardware(id="usb.host.root_hub_read", component="usb.host", layer="headless")
def test_usb_root_hub_read(shell, evidence):
    records = []
    for host in usb_hosts(evidence):
        hubs = [path for path in Path("/sys/bus/usb/devices").glob("usb*") if host.resolve() in path.resolve().parents]
        assert hubs, f"USB controller has no registered root hub: {host}"
        for hub in hubs:
            values = {name: attribute(shell, hub / name) for name in ("authorized", "speed", "urbnum", "power/runtime_status")}
            assert values["authorized"] == "1" and float(values["speed"]) > 0 and int(values["urbnum"]) >= 0, values
            assert values["power/runtime_status"] in {"active", "suspended", "suspending", "resuming", "unsupported"}, values
            records.append({"host": identity(host), "hub": identity(hub), "values": values})
    evidence("usb_root_hub_reads", records)
    evidence("limitations", "Root hub attributes only; no external USB device or attachment required, port traffic/enumeration under load not proven.")
