"""Pytest owns collection, fixtures and assertions; this plugin adds hardware metadata."""

import json
import os
from pathlib import Path
import shutil

import pytest

from .commands import run_command
from .reporting import enrich_report, identity, observations_for
from .scripts import ShellFile


class HardwareWarning(UserWarning):
    """An explicit non-fatal hardware limitation, never a clean PASS."""


def pytest_addoption(parser):
    group = parser.getgroup('hardware checks')
    group.addoption('--active', action='append', default=[], metavar='GROUP',
                    help='Opt into a suite-defined intrusive check group (repeatable; all enables every group)')
    group.addoption('--human-results', default=None, help='Explicit observations bound to this boot/kernel/machine')
    group.addoption('--inventory-json', default=None, help='Save collected check metadata without running checks')


def hardware_metadata(item):
    mark = item.get_closest_marker('hardware')
    if not mark:
        return None
    data = dict(mark.kwargs)
    for field in ('id', 'component', 'layer'):
        if not isinstance(data.get(field), str) or not data[field]:
            raise pytest.UsageError(f'{item.nodeid}: hardware marker requires nonempty {field!r}')
    active = item.get_closest_marker('active')
    if active:
        if len(active.args) != 1 or not isinstance(active.args[0], str):
            raise pytest.UsageError(f'{item.nodeid}: active marker needs one group name')
        data['active'] = active.args[0]
    data.setdefault('instructions', '')
    return data


def pytest_configure(config):
    config.addinivalue_line('markers', 'hardware(id, component, layer, instructions=""): check identity and evidence layer')
    config.addinivalue_line('markers', 'active(group): explicit opt-in for an intrusive operation')
    config.addinivalue_line('filterwarnings', 'always::ybcheck.plugin.HardwareWarning')
    extension = ReportExtension(config)
    config._ybcheck = extension
    config.pluginmanager.register(extension, 'ybcheck-reporting')


def pytest_collect_file(file_path, parent):
    if file_path.suffix == '.sh' and file_path.name.startswith('test_'):
        return ShellFile.from_parent(parent, path=file_path)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    seen = set()
    catalog = []
    for item in items:
        data = hardware_metadata(item)
        if not data:
            continue
        if data['id'] in seen:
            raise pytest.UsageError(f'duplicate hardware check id: {data["id"]}')
        seen.add(data['id'])
        catalog.append({**data, 'nodeid': item.nodeid, 'selected': True})
    config._ybcheck.catalog = catalog
    if catalog and getattr(config.option, 'numprocesses', None):
        raise pytest.UsageError('hardware checks must run serially; do not use pytest-xdist -n')


def pytest_runtest_setup(item):
    data = hardware_metadata(item)
    if not data:
        return
    active = data.get('active')
    enabled = item.config.getoption('active')
    if active and active not in enabled and 'all' not in enabled:
        pytest.skip(f'opt-in: rerun with --active {active}')


@pytest.fixture
def evidence(request):
    def record(key, value):
        json.dumps(value)  # Reject nonserializable evidence at its source, not during report teardown.
        if not hasattr(request.node, '_yb_evidence'):
            request.node._yb_evidence = {}
        request.node._yb_evidence[key] = value
    return record


@pytest.fixture
def shell(evidence):
    commands = []
    def execute(*argv, timeout=10, check=False, input=None):
        result = run_command(*argv, timeout=timeout, check=False, input=input)
        commands.append({'argv': [str(arg) for arg in argv], 'returncode': result.returncode,
                         'stdout': result.stdout, 'stderr': result.stderr})
        evidence('commands', commands)
        if check:
            result.check_returncode()
        return result
    return execute


@pytest.fixture
def require_program():
    def require(name):
        path = shutil.which(name)
        if path is None:
            pytest.skip(f'blocked: missing program {name}; install its system package')
        return path
    return require


@pytest.fixture
def require_root():
    def require():
        if not hasattr(os, 'geteuid') or os.geteuid() != 0:
            pytest.skip('blocked: root privileges required; rerun the selected checks with sudo')
    return require


@pytest.fixture
def human(request, evidence):
    data = hardware_metadata(request.node)
    path = request.config.getoption('human_results')
    if not path:
        pytest.skip(f'human pending: {data["instructions"]}')
    try:
        observations = observations_for(request.config._ybcheck.run, path)
    except (OSError, ValueError) as exc:
        pytest.skip(f'blocked: {exc}')
    observation = observations.get(data['id'])
    if not observation or observation.get('status') == 'pending':
        pytest.skip(f'human pending: {data["instructions"]}')
    evidence('human_observation', observation)
    assert observation.get('status') in ('pass', 'fail'), 'invalid human observation status'
    assert observation['status'] == 'pass', observation.get('note', 'human reported failure')
    return True


class ReportExtension:
    def __init__(self, config):
        self.config = config
        self.run = identity()
        self.catalog = []
        self.states = {}

    def pytest_deselected(self, items):
        deselected = {item.nodeid for item in items}
        for entry in self.catalog:
            if entry['nodeid'] in deselected:
                entry['selected'] = False

    def pytest_collection_finish(self, session):
        path = self.config.getoption('inventory_json')
        if path:
            destination = Path(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(self.catalog, indent=2) + '\n')

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        if not hardware_metadata(item):
            return
        state = self.states.setdefault(item.nodeid, {})
        if report.failed:
            is_assertion = call.excinfo and isinstance(call.excinfo.value, (AssertionError, pytest.fail.Exception))
            status = 'fail' if call.when == 'call' and is_assertion else 'error'
            if state.get('status') != 'error':
                state['status'] = status
            state['reason'] = str(call.excinfo.value) if call.excinfo else str(report.longrepr)
        elif report.skipped and 'status' not in state:
            detail = report.longrepr
            state['reason'] = str(detail[-1] if isinstance(detail, tuple) else detail).removeprefix('Skipped: ')

    def pytest_warning_recorded(self, warning_message, when, nodeid, location):
        if isinstance(warning_message.message, HardwareWarning) and nodeid:
            self.states.setdefault(nodeid, {}).setdefault('warnings', []).append(str(warning_message.message))

    @pytest.hookimpl(optionalhook=True)
    def pytest_json_runtest_metadata(self, item, call):
        data = hardware_metadata(item)
        if not data:
            return {}
        return {'hardware': data, 'evidence': dict(getattr(item, '_yb_evidence', {}))}

    @pytest.hookimpl(optionalhook=True)
    def pytest_json_modifyreport(self, json_report):
        enrich_report(json_report, self.catalog, self.states, self.run)

    def pytest_terminal_summary(self, terminalreporter):
        if not self.catalog:
            return
        warnings = [node for node, state in self.states.items() if state.get('warnings')]
        terminalreporter.write_sep('-', 'Hardware layers remain independent; skips are not passes')
        if warnings:
            terminalreporter.write_line(f'Hardware warnings: {len(warnings)} (non-fatal, see JSON evidence)')
        terminalreporter.write_line('Human observations require explicit recording for this boot/kernel.')
