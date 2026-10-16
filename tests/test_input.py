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
])
def test_physical_control_identity_requires_correct_capability_and_ancestry(kind, device, expected):
    assert matches(device, kind) is expected
