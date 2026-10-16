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


def pytest_runtest_setup(item):
    if platform.system() != 'Linux':
        pytest.skip('blocked: Yoga Book checks must run locally on Linux')
    product_path = Path('/sys/class/dmi/id/product_name')
    try:
        product = product_path.read_text().strip()
    except OSError as exc:
        pytest.skip(f'blocked: cannot identify Yoga Book model: {exc}')
    if 'YB1-X90' not in product:
        pytest.skip(f'blocked: suite targets YB1-X90F; detected {product!r}')
