"""Result semantics layered onto pytest-json-report, without changing its raw outcomes."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import socket
import uuid


SCHEMA = 1


def read_optional(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def identity():
    machine = read_optional('/etc/machine-id')
    return {
        'run_id': str(uuid.uuid4()), 'started_at': datetime.now(timezone.utc).isoformat(),
        'kernel': platform.release(), 'system': platform.system(), 'hostname': socket.gethostname(),
        'boot_id': read_optional('/proc/sys/kernel/random/boot_id'),
        'machine': hashlib.sha256(machine.encode()).hexdigest() if machine else None,
        'product': read_optional('/sys/class/dmi/id/product_name'),
    }


def skipped_status(reason):
    if reason.startswith('blocked:'):
        return 'blocked'
    if reason.startswith('human pending:'):
        return 'pending'
    return 'skip'


def stage_reason(test):
    for stage in ('teardown', 'setup', 'call'):
        detail = test.get(stage, {}).get('longrepr')
        if detail:
            return str(detail[-1] if isinstance(detail, (tuple, list)) else detail)
    return ''


def enrich_report(report, catalog, states, run):
    checks = []
    for test in report.get('tests', []):
        metadata = test.get('metadata', {}).get('hardware')
        if not metadata:
            continue
        state = states.get(test['nodeid'], {})
        outcome = test['outcome']
        reason = state.get('reason') or stage_reason(test)
        if outcome == 'skipped':
            status = skipped_status(reason.removeprefix('Skipped: '))
        elif outcome in ('failed', 'error'):
            status = state.get('status', 'error' if outcome == 'error' else 'fail')
        elif outcome == 'passed':
            status = 'warn' if state.get('warnings') else 'pass'
        else:
            status = 'warn'
        checks.append({**metadata, 'nodeid': test['nodeid'], 'status': status,
                       'reason': reason, 'warnings': state.get('warnings', []),
                       'evidence': test.get('metadata', {}).get('evidence', {})})
    report['ybcheck'] = {'schema': SCHEMA, 'run': run, 'catalog': catalog, 'checks': checks,
                         'summary': dict(Counter(check['status'] for check in checks))}
    return report


def load_report(path):
    data = json.loads(Path(path).read_text())
    result = data.get('ybcheck', {})
    if result.get('schema') != SCHEMA:
        raise ValueError(f'{path}: not a supported ybcheck report')
    return result


def observations_for(run, path):
    data = json.loads(Path(path).read_text())
    if data.get('schema') != SCHEMA:
        raise ValueError('unsupported human observation schema')
    if not run.get('boot_id') or any(data.get(key) != run.get(key) for key in ('boot_id', 'kernel', 'machine')):
        raise ValueError('human observations belong to a different boot, kernel or machine')
    return data.get('observations', {})


def compare_reports(before, after):
    if before['run'].get('machine') != after['run'].get('machine'):
        raise ValueError('reports belong to different machines')
    old = {check['id']: check for check in before['checks']}
    new = {check['id']: check for check in after['checks']}
    result = {'regressions': [], 'coverage_gaps': [], 'new': [], 'changed': [],
              'human_pending': [], 'human_failures': []}
    for check_id in sorted(old.keys() | new.keys()):
        a, b = old.get(check_id), new.get(check_id)
        if b and b['layer'] == 'human':
            if b['status'] in ('fail', 'error'):
                result['human_failures'].append(check_id)
            elif b['status'] != 'pass':
                result['human_pending'].append(check_id)
            continue
        if a is None:
            result['new'].append(check_id)
            continue
        if b is None:
            result['coverage_gaps'].append(check_id)
            continue
        if a['status'] != b['status']:
            row = {'id': check_id, 'before': a['status'], 'after': b['status']}
            result['changed'].append(row)
            if a['status'] in ('pass', 'warn') and b['status'] in ('blocked', 'skip', 'pending'):
                result['coverage_gaps'].append(check_id)
            elif (a['status'] == 'pass' and b['status'] in ('warn', 'fail', 'error')) or (
                a['status'] == 'warn' and b['status'] in ('fail', 'error')
            ):
                result['regressions'].append(row)
    return result
