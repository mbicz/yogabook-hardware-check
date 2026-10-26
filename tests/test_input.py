"""Physical controls cannot be satisfied by software-emulated keyboard keys."""
import pytest

from checks.yogabook.test_input import matches


@pytest.mark.parametrize("kind,device,expected", [
    ("volume_buttons", {"name": "gpio-keys", "drivers": {"/device": "gpio-keys"},
                        "resolved": "/devices/platform/ACPI0011:00/input/input11", "ev": "100003", "key": "c000000000000 0"}, True),
    ("volume_buttons", {"name": "gpio-keys", "drivers": {"/device": "gpio-keys"},
                        "resolved": "/devices/platform/gpio-keys.1.auto/input/input0", "ev": "21", "key": "0"}, False),
    ("volume_buttons", {"name": "virtual-keyboard", "drivers": {},
                        "resolved": "/devices/virtual/input/input38", "ev": "3", "key": "c000000000000 0"}, False),
    ("power_button", {"name": "Power Button", "drivers": {"/device": "button"},
                      "resolved": "/devices/platform/PNP0C0C:00/input/input10", "ev": "3", "key": "8000 10000000000000 0"}, True),
    ("power_button", {"name": "virtual-keyboard", "drivers": {},
                      "resolved": "/devices/virtual/input/input38", "ev": "3", "key": "8000 10000000000000 0"}, False),
    ("power_button", {"name": "Power Button", "drivers": {"/device": "button"},
                      "resolved": "/devices/platform/PNP0C0C:00/input/input10", "ev": "3", "key": "0"}, False),
    ("tablet_switch", {"name": "Lenovo Yoga Book Tablet Mode Switch",
                       "drivers": {"/device": "yogabook-touch-kbd-digitizer-switch"},
                       "resolved": "/devices/platform/yogabook-touch-kbd-digitizer-switch/input/input52",
                       "ev": "21", "sw": "2", "key": "0"}, True),
    # The former synthetic uinput switch claimed tablet mode in every pose.
    ("tablet_switch", {"name": "virtual-tablet-switch", "drivers": {},
                       "resolved": "/devices/virtual/input/input43", "ev": "21", "sw": "2", "key": "0"}, False),
])
def test_physical_control_identity_requires_correct_capability_and_ancestry(kind, device, expected):
    assert matches(device, kind) is expected


def test_headphone_detection_is_not_an_unrelated_input_switch():
    jack = {"name": "cht-yogabook Headset", "drivers": {"/device": "snd_soc_sst_cht_yogabook"},
            "ev": "21", "sw": "14"}
    assert matches(jack, "headset")
    assert not matches({**jack, "name": "USB Headphones"}, "headset")
    assert not matches({**jack, "sw": "1"}, "headset")
