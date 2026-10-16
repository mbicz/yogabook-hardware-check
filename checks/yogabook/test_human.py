"""Physical/userspace acceptance is explicit and never gates low-level checks."""

import pytest


OBSERVATIONS = [
    ('display.image', 'display', 'Confirm the internal panel has a stable image, correct rotation, no corruption and usable brightness.'),
    ('display.external', 'display.external', 'Attach HDMI display; confirm external image and hotplug. Leave pending when no display is available.'),
    ('camera.front.image', 'camera.front', 'In a lit scene, view front-camera preview in an actual application; check orientation, detail, natural colors and exposure convergence.'),
    ('camera.rear.image', 'camera.rear', 'In a lit scene, view rear-camera preview in an actual application; check orientation, detail, natural colors and exposure convergence.'),
    ('audio.speaker.audible', 'audio.speaker', 'Confirm left/right speaker sound is audible, correctly routed and free of distortion at a safe volume.'),
    ('audio.headphones.audible', 'audio.headphones', 'Insert headphones; confirm jack detection, L/R playback and speaker muting, then unplug and confirm speaker recovery.'),
    ('audio.microphone.intelligible', 'audio.microphone', 'Record speech with the internal microphone in a normal application and listen to the recording; confirm intelligibility and routing.'),
    ('audio.headset.routing', 'audio.headset', 'Attach a supported headset and confirm its microphone and earphones are correctly selected, then verify unplug recovery.'),
    ('audio.hdmi.audible', 'audio.hdmi', 'With an HDMI receiver attached, select HDMI audio and confirm audible playback.'),
    ('input.touchscreen.gestures', 'input.touchscreen', 'Touch screen corners and use multitouch gestures; verify mapping after display rotation.'),
    ('input.pen.mapping', 'input.pen', 'With the Create Pad pen, test all corners, hover, pressure and strokes after each display rotation; no 90-degree offset.'),
    ('input.keyboard.typing', 'input.keyboard', 'Type every Halo key including Enter/backslash and switch keyboard/pen modes; confirm the handler recovers.'),
    ('input.touchpad.pointer', 'input.touchpad', 'Move, tap, click and scroll on the Halo touchpad for over 40 seconds and after a mode switch; verify no tablet-switch-induced freeze.'),
    ('input.buttons.actions', 'input.buttons', 'Press volume buttons and verify their desktop actions. Check power-button policy only with recovery access and saved work.'),
    ('input.lid.policy', 'input.lid', 'With physical recovery available and saved work, close/open the lid and confirm intended sleep/wake behavior. Never triggered by the automatic suite.'),
    ('haptics.left.felt', 'haptics.left', 'During normal Halo typing confirm the left actuator can be felt, independently of the right.'),
    ('haptics.right.felt', 'haptics.right', 'During normal Halo typing confirm the right actuator can be felt, independently of the left.'),
    ('backlight.panel.visible', 'backlight.panel', 'Adjust display brightness through the desktop; confirm visible dimming/brightening and persistence.'),
    ('backlight.keyboard.visible', 'backlight.keyboard', 'Confirm Halo keyboard illumination follows mode and brightness controls; an off keyboard in pen mode is not itself a fault.'),
    ('leds.visible', 'leds', 'Confirm charging/notification indicators physically illuminate in their intended conditions; sysfs readback alone cannot prove light output.'),
    ('sensors.als.response', 'sensors.als', 'Illuminate and cover each light sensor while observing its raw/scaled channels; verify a meaningful response. Readable constant values do not establish responsiveness.'),
    ('sensors.motion.response', 'sensors.motion', 'Move/rotate both halves while watching accelerometer, gyro and magnetic channels; verify both physical sensors respond.'),
    ('sensors.hinge.response', 'sensors.hinge', 'Move the hinge through normal laptop/tablet positions while observing labeled hinge/inclination/rotation channels; verify meaningful changes.'),
    ('sensors.desktop.rotation', 'sensors', 'In a logged-in desktop, rotate the screen and change posture; confirm automatic rotation and keyboard policy without breaking the touchpad.'),
    ('gps.outdoor.position', 'gps', 'Outdoors with sky visibility, obtain a checksum-valid live position and compare it with a known location; indoor no-fix does not invalidate receiver NMEA operation.'),
    ('network.wifi.traffic', 'network.wifi', 'Use normal applications over WiFi and confirm sustained transfer and reconnect behavior; no disconnect is forced automatically.'),
    ('network.bluetooth.peripheral', 'network.bluetooth', 'Pair/connect a real Bluetooth peripheral and verify its intended input/audio/data behavior.'),
    ('power.charging.transition', 'power.charger', 'Connect/disconnect the charger and verify power-source/status transitions and actual charging; avoid deep discharge.'),
    ('usb.attached.device', 'usb', 'Attach a known working USB/OTG device and verify enumeration and its normal operation, then safe removal.'),
    ('storage.microsd.io', 'storage.microsd', 'Insert a known microSD card and verify a disposable file write/read without touching existing data, then safely eject.'),
    ('power.suspend.resume', 'power.sleep', 'With saved work and physical recovery available, suspend/resume and rerun automatic checks; verify display, input, radios and audio recover.'),
    ('power.shutdown.reboot', 'power.boot', 'With physical recovery available, verify complete power-off and a subsequent normal boot. Automatic checks never issue power commands.'),
]


@pytest.mark.parametrize('human_case', [
    pytest.param(check_id, id=check_id, marks=pytest.mark.hardware(
        id='human.' + check_id, component=component, layer='human', instructions=instructions,
    )) for check_id, component, instructions in OBSERVATIONS
])
def test_physical_acceptance(human_case, human):
    assert human, human_case
