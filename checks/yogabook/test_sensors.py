"""Read every discovered IIO instance; identity is not a stimulus-response test."""
from pathlib import Path
import time

import pytest

from checks.yogabook.platform import SENSOR_NAMES, attribute, numeric, sensors


def cases(layer):
    return [pytest.param(kind, id=kind, marks=pytest.mark.hardware(
        id=f"sensors.{kind}.{layer}", component=f"sensors.{kind}", layer=layer))
        for kind in SENSOR_NAMES]


def selected(kind, evidence):
    inventory = sensors()
    evidence("iio_inventory", inventory)
    found = [item for item in inventory if item["kind"] == kind]
    # The two HID sensor hubs expose one of each function. Do not assign base/lid
    # using boot-dependent HID suffixes, or invent extra accelerometer taxonomies.
    assert len(found) >= 2, f"expected both sensor-hub instances of {kind}; found {found}"
    return found


@pytest.mark.parametrize("kind", cases("enumeration"))
def test_iio_instances(kind, evidence):
    found = selected(kind, evidence)
    for item in found:
        assert item["drivers"], f"unbound IIO sensor: {item}"
        assert any(name.endswith(("_raw", "_input")) for name in item["channels"]), item
        if kind == "hinge":
            assert {"hinge", "screen", "keyboard"} <= set(item["labels"].values()), item
    evidence("limitations", "Both physical hubs found; base/lid mapping not inferred from node numbers or HID instance suffixes.")


@pytest.mark.parametrize("kind", cases("headless"))
def test_iio_read_channels(kind, shell, evidence):
    found = selected(kind, evidence)
    samples = []
    for item in found:
        path = Path(item["path"])
        channels = [name for name in item["channels"] if name.endswith(("_raw", "_input"))]
        assert channels, f"no readable sample channels: {item}"
        data = {"identity": item, "samples": [], "conversion": {}}
        for name in item["channels"]:
            if name.endswith(("_scale", "_offset")):
                value = attribute(shell, path / name)
                numeric(value)
                data["conversion"][name] = value
        for sample in range(2):
            values = {name: attribute(shell, path / name) for name in channels}
            for value in values.values():
                numeric(value)
            data["samples"].append(values)
            if sample == 0:
                time.sleep(0.1)
        samples.append(data)
        evidence("iio_samples", samples)
    evidence("limitations", "Two finite channel reads per instance only. No movement, orientation accuracy, mounting, freshness, or light responsiveness proven; constant ALS values are not a failure or proof of changing light.")
