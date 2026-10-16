import pytest

from checks.yogabook.pen import device_mapping, graphical_session, libwacom_devices

DATAFILE = """[Device]
Name=Wacom HID 169 Pen
DeviceMatch=i2c|056a|0169;
IntegratedIn=Display;System;

# Wacom HID 169 Pen
#  - /dev/input/event17
---------------------------------------------------------------
[Device]
Name=External Tablet
IntegratedIn=

# External Tablet
#  - /dev/input/event22
"""


def test_libwacom_integration_flags_are_attributed_to_their_own_node():
    devices = {item["nodes"][0]: item for item in libwacom_devices(DATAFILE)}
    assert devices["/dev/input/event17"]["integrated_in"] == ["Display", "System"]
    assert devices["/dev/input/event22"]["integrated_in"] == []


@pytest.mark.parametrize("reply", [
    "Error: GDBus.Error:...: Device is not mapped to any output",
    "((0, 0, 0, 1200),)",
    "((0, 0, 1920, 1200), (0, 0, 1, 1))",
])
def test_unmapped_or_degenerate_mapping_is_rejected(reply):
    with pytest.raises(ValueError):
        device_mapping(reply)


def test_mapped_panel_rectangle_is_parsed():
    assert device_mapping("((0, 0, 1920, 1200),)\n") == (0, 0, 1920, 1200)


def session(**fields):
    base = {"User": "1000", "Name": "mbicz", "Type": "wayland", "Class": "user", "Active": "yes", "Seat": "seat0"}
    base.update(fields)
    return "\n".join(f"{key}={value}" for key, value in base.items())


def test_only_active_seat_graphical_user_session_is_selected():
    blocks = [session(Type="tty", Name="ssh"), session(Class="greeter", Name="gdm"),
              session(Active="no", Name="old"), session()]
    assert graphical_session(blocks) == {"uid": 1000, "user": "mbicz", "type": "wayland"}
    assert graphical_session([session(Seat="")]) is None


def test_two_active_graphical_sessions_are_ambiguous():
    with pytest.raises(ValueError, match="ambiguous"):
        graphical_session([session(), session(User="1001", Name="other")])
