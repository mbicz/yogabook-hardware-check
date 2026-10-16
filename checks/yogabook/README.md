# Yoga Book YB1-X90F checks

This suite is independent of the generic `ybcheck` pytest plugin. Device-specific discovery, expectations, native helper source and GPS CLI options live here. Run locally on the Yoga Book. The board guard requires Linux and the Android-edition DMI identity (`sys_vendor=Intel Corporation`, `product_name=CHERRYVIEW D1 PLATFORM`, `product_version=YETI-11`), matching the kernel's YB1-X90F/X90L panel quirk; other identities are blocked rather than assumed compatible. There is no LTE/modem requirement for this WiFi model.

## Install on Debian and enumerate

From this repository's root:

```sh
sudo apt-get install python3-venv gcc libc6-dev linux-libc-dev \
  alsa-utils iproute2 iw bluez coreutils util-linux udev \
  libinput-tools libwacom-bin dconf-cli libglib2.0-bin \
  iio-sensor-proxy vainfo i965-va-driver
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/yb-check list checks/yogabook
```

The existing working kernel/device setup, SST firmware, UCM profiles, touch-keyboard handler and physical input permissions remain prerequisites. This suite does not install drivers or fix hardware. Missing expected devices/bindings fail; missing permissions/tools are blocked. Run as root for complete physical-device access, particularly at the GDM greeter where an inactive user's session may lack device ACLs:

```sh
sudo .venv/bin/yb-check run checks/yogabook \
  --report reports/before-kernel-upgrade.json -v
```

The default performs enumeration, sysfs/ioctl queries, short CPU-accounting samples and a transient sensor-proxy D-Bus accelerometer claim (released when its client disconnects). It does not change power policy or operate cameras/audio/GPS. Those operations require explicit opt-in; GPS acquisition also requires the original privately prepared runtime. An overall pytest exit zero is not full acceptance: inspect warnings, blocked, skipped and pending results.

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

The catalogue currently contains **132 independently reported checks**, including **33 physical observations**. `yb-check list checks/yogabook` enumerates their stable IDs, layers and active-operation requirements; ordinary pytest collection remains the source of truth. A component's enumeration, operational query, userspace configuration and human observations have independent outcomes.

| Components | Enumeration / headless assertions | Separately pending physical or userspace acceptance |
|---|---|---|
| Front OV2740 / rear OV8858 | Sensor/AtomISP bindings; opt-in MMAP streaming with buffer/error checks | Lit preview, color/detail/orientation, application integration |
| RT5677 / SST / UCM | Physical card/codec/firmware/HiFi profile; opt-in playback and capture with advancing PCM hardware pointers | Speakers/headphones/headset/HDMI audibility and microphone intelligibility |
| GPS BCM4752 | rfkill/UART identity; opt-in checksum-valid, decoded NMEA; daemon lifecycle; fix independently | Outdoor known-position validation |
| Accelerometer, gyro, compass, ALS, hinge, inclination, rotation | Both HID-hub instances, drivers, channels/labels; repeated finite raw/scaled reads | Stimulus response, mounting, accuracy and desktop rotation/posture policy |
| Goodix Halo, HiDeep touch, Wacom pen | Bound endpoints/capabilities; non-consuming evdev queries; pen libwacom integration, no stale calibration/output override, and actual Mutter panel mapping | Typing/layout, touch gestures, pen corners/hover/pressure/strokes through rotations |
| Virtual keyboard/touchpad/tablet switch, physical lid and buttons | Expected capabilities, distinct physical volume/power controls; current lid/tablet switch state; effective no-suspend touchpad quirk | Pointer/click behavior over time, mode-switch recovery, physical button actions and lid policy |
| Both DRV2604L haptics | Bound controllers, input FF capability and effect-slot ioctl | Independent left/right tactile output |
| LCD/Halo/charging/pen-icon lights | Correct endpoints/drivers; brightness/range/actual/trigger readback | Visible output and desktop control |
| Battery / BQ25892 charger | Binding, presence, capacity/voltage/current/status and health readback | Charging-source transitions, battery calibration/runtime |
| Thermal | All discovered thermal-zone and hwmon temperatures, including CPU cores; collect every reading before reporting invalid channels, including 0-K firmware returns | Shared sysfs aliases are not separate sensors; a readable policy constant does not prove physical response. Load cooling effectiveness and sensor accuracy require separate validation |
| CPU / runtime PM / RAPL | CPU idle/frequency accounting deltas; optional frequency residency; discovered device runtime states/deltas; wakeup sources; RAPL energy deltas with wrap accounting | Deep-idle efficiency, battery runtime and real wake response; unavailable optional counters remain blocked, not measured zero |
| Sleep / lid / posture | CHT s2idle contract, live logind policy/inhibitors and sleep masks; both labeled hinge instances converted to circular degree readings with cross-hub diagnostics | Two consecutive supervised suspend/resume cycles, physical wake, quick-reopen cancellation, laptop/closed/360-fold/flat stimulus and shutdown/boot |
| i915 / internal DSI panel | Bound GPU/panel connector, connected state/modes; real DRM/i915 identity ioctls and headless libva H.264/HEVC Main decode-profile queries | Visible rendering, actual decoded video, rotation, brightness and external HDMI |
| eMMC / mounted root | Dynamic backing-chain discovery, geometry, block statistics and exposed error state | Media integrity; optional microSD file I/O |
| USB host | Bound PCI controller/root hubs, speed/authorization/runtime-state readback | Actual attached USB/OTG device operation |
| WiFi / Bluetooth | Onboard identity, read-only link/controller queries; existing WiFi association if available | Sustained traffic/reconnect and paired peripheral behavior |
| RTC | Standalone shell check verifies advancing **RTC driver** time, not system wall time | Absolute accuracy, battery retention, alarms/wake |
| Required userspace services | Keyboard/lid daemon status and restart evidence; sensor-proxy activation/acquisition, udev base/display accelerometer classification and mount matrix | End-to-end typing, mode switching, desktop rotation and physical sleep policy |
| Current-boot kernel / CPU security | Timestamped priority-preserving diagnostics and microcode/mitigation readback; critical faults fail, unresolved component diagnostics warn | Fault reproduction/root-cause repair; latest microcode/security completeness is not established |

No hardcoded eventN, iio:deviceN, v4l-subdevN, ALSA cardN or mmcblkN assumptions. Both HID hubs expose seven IIO functions; their firmware instance suffixes do not establish a reliable base/lid mapping. Constant ALS values may be readable while the physical response is broken; readback and response are deliberately separate results. Idle runtime-suspended devices, an intentionally dark Halo keyboard, an unplugged charger, absent HDMI/peripheral attachments and an unassociated AP are not blindly treated as kernel failures.

These checks do **not** establish exhaustive hardware correctness. Readback is not proof of stimulus response, RF performance, decoded-frame quality, disk surface integrity or acoustic/optical/tactile output. VA-API profile queries prove initialization/capability advertisement, not decoding. Hinge angles do not classify physical posture or replace `SW_LID`: closed and 360-fold can both read near zero. Empty kernel wakeup counters mean no attached wakeup source, not a measured zero. Human checks cover safe manual acceptance and explicitly guarded recovery-dependent suspend/shutdown/reboot observations. Automatic tests never suspend, reboot, power off, scan raw PMIC/GPIO registers, synthesize lid transitions, disconnect networking, upload haptic effects or overwrite disks.

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
