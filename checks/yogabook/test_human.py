"""Physical/userspace acceptance is explicit and never gates low-level checks."""

import pytest


OBSERVATIONS = [
    ('display.image', 'display', 'Confirm the internal panel has a stable image, correct rotation, no corruption and usable brightness.'),
    ('display.external', 'display.external', 'Attach HDMI display; confirm external image and hotplug. Leave pending when no display is available.'),
    ('camera.front.image', 'camera.front', 'In a lit scene, view front-camera preview in an actual application; check orientation, detail, natural colors and exposure convergence.'),
    ('camera.rear.image', 'camera.rear', 'In a lit scene, view rear-camera preview in an actual application; check orientation, detail, natural colors and exposure convergence.'),
    ('camera.rear.ae.daylight', 'camera.rear', 'In the real rear-camera preview, alternate a dim indoor scene and a daylight scene containing bright sky and shaded detail. Wait for AE convergence each time; check the image never stays black/white, sky highlights do not acquire a pink cast or destroy all detail, shadows remain usable and exposure does not visibly pump. Record lighting and limitations; a 40-frame stream or indoor recovery alone does not accept daylight clipping behavior.'),
    ('camera.rear.autofocus', 'camera.rear', 'In an actual application with the rear OV8858 selected, focus first on a high-contrast near target and then a distant target; verify visible detail sharpens at both distances and focus recovers after camera switching. Front OV2740 is fixed-focus; do not require front autofocus.'),
    ('audio.speaker.audible', 'audio.speaker', 'Confirm left/right speaker sound is audible, correctly routed and free of distortion at a safe volume.'),
    ('audio.headphones.audible', 'audio.headphones', 'Insert headphones; confirm jack detection, L/R playback and speaker muting, then unplug and confirm speaker recovery.'),
    ('audio.microphone.intelligible', 'audio.microphone', 'Record speech with the internal microphone in a normal application and listen to the recording; confirm intelligibility and routing.'),
    ('audio.headset.routing', 'audio.headset', 'Attach a supported headset and confirm its microphone and earphones are correctly selected, then verify unplug recovery.'),
    ('audio.hdmi.audible', 'audio.hdmi', 'With an HDMI receiver attached, select HDMI audio and confirm audible playback.'),
    ('input.touchscreen.gestures', 'input.touchscreen', 'Touch screen corners and use multitouch gestures; verify mapping after display rotation.'),
    ('input.pen.mapping', 'input.pen', 'With the Create Pad pen, test all corners, hover, pressure and strokes after each display rotation; no 90-degree offset.'),
    ('input.keyboard.typing', 'input.keyboard', 'Type every Halo key including Enter/backslash and switch keyboard/pen modes; confirm the handler recovers.'),
    ('input.keyboard.fn', 'input.keyboard', 'Hold Fn and exercise F-row and navigation combinations, then slide off/release Fn and type ordinary keys. Verify no stuck Fn, extra keypresses or lost releases; repeat after keyboard/pen mode switching.'),
    ('input.touchpad.pointer', 'input.touchpad', 'Move, tap, click and scroll on the Halo touchpad for over 40 seconds and after a mode switch; verify no tablet-switch-induced freeze.'),
    ('input.buttons.actions', 'input.buttons', 'Press volume buttons and verify their desktop actions. Check power-button policy only with recovery access and saved work.'),
    ('input.lid.policy', 'input.lid', 'With physical recovery available and saved work, close/open the lid and confirm that the physical SW_LID switch—not hinge angles—drives the intended policy; verify a quick reopen cancels a pending sleep request. Never triggered by the automatic suite.'),
    ('haptics.left.felt', 'haptics.left', 'During normal Halo typing confirm the left actuator can be felt, independently of the right.'),
    ('haptics.right.felt', 'haptics.right', 'During normal Halo typing confirm the right actuator can be felt, independently of the left.'),
    ('backlight.panel.visible', 'backlight.panel', 'Adjust display brightness through the desktop; confirm visible dimming/brightening and persistence.'),
    ('backlight.keyboard.visible', 'backlight.keyboard', 'In keyboard mode press plain F10 repeatedly without Fn; verify exactly one keyboard-brightness step per press, visible off/dim/full cycling and wraparound, no autorepeat-induced skipping or desktop F10 side effect, and continued typing. Then verify Fn+F10 retains its intended key behavior and pen-mode illumination policy remains correct. An intentionally off Halo in pen mode is not itself a fault.'),
    ('leds.visible', 'leds', 'Confirm charging/notification indicators physically illuminate in their intended conditions; sysfs readback alone cannot prove light output.'),
    ('sensors.als.response', 'sensors.als', 'Illuminate and cover each light sensor while observing its raw/scaled channels; verify a meaningful response. Readable constant values do not establish responsiveness.'),
    ('sensors.motion.response', 'sensors.motion', 'Move/rotate both halves while watching accelerometer, gyro and magnetic channels; verify both physical sensors respond.'),
    ('sensors.hinge.response', 'sensors.hinge', 'Move through laptop, closed, 360-fold and open-flat positions while observing both labeled hinge instances; verify meaningful hinge/screen/keyboard changes and circular 359/0-degree wraparound. Do not infer physical SW_LID state or sleep policy from these angles.'),
    ('sensors.desktop.rotation', 'sensors', 'In a logged-in desktop, rotate the screen and change posture; confirm automatic rotation and keyboard policy without breaking the touchpad.'),
    ('gps.outdoor.position', 'gps', 'Outdoors with sky visibility, obtain a checksum-valid live position and compare it with a known location; indoor no-fix does not invalidate receiver NMEA operation.'),
    ('network.wifi.traffic', 'network.wifi', 'Use normal applications over WiFi and confirm sustained transfer and reconnect behavior; no disconnect is forced automatically.'),
    ('network.bluetooth.peripheral', 'network.bluetooth', 'Pair/connect a real Bluetooth peripheral and verify its intended input/audio/data behavior.'),
    ('power.charging.transition', 'power.charger', 'Connect/disconnect the charger and verify power-source/status transitions and actual charging; avoid deep discharge.'),
    ('power.battery.runtime', 'power.battery', 'On battery, record idle and representative-workload drain under documented brightness/radio conditions without deep discharge. AC-connected or fully charged telemetry is not an idle-power or battery-runtime measurement.'),
    ('usb.attached.device', 'usb.host', 'With a known-good data-capable micro-USB OTG adapter (not a charge-only cable), attach a low-power known-good USB peripheral. Verify host-role/extcon transition, new non-hub enumeration and real intended transfer/input. Safely remove it, physically unplug/replug and repeat. Record adapter/peripheral and observed role; xHCI root hubs alone do not accept the external port. Avoid disconnecting the SSH network or any root-storage device.'),
    ('usb.gadget.transfer', 'usb.device', 'Only with a second computer, known-good data cable, saved work and an explicitly authorized gadget configuration: verify device-role detection, a real exposed UDC and host enumeration of the intended gadget, transfer real data in both directions, then physically unplug/replug and confirm recovery. Restore the prior gadget/role configuration. Never force a role or invent a gadget when no UDC is exposed; record that prerequisite as unavailable and leave pending. Host OTG peripheral success does not accept gadget/device mode.'),
    ('storage.microsd.io', 'storage.microsd', 'Use a known-good expendable microSD card with a backed-up filesystem. Verify insertion creates a distinct SD (not internal MMC) block device, mount normally and write/read/compare a uniquely named disposable file without overwriting existing data. Remove that file, sync/unmount/eject, physically remove/reinsert and verify recovery. No automatic media writes; record read-only-card limitations.'),
    ('power.suspend.resume', 'power.sleep', 'With saved work and physical recovery available, perform two consecutive real suspend/resume cycles using physical wake rather than an RTC fallback, then rerun automatic checks; verify display, input, radios and audio recover. Automatic checks never request sleep.'),
    ('power.shutdown.reboot', 'power.boot', 'With saved work and physical recovery available, verify complete power-off, a subsequent normal cold boot, and a supervised warm reboot. Rerun automatic checks after each and confirm input/display/radios/audio recover. Automatic checks never issue power or boot-policy commands.'),
    ('graphics.video.decode', 'graphics.video', 'Play known H.264 and HEVC Main samples using a real VA-API/i965 decode path; verify the player actually selects hardware decoding and displays correct uninterrupted frames, then repeat after rotation. Record codec/sample/player; vainfo profile advertisement alone is not decode acceptance.'),
    ('thermal.cooling.response', 'thermal', 'With supervision, monitor all thermal readings and cooling/throttling telemetry during a short representative workload and recovery to idle. Stop if abnormally hot; do not disable thermal policy or run unattended stress. Verify plausible changing readings and stable behavior without overtemperature. Readable constant policy channels alone do not prove a physical sensor or effective cooling.'),
]


@pytest.mark.parametrize('human_case', [
    pytest.param(check_id, id=check_id, marks=pytest.mark.hardware(
        id='human.' + check_id, component=component, layer='human', instructions=instructions,
    )) for check_id, component, instructions in OBSERVATIONS
])
def test_physical_acceptance(human_case, human):
    assert human, human_case
