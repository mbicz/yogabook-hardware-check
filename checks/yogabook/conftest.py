"""All device-specific options and execution policy belong to this suite."""

from pathlib import Path
import platform

import pytest


def pytest_addoption(parser):
    group = parser.getgroup('Yoga Book GPS')
    group.addoption('--gps-launcher', default=None, help='Existing stock-daemon run_gps.py launcher')
    group.addoption('--gps-runtime', default=None, help='Prepared stock Android GPS runtime; never bundled here')
    group.addoption('--gps-seconds', type=int, default=20, help='Bounded active receiver observation window')
    group.addoption('--gps-nmea', default=None, help='Inspect a saved capture instead of live receiver operation')


# Android-edition firmware does not report a Lenovo model name. This is the
# same DMI identity the kernel's YB1-X90F/X90L panel-orientation quirk uses.
BOARD_DMI = {
    'sys_vendor': 'Intel Corporation',
    'product_name': 'CHERRYVIEW D1 PLATFORM',
    'product_version': 'YETI-11',
}


def pytest_runtest_setup(item):
    if platform.system() != 'Linux':
        pytest.skip('blocked: Yoga Book checks must run locally on Linux')
    try:
        detected = {key: (Path('/sys/class/dmi/id') / key).read_text().strip() for key in BOARD_DMI}
    except OSError as exc:
        pytest.skip(f'blocked: cannot identify Yoga Book model: {exc}')
    if detected != BOARD_DMI:
        pytest.skip(f'blocked: suite targets YB1-X90F/X90L (DMI {BOARD_DMI}); detected {detected}')
