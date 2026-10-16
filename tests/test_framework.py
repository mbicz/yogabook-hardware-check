"""Consumer-visible result/coverage contracts, exercised through real pytest runs."""

import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest

from ybcheck.cli import record_observation
from ybcheck.commands import CommandTimeout, run_command
from ybcheck.reporting import compare_reports, observations_for

pytest_plugins = ['pytester']


@pytest.mark.parametrize(('code', 'status', 'pytest_code'), [
    (0, 'pass', 0), (1, 'fail', 1), (2, 'error', 1),
    (77, 'skip', 0), (78, 'blocked', 0), (79, 'warn', 0),
])
def test_standalone_shell_result_contract(pytester, code, status, pytest_code):
    pytester.makefile('.sh', test_sensor='''#!/bin/sh
# ybcheck: {"id":"example.sensor","component":"example","layer":"sample"}
printf 'sensor diagnostic\\n'
exit ''' + str(code))
    result = pytester.runpytest_subprocess('--json-report', '--json-report-file=result.json')
    assert result.ret == pytest_code
    report = json.loads((pytester.path / 'result.json').read_text())
    check = report['ybcheck']['checks'][0]
    assert check['status'] == status
    assert check['evidence']['stdout'] == 'sensor diagnostic\n'
    if status == 'warn':
        assert check['warnings'], 'nonfatal warnings must remain visible in structured results'


@pytest.mark.parametrize(('body', 'status'), [
    ('assert 2 + 2 == 5, "sample out of range"', 'fail'),
    ('raise RuntimeError("driver command crashed")', 'error'),
    ('pytest.skip("blocked: device owned by another process")', 'blocked'),
    ('pytest.skip("human pending: inspect the physical output")', 'pending'),
    ('warnings.warn("sample readable; stimulus response unverified", HardwareWarning)', 'warn'),
])
def test_python_assertions_and_infrastructure_errors_stay_distinct(pytester, body, status):
    pytester.makepyfile('''import pytest, warnings
from ybcheck.plugin import HardwareWarning
@pytest.mark.hardware(id='example.read', component='example', layer='headless')
def test_read():
    ''' + body)
    pytester.runpytest_subprocess('--json-report', '--json-report-file=result.json')
    report = json.loads((pytester.path / 'result.json').read_text())
    assert report['ybcheck']['checks'][0]['status'] == status


def test_command_timeout_stops_owned_descendant_activity(tmp_path):
    heartbeat = tmp_path / 'heartbeat'
    child = ("import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
             f"p=pathlib.Path({str(heartbeat)!r}); "
             "exec('while True:\\n p.write_text(str(time.monotonic()))\\n time.sleep(.02)')")
    parent = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(60)'
    start = time.monotonic()
    with pytest.raises(CommandTimeout):
        run_command(sys.executable, '-c', parent, timeout=.5)
    assert time.monotonic() - start < 5
    before = heartbeat.read_text()
    time.sleep(.15)
    assert heartbeat.read_text() == before, 'a timed-out shell command left an owned descendant running'


def sample_report(checks):
    return {'run': {'run_id': 'run-a', 'boot_id': 'boot-a', 'kernel': '6.example', 'machine': 'host-a'},
            'catalog': [{'id': item['id'], 'layer': item['layer']} for item in checks], 'checks': checks}


def test_regressions_coverage_loss_and_unchecked_humans_are_separate():
    before = sample_report([
        {'id': 'sensor', 'layer': 'headless', 'status': 'pass'},
        {'id': 'stream', 'layer': 'headless', 'status': 'pass'},
        {'id': 'removed', 'layer': 'enumeration', 'status': 'pass'},
        {'id': 'view', 'layer': 'human', 'status': 'pass'},
    ])
    after = sample_report([
        {'id': 'sensor', 'layer': 'headless', 'status': 'warn'},
        {'id': 'stream', 'layer': 'headless', 'status': 'skip'},
        {'id': 'view', 'layer': 'human', 'status': 'pending'},
    ])
    comparison = compare_reports(before, after)
    assert comparison['regressions'] == [{'id': 'sensor', 'before': 'pass', 'after': 'warn'}]
    assert comparison['coverage_gaps'] == ['removed', 'stream']
    assert comparison['human_pending'] == ['view']


def test_human_observations_cannot_green_an_automatic_check_or_different_boot(tmp_path):
    report = sample_report([
        {'id': 'read', 'layer': 'headless', 'status': 'pass'},
        {'id': 'view', 'layer': 'human', 'status': 'pending'},
    ])
    destination = tmp_path / 'human.json'
    with pytest.raises(ValueError, match='not a human check'):
        record_observation(report, 'read', 'pass', 'observed', destination)
    record_observation(report, 'view', 'pass', 'actual physical image looked correct', destination)
    assert observations_for(report['run'], destination)['view']['status'] == 'pass'
    for field in ('boot_id', 'kernel', 'machine'):
        with pytest.raises(ValueError, match='different boot'):
            observations_for({**report['run'], field: 'changed'}, destination)


def test_duplicate_stable_ids_are_a_collection_error(pytester):
    pytester.makepyfile('''import pytest
@pytest.mark.hardware(id='duplicated', component='sensor', layer='headless')
def test_first():
    assert True
@pytest.mark.hardware(id='duplicated', component='sensor', layer='headless')
def test_second():
    assert True
''')
    result = pytester.runpytest_subprocess()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(['*duplicate hardware check id: duplicated*'])


def test_cli_inventory_collects_supplied_scripts_without_executing_them(tmp_path):
    executed = tmp_path / 'executed'
    script = tmp_path / 'test_external.sh'
    script.write_text('#!/bin/sh\n'
                      '# ybcheck: {"id":"external.sample","component":"external","layer":"custom"}\n'
                      f'touch "{executed}"\n')
    result = run_command(sys.executable, '-m', 'ybcheck.cli', 'list', str(tmp_path), '--json', timeout=20)
    assert result.returncode == 0, result.stderr
    inventory = json.loads(result.stdout)
    assert [(item['id'], item['layer']) for item in inventory] == [('external.sample', 'custom')]
    assert not executed.exists(), 'inventory collection ran a test body'


def test_cli_forwards_suite_defined_options_without_framework_registration(tmp_path):
    (tmp_path / 'conftest.py').write_text(
        'def pytest_addoption(parser):\n'
        '    parser.addoption("--sample-value", type=int, default=0)\n'
    )
    (tmp_path / 'test_external.py').write_text(
        'import pytest\n'
        '@pytest.mark.hardware(id="external.option", component="external", layer="custom")\n'
        'def test_assertion(request):\n'
        '    assert request.config.getoption("sample_value") == 17\n'
    )
    report_path = tmp_path / 'report.json'
    result = run_command(sys.executable, '-m', 'ybcheck.cli', 'run', str(tmp_path),
                         '--sample-value', '17', '--report', str(report_path), timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(report_path.read_text())
    assert report['ybcheck']['checks'][0]['status'] == 'pass'


def test_failed_human_observation_is_not_misreported_as_pending():
    before = sample_report([{'id': 'visual', 'layer': 'human', 'status': 'pending'}])
    after = sample_report([{'id': 'visual', 'layer': 'human', 'status': 'fail'}])
    comparison = compare_reports(before, after)
    assert comparison['human_failures'] == ['visual']
    assert comparison['human_pending'] == []
