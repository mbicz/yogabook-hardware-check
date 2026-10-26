"""Input binding and non-consuming evdev queries; no synthetic events or grabs."""
import json
from pathlib import Path
import sys

import pytest

from checks.yogabook.platform import bitmap_has, identity, text

KINDS = ("halo", "touchscreen", "pen", "keyboard", "touchpad", "tablet_switch", "lid",
         "volume_buttons", "power_button", "headset")


def input_inventory():
    result = []
    for path in sorted(Path("/sys/class/input").glob("event*")):
        device = path / "device"
        result.append({**identity(device), "node": str(Path("/dev/input") / path.name),
                       "name": text(device / "name"), "phys": text(device / "phys"),
                       "vendor": text(device / "id/vendor"), "product": text(device / "id/product"),
                       "ev": text(device / "capabilities/ev"), "sw": text(device / "capabilities/sw"),
                       "key": text(device / "capabilities/key")})
    return result


def matches(item, kind):
    name = item["name"].lower()
    driver_names = set(item["drivers"].values())
    if kind == "halo":
        return "goodix" in name
    if kind == "touchscreen":
        return "hideep" in name
    if kind == "pen":
        return "wacom" in name and item["vendor"].lower() == "056a" and item["product"].lower() == "0169"
    if kind == "volume_buttons":
        return ("gpio-keys" in driver_names and bitmap_has(item["ev"], 1)
                and all(bitmap_has(item["key"], key) for key in (114, 115)))
    if kind == "power_button":
        return ("/PNP0C0C:" in item["resolved"] and bitmap_has(item["ev"], 1)
                and bitmap_has(item["key"], 116))
    if kind == "headset":
        return (any(token in name for token in ("headset", "headphone", "jack"))
                and "cht" in name and bitmap_has(item["ev"], 5)
                and bitmap_has(item["sw"], 2))
    if kind == "tablet_switch":
        # lenovo-yogabook reports the back-side hall sensor (keyboard half folded
        # behind the screen) as SW_TABLET_MODE.
        return (name == "lenovo yoga book tablet mode switch" and bitmap_has(item["ev"], 5)
                and bitmap_has(item["sw"], 1))
    if kind in ("keyboard", "touchpad"):
        expected = "virtual-" + kind
        return name == expected and "/virtual/input/" in item["resolved"]
    return "gpio-keys" in driver_names and bitmap_has(item["ev"], 5) and bitmap_has(item["sw"], 0)


def selected(kind, evidence):
    inventory = input_inventory()
    evidence("input_inventory", inventory)
    found = [item for item in inventory if matches(item, kind)]
    assert found, f"required Yoga Book {kind} input device is missing"
    return found


def cases(layer):
    return [pytest.param(kind, id=kind, marks=pytest.mark.hardware(
        id=f"input.{kind}.{layer}", component=f"input.{kind}", layer=layer)) for kind in KINDS]


@pytest.mark.parametrize("kind", cases("enumeration"))
def test_input_bound(kind, evidence):
    for item in selected(kind, evidence):
        if kind in ("keyboard", "touchpad"):
            assert bitmap_has(item["ev"], 1 if kind == "keyboard" else 3), item
        else:
            assert item["drivers"], f"required physical input has no bound ancestor: {item}"


IOCTL_QUERY = '''import array, fcntl, json, os, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
except PermissionError:
    print('blocked: permission to open ' + sys.argv[1]); sys.exit(78)
try:
    version = array.array('i', [0])
    fcntl.ioctl(fd, 0x80044501, version, True)
    result = {'evdev_version': version[0]}
    if sys.argv[2] == 'switch':
        switches = bytearray(8)
        fcntl.ioctl(fd, 0x8008451b, switches, True)
        result['switch_bits'] = int.from_bytes(switches, sys.byteorder)
    print(json.dumps(result))
finally:
    os.close(fd)
'''


@pytest.mark.parametrize("kind", cases("headless"))
def test_input_queries(kind, shell, evidence):
    results = []
    for item in selected(kind, evidence):
        operation = "switch" if kind in ("lid", "tablet_switch", "headset") else "version"
        result = shell(sys.executable, "-c", IOCTL_QUERY, item["node"], operation, timeout=5)
        if result.returncode == 78:
            pytest.skip(result.stdout.strip())
        assert result.returncode == 0, result.stderr
        query = json.loads(result.stdout)
        assert query["evdev_version"] > 0, query
        results.append({"device": item, "query": query})
        evidence("input_queries", results)
    evidence("limitations", "Opened evdev and queried version/current switch state without consuming events. No touch, pen direction/calibration, key layout, pointer motion, physical lid transition or compositor behavior proven.")
