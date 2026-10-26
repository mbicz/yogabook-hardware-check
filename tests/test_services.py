"""Optional local policy must not become a universal hardware requirement."""
from subprocess import CompletedProcess

import pytest

from checks.yogabook.test_services import test_daemon_policy as daemon_policy
from ybcheck.plugin import HardwareWarning


def policy_check(required, *, load="loaded", state="inactive", file_state="disabled", rc=0):
    properties = {
        "LoadState": load, "UnitFileState": file_state, "ActiveState": state,
        "MainPID": "0", "Result": "success", "ExecMainStatus": "0", "NRestarts": "0",
    }

    def shell(*argv, **kwargs):
        return CompletedProcess(argv, rc, "\n".join(f"{key}={value}" for key, value in properties.items()), "")

    daemon_policy("example.service", required, shell, lambda *args: None, lambda name: None)


@pytest.mark.parametrize("load,file_state,rc", [
    ("loaded", "disabled", 0), ("masked", "masked", 1), ("not-found", "", 1),
])
def test_optional_lid_policy_is_visible_warning_not_failure(load, file_state, rc):
    with pytest.warns(HardwareWarning):
        policy_check(False, load=load, file_state=file_state, rc=rc)


@pytest.mark.parametrize("load,rc", [("loaded", 0), ("not-found", 1)])
def test_required_keyboard_cannot_be_disabled_or_absent(load, rc):
    with pytest.raises(AssertionError):
        policy_check(True, load=load, rc=rc)


@pytest.mark.parametrize("required", [False, True])
def test_failed_deployed_service_is_not_hidden_as_optional_policy(required):
    with pytest.raises(AssertionError):
        policy_check(required, state="failed")


def test_query_failure_is_not_greened_as_optional_absence():
    with pytest.raises(AssertionError):
        policy_check(False, rc=7)
