"""Indexed controller queries do not depend on an optional sysfs address file."""
from subprocess import CompletedProcess

import pytest

from checks.yogabook import test_network as network
from ybcheck.plugin import HardwareWarning


def query(monkeypatch, output):
    monkeypatch.setattr(network, "bluetooth", lambda evidence: [{"name": "hci7", "address": ""}])
    monkeypatch.setattr(network.shutil, "which", lambda name: "/usr/bin/hciconfig" if name == "hciconfig" else None)
    evidence = {}
    network.test_bluetooth_controller_read(
        lambda *argv, **kwargs: CompletedProcess(argv, 0, output, ""),
        lambda key, value: evidence.update({key: value}))
    return evidence["bluetooth_queries"][0]


def test_indexed_hci_query_works_without_sysfs_address(monkeypatch):
    result = query(monkeypatch, "hci7:\tType: Primary Bus: UART\n BD Address: 0E:82:35:16:DA:CF\n UP RUNNING\n")
    assert result["powered"] is True


@pytest.mark.parametrize("output", [
    "hci7: Type: Primary\n UP RUNNING\n",
    "hci7: Type: Primary\n BD Address: 00:00:00:00:00:00\n UP RUNNING\n",
    "hci8: Type: Primary\n BD Address: 0E:82:35:16:DA:CF\n UP RUNNING\n",
])
def test_uninitialized_or_wrong_index_is_not_controller_health(monkeypatch, output):
    with pytest.raises(AssertionError):
        query(monkeypatch, output)


def test_powered_off_controller_warns_without_changing_policy(monkeypatch):
    with pytest.warns(HardwareWarning, match="powered off"):
        result = query(monkeypatch, "hci7: Type: Primary\n BD Address: 0E:82:35:16:DA:CF\n DOWN\n")
    assert result["powered"] is False
