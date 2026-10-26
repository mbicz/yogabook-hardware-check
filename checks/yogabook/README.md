# Yoga Book YB1-X90F checks

This suite is independent of the generic `ybcheck` pytest plugin. Device-specific discovery, expectations, native helper source and GPS CLI options live here. Run locally on the Yoga Book. The board guard requires Linux and the Android-edition DMI identity (`sys_vendor=Intel Corporation`, `product_name=CHERRYVIEW D1 PLATFORM`, `product_version=YETI-11`), matching the kernel's YB1-X90F/X90L panel quirk; other identities are blocked rather than assumed compatible. There is no LTE/modem requirement for this WiFi model.

## Install on Debian and enumerate

From this repository's root:

```sh
sudo apt-get install python3-venv gcc libc6-dev linux-libc-dev \
  alsa-utils iproute2 iw bluez coreutils util-linux udev \
  libinput-tools libwacom-bin dconf-cli libglib2.0-bin \
  iio-sensor-proxy vainfo i965-va-driver pipewire-bin
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/yb-check list checks/yogabook
```

The existing working kernel/device setup, SST firmware, UCM profiles, touch-keyboard handler and physical input permissions remain prerequisites. This suite does not install drivers or fix hardware. Missing expected devices/bindings fail; missing permissions/tools are blocked. Run as root for complete physical-device access, particularly at the GDM greeter where an inactive user's session may lack device ACLs:

```sh
sudo .venv/bin/yb-check run checks/yogabook \
  --report reports/before-kernel-upgrade.json -v
```

The default performs enumeration, sysfs/ioctl queries, short CPU-accounting samples, read-only queries of the existing desktop PipeWire graph and a transient sensor-proxy D-Bus accelerometer claim (released when its client disconnects). It does not change USB role/authorization/gadgets, power policy, brightness or input state; operate cameras/audio/GPS; mount or write removable media; reload drivers; or request suspend, reboot or shutdown. Media/receiver operations require explicit opt-in; GPS acquisition also requires the original privately prepared runtime. An overall pytest exit zero is not full acceptance: inspect warnings, blocked, skipped and pending results.

## Active headless exercise

```sh
# Camera streaming and real speaker/microphone PCM transport; no GPS configuration needed.
sudo .venv/bin/yb-check run checks/yogabook \
  --active camera --active audio --report reports/media.json -v

# Complete configured active run, including GPS. Paths below refer to your existing setup.
sudo .venv/bin/yb-check run checks/yogabook --active all \
  --gps-launcher "$HOME/src/run_gps.py" \
  --gps-runtime "$HOME/src/android-gps-final" --gps-seconds 60 \
  --report reports/after-kernel-upgrade.json -v

.venv/bin/yb-check report reports/after-kernel-upgrade.json
.venv/bin/yb-check compare reports/before-kernel-upgrade.json reports/after-kernel-upgrade.json
```

For useful upgrade comparison, collect the same active groups and prerequisites before and after upgrading. If a previously passing active check was not selected/exercised afterward, comparison reports a coverage gap rather than success. Use pytest's normal `-k`/`-m` selection for focused debugging, not as a replacement for a full acceptance run.

Active camera tests turn on the camera and acquire 40 frames per sensor. They retain **no images**, require eight MMAP buffers, check negotiated YUYV geometry/buffer bounds, and stream off/release mappings/restore prior input and format when supported. No private AtomISP ioctl, module reload or AE/AWB setting changes are used. Camera application preview, color, scene lighting and optical quality remain independent human checks.

Active audio tests temporarily configure HiFi/Speaker1 and play an eight-second low-amplitude (-36 dBFS) tone. They require sustained `RUNNING` PCM state and an advancing **hardware** pointer, not merely an application buffer or a transient open. Microphone capture selects parameters from the physical UCM PCM's advertised constraints and discards four seconds of data into `/dev/null`. Both save and restore mixer state in `finally`, stop/reap owned processes, and fail if restoration fails. There is no claim of audible sound, intelligible speech or logged-in PipeWire routing from a successful PCM test. Run when audio devices are idle; busy devices are blocked, never stolen.

### GPS prerequisites and interpretation

The suite reuses the existing stock GPS launcher rather than implementing another receiver initialization protocol. In the outer Yoga Book workspace, runtime preparation is documented alongside `device/gps/prepare_runtime.py` and `device/gps/run_gps.py`; supply the local launcher and prepared runtime paths explicitly. The original proprietary daemon/firmware are not included or redistributed. A standalone clone still needs that separately prepared runtime.

GPS options are registered **only** by this directory's `conftest.py`:

- `--gps-launcher PATH`: working stock-daemon Python launcher.
- `--gps-runtime PATH`: privately prepared Android runtime.
- `--gps-seconds N`: positive bounded acquisition window, default 20 seconds.
- `--gps-nmea PATH`: analyze an existing capture without launching the receiver. Valid historical captures warn explicitly; they are **not evidence of current kernel/hardware health**.

The checks separate `gps.rfkill`/`gps.uart` enumeration, `gps.nmea` valid decoded receiver traffic, `gps.daemon` userspace lifecycle, and `gps.fix` satellite positioning. Valid NMEA without a fix passes receiver operation but warns for positioning: indoors, obstructed sky or a short window are not receiver-failure proof. A nonzero daemon exit still fails independently, even if valid positions were received. UART identities are discovered through ACPI association; the existing launcher's fixed ttyS2 mapping must agree before it may run. A weaker documented-controller association is an explicit warning.

The launcher owns its private mount namespace and restores termios/rfkill/runtime-PM policy. On deadline the suite first requests graceful termination. Forced termination is an error with an explicit restoration warning; it cannot guarantee cleanup of a separately sessioned vendor daemon. Never start a second receiver owner. Review the existing launcher's safety behavior before enabling acquisition.

**GPS reports/captures can contain precise coordinates.** Other reports can contain SSIDs, MAC addresses and hostnames. Keep them local, redact before sharing, and never commit device reports or proprietary runtime data.

## Coverage and evidence boundaries

`yb-check list checks/yogabook` enumerates the comprehensive stable IDs, layers and active-operation requirements; ordinary pytest collection remains the source of truth, rather than a stale hardcoded check count. A component's enumeration, operational query, userspace configuration and human observations have independent outcomes.

The keyboard handler is a required persistent input service. A local
`yogabook-lid-watch` helper is optional: disabled, masked or absent policy is a
visible warning, not a requirement to install/start that helper. An actually
failed deployed service still fails its check. Native lid handling, masked sleep
targets and real lid/suspend acceptance remain independent checks; the suite
never starts a watcher or unmasks sleep to make the result green.

| Subsystem / check IDs | Safe automated proof (default) | Optional active / attachment evidence | Required physical acceptance (always separate) |
|---|---|---|---|
| USB host: `usb.host.*`, `usb.peripheral.attached_read` | PCI programming interface distinguishes xHCI host from xDCI device function; bound host/root hubs, speed/authorization/runtime status | Already-attached non-hub device identity/configuration/interface drivers; absent peripheral is **skip**, not pass/fail | `human.usb.attached.device`: real data-capable OTG adapter, intended peripheral operation and actual unplug/replug; record role transition |
| USB device/OTG: `usb.device.controller_support`, `usb.otg.role_read` | Separate xDCI/DWC3/UDC inventory and current role-switch/extcon state; no UDC warns that device mode is unproven, without diagnosing an unplugged neutral role as a missing driver | No automatic role forcing, VBUS switching or gadget configuration, even with `--active all` | `human.usb.gadget.transfer`: second computer, exposed UDC, explicitly authorized gadget, host enumeration, bidirectional transfer and reconnect. Host-mode success is not gadget acceptance |
| Internal eMMC/root: `storage.mmc.enumeration`, `storage.root_and_mmc.read` | Dynamic mounted-root backing chain, card geometry, block statistics and exposed error state | No automatic writes, surface scan or filesystem integrity claim | Media integrity/backup/recovery remain outside a readback smoke test |
| Removable microSD: `storage.microsd.*` | Reader/controller support separately from an inserted SD card; neither MMC nor SDIO satisfies inserted-SD evidence | Existing SD geometry/RO/stat readback; no card is **skip**, no mount or filesystem writes | `human.storage.microsd.io`: expendable backed-up card, disposable write/read/compare, safe eject and real reinsert |
| HDMI video/audio: `display.hdmi.*`, `audio.hdmi.enumeration` | i915 HDMI connectors and HDMI ALSA endpoint, existing ELD | Connected sink status/modes; disconnected display is **skip**, no forced modeset | `human.display.external`, `human.audio.hdmi.audible`: external image, actual hotplug recovery and audible receiver playback |
| WiFi/BT: `network.wifi.*`, `network.bluetooth.*`, `network.firmware.*` | Bound onboard identity, current link/controller state/counters; installed firmware (including updates/compression), current-boot WiFi version and nonzero BT patch-build evidence | Existing AP association only; no AP is **skip**. No scan/pair/disconnect or generated network traffic | `human.network.wifi.traffic`, `human.network.bluetooth.peripheral`: real sustained transfers, reconnect and paired input/audio/data behavior. Firmware loaded is not RF/transfer acceptance |
| GPS: `gps.rfkill`, `gps.uart`, `gps.service.configuration` | Firmware-associated rfkill/UART and installed/current GPS service state; no service warns, and running gpsd/initializer never proves receiver traffic | `gps.nmea`, `gps.daemon`, `gps.fix`: explicit stock-launcher opt-in or clearly labeled historical capture analysis | `human.gps.outdoor.position`: live outdoor known-position validation. Indoor no-fix is not receiver-failure proof |
| i915/internal DSI: `graphics.render_node.ioctl`, `display.panel.*`, `graphics.vaapi.decode_profiles` | Real DRM/i915 identity ioctls; internal connection/modes; actual libva H.264/HEVC Main capability initialization using real i965 fallback | No default rendering benchmark, decode stream or modeset; Cherryview requires no DMC firmware | `human.display.image`, `human.graphics.video.decode`: stable visible rendering/rotation and verified real hardware decoding, not profile advertisement alone |
| Front/rear cameras: `camera.front.*`, `camera.rear.*` | Actual OV2740/OV8858 + AtomISP bindings and distinct existing desktop PipeWire nodes | `--active camera`: 40 MMAP frames per sensor with buffers/error checks; no images retained | `human.camera.front.image`, `human.camera.rear.image`: actual application preview, color/detail/orientation/exposure |
| Rear AE/AF: independent human checks | Stream transport/node presence does not prove optical behavior or daylight headroom | No default AE/AWB/focus writes, private ISP probes or intrusive scene tests | `human.camera.rear.ae.daylight`: dim-to-daylight convergence, sky/clipping/pink highlights/shadows/flicker; `human.camera.rear.autofocus`: visible near/far focus and switch recovery. Front is fixed-focus |
| Speakers/mic/headset: `audio.physical.enumeration`, `audio.ucm.enumeration`, `audio.desktop.*`, `input.headset.*` | Physical RT5677/SST/HiFi profile, existing desktop ALSA sink/source, non-consuming jack-state query | `--active audio`: low-level playback/capture with advancing **hardware** pointer; mixer restoration mandatory | `human.audio.speaker.audible`, `human.audio.microphone.intelligible`, `human.audio.headphones.audible`, `human.audio.headset.routing`: real L/R sound, speech, jack transitions, muting and unplug recovery |
| Halo keyboard/touchpad: `input.keyboard.*`, `input.touchpad.*`, `services.touch_keyboard*` | Bound capabilities, evdev query, running daemon, actual selected layout and Goodix ownership | No synthetic input, brightness writes or key presses in the default suite | `human.input.keyboard.typing`, `human.input.keyboard.fn`, `human.backlight.keyboard.visible`: full layout, no stuck Fn/extra keys, plain F10 cycles 0/128/255 on max255 once per press, Fn+F10 preserved; pointer/tap/click/scroll >40s and mode recovery |
| HiDeep touch/Wacom pen: `input.touchscreen.*`, `input.pen.*` | Bound input/capabilities, pen libwacom System integration, no stale calibration/output override and actual Mutter panel mapping | Desktop checks blocked if no active graphical session; no consuming evdev reads | `human.input.touchscreen.gestures`, `human.input.pen.mapping`: all corners, multitouch, hover/pressure/strokes through rotations |
| Posture/buttons/lid: `input.lid.*`, `input.tablet_switch.*`, `input.volume_buttons.*`, `input.power_button.*`, `posture.*` | Distinct physical controls/current switch state, labeled/circular hinge degrees, live policy/services, no-suspend touchpad quirk | No synthetic lid/tablet transitions; hinge angle cannot distinguish closed from 360-fold or replace SW_LID | `human.input.buttons.actions`, `human.input.lid.policy`, `human.sensors.hinge.response`, `human.sensors.desktop.rotation`: supervised physical actions, quick reopen cancellation, all postures and desktop recovery |
| Motion/ALS: `sensors.*`, `services.sensor_proxy.*` | Both HID hubs, drivers/channels/labels, repeated finite readings, real sensor-proxy acquisition and base/display udev metadata | All-zero ALS samples **warn**, not a clean readback acceptance; nonzero constant samples still do not prove responsiveness | `human.sensors.als.response`, `human.sensors.motion.response`: illuminate/cover both ALS and physically stimulate both motion sensor sets; accuracy/mounting/desktop behavior independent |
| Both haptics: `haptics.controllers.*` | Two bound DRV2604L controllers, both input FF endpoints and effect-slot ioctls | No effect upload/play; binding and slot count do not prove tactile output | `human.haptics.left.felt`, `human.haptics.right.felt`: independently feel each endpoint during typing |
| LCD/Halo/edge/pen-icon lights: `lights.*` | Endpoint/driver and brightness/range/actual/trigger readback; zero may be intentional | No brightness/trigger writes | `human.backlight.panel.visible`, `human.backlight.keyboard.visible`, `human.leds.visible`: real visible output and intended controls/modes |
| Battery/charger: `power.battery.*`, `power.charger.*` | Binding/presence/capacity/voltage/current/status/health; charger offline is allowed | No source toggling, discharge cycle or charging-policy writes | `human.power.charging.transition`, `human.power.battery.runtime`: actual source transition/charging and measured drain under documented workload without deep discharge |
| PM/thermal: `power.cpu.*`, `power.runtime.*`, `power.wakeup.*`, `power.rapl.*`, `thermal.*` | Idle/frequency/runtime/energy accounting, available wake sources, every discovered zone/hwmon temperature, cooling-device state/range; invalid temperatures fail | Optional counters blocked when unavailable; empty wake counters are not a measured zero; no stress or thermal-policy writes | `human.thermal.cooling.response`, `human.power.battery.runtime`: supervised response/cooling and real efficiency, not readable constants alone |
| Sleep/shutdown: `power.sleep.*` | Actual CHT s2idle contract and logind/masks/inhibitors, no fixed kernel version | **Never** automatically suspend, reboot, shut down or change boot policy, including `--active all` | `human.power.suspend.resume`: two consecutive supervised real cycles/physical wake and subsystem recovery; `human.power.shutdown.reboot`: complete power-off and subsequent normal boot, physical recovery required |
| RTC: `rtc.time_advances` | Standalone shell reads advancing RTC-driver time, not system wall time | No alarm/wake programming | Absolute accuracy, power-off retention and actual alarm wake require physical validation |
| Kernel/security: `kernel.current_boot_diagnostics`, `cpu.microcode_security.readback` | Bounded current-boot severity/timestamp diagnostics, all CPU revisions/mitigation statements; critical faults fail, unresolved diagnostics warn | No hardcoded kernel/microcode revision; missing/unreadable logs blocked, truncated logs warn | Fault reproduction/root-cause repair and latest security completeness are not established by smoke checks |
| Service/security-engine limitations: `services.system.failed_units`, `services.desktop.failed_units`, `platform.txe.enumeration` | Current system/user failures are preserved as warnings (including unrelated desktop software); exposed but unbound optional Intel TXE warns explicitly | No reset-failed, restarts, ME/TXE commands or firmware updates; absent optional TXE is skip | Diagnose each software/security-engine issue independently; neither hides an operational hardware result nor certifies security completeness |

No hardcoded eventN, iio:deviceN, v4l-subdevN, ALSA cardN, mmcblkN or root-hub bus numbers. Both HID hubs expose seven IIO functions; firmware instance suffixes do not establish base/lid mapping. Hardware attachments unavailable are **skip**; unavailable tools/permissions/session/exclusive device access are **blocked**; missing required onboard support fails or explicitly warns as scoped above. A human observation remains **pending** until actually performed and recorded, even if enumeration passes.

These checks do not establish exhaustive hardware correctness. The matrix deliberately distinguishes safe machine evidence from attachment, active and optical/acoustic/tactile/physical acceptance. Automatic tests never scan raw PMIC/GPIO registers, synthesize input/lid transitions, disconnect networking, force USB roles, configure gadgets, write removable filesystems, upload haptic effects or override firmware/thermal policy. `--active all` only selects the documented camera/audio/GPS operations; it does not authorize suspend or any other intrusive procedure.

Kernel diagnostics prefer the current boot's info-and-higher journal (debug chatter excluded), falling back to the current `dmesg --raw` ring. Both reads are time/byte/record bounded. Original severity/timestamp/text, source, truncation and collection failures are retained. An incomplete window warns and cannot prove absence of earlier faults; missing/unreadable logs are blocked. BUG/Oops/panic, RCU stalls, hung tasks, runtime-PM accounting faults and GPU hangs fail even if logged below error priority. Other unresolved warnings/errors remain visible and do not erase an independent successful component query.

## Record physical acceptance

After actually performing the listed observation:

```sh
.venv/bin/yb-check record reports/after-kernel-upgrade.json \
  human.audio.speaker.audible pass --note 'Heard correct L/R output on the device'
.venv/bin/yb-check report reports/after-kernel-upgrade.json \
  --human-results reports/after-kernel-upgrade.human.json
```

A skipped/pending human check never invalidates a successful low-level result. It also never becomes a pass automatically: observations are bound to the report's boot/kernel/machine and must be explicitly supplied. Preserve a fallback kernel until both the automatic evidence and required physical observations meet your acceptance criteria.

The RTC program can also be run directly: `sh checks/yogabook/test_rtc.sh`. See the root README for the Python/shell assertion/status contract and how to add an independent suite without modifying the framework.
