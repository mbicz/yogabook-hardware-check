"""Thin pytest invocation plus local report/observation utilities."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from .reporting import SCHEMA, compare_reports, load_report, observations_for


def record_observation(report, check_id, status, note, destination):
    matches = [check for check in report['catalog'] if check['id'] == check_id]
    if len(matches) != 1 or matches[0]['layer'] != 'human':
        raise ValueError(f'{check_id}: not a human check in this report')
    run = report['run']
    if not run.get('boot_id'):
        raise ValueError('human observations require a report with an actual Linux boot identity')
    destination = Path(destination)
    if destination.exists():
        observations_for(run, destination)  # Do not silently overwrite a different boot's observations.
        document = json.loads(destination.read_text())
    else:
        document = {'schema': SCHEMA, **{key: run.get(key) for key in ('run_id', 'boot_id', 'kernel', 'machine')},
                    'observations': {}, 'history': []}
    observation = {'status': status, 'note': note, 'observed_at': datetime.now(timezone.utc).isoformat(),
                   'recorded_by': getpass.getuser(), 'source_run_id': run['run_id']}
    document['observations'][check_id] = observation
    document['history'].append({'id': check_id, **observation})
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2) + '\n')


def display_report(report, human_results=None):
    observations = observations_for(report['run'], human_results) if human_results else {}
    print(f"Kernel: {report['run']['kernel']}  Boot: {report['run']['boot_id']}")
    statuses = []
    for check in report['checks']:
        observation = observations.get(check['id']) if check['layer'] == 'human' else None
        status = observation['status'] if observation else check['status']
        statuses.append(status)
        print(f"{status.upper():8} {check['layer']:11} {check['id']}")
        if status != 'pass':
            reason = (observation.get('note', '') if observation else
                      check['reason'] or '\n'.join(check.get('warnings', [])))
            if reason:
                print('\n'.join('         ' + line for line in reason.splitlines()))
    print(' '.join(f'{key}={value}' for key, value in sorted(Counter(statuses).items())))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='Run checks at supplied paths; additional arguments go to pytest')
    run.add_argument('--report', default=None)
    inventory = commands.add_parser('list', help='Enumerate checks without running hardware operations')
    inventory.add_argument('--json', action='store_true')
    report_parser = commands.add_parser('report', help='Show independent results, optionally overlaying human observations')
    report_parser.add_argument('report')
    report_parser.add_argument('--human-results')
    compare = commands.add_parser('compare', help='Compare automatic results and coverage between kernel runs')
    compare.add_argument('before')
    compare.add_argument('after')
    record = commands.add_parser('record', help='Record a real human observation, separately from the immutable pytest report')
    record.add_argument('report')
    record.add_argument('check_id')
    record.add_argument('status', choices=('pass', 'fail', 'pending'))
    record.add_argument('--note', required=True)
    record.add_argument('--output')
    args, extra = parser.parse_known_args(argv)
    if extra and args.command not in ('run', 'list'):
        parser.error('unrecognized arguments: ' + ' '.join(extra))
    if extra[:1] == ['--']:
        extra = extra[1:]
    try:
        if args.command == 'run':
            path = Path(args.report) if args.report else Path('reports') / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8] + '.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                raise ValueError(f'{path}: refusing to overwrite an existing run; choose a new --report')
            return subprocess.call([sys.executable, '-m', 'pytest', '-p', 'ybcheck', '--json-report',
                                    f'--json-report-file={path}', '--json-report-indent=2', *extra])
        if args.command == 'list':
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'inventory.json'
                process = subprocess.run(
                    [sys.executable, '-m', 'pytest', '-p', 'ybcheck', '--collect-only', '-q',
                     f'--inventory-json={path}', *extra], capture_output=True, text=True,
                )
                if process.returncode:
                    print(process.stdout + process.stderr, file=sys.stderr)
                    return process.returncode
                catalog = json.loads(path.read_text())
                if args.json:
                    print(json.dumps(catalog, indent=2))
                else:
                    for item in catalog:
                        if item['selected']:
                            opt_in = f" [active:{item['active']}]" if item.get('active') else ''
                            print(f"{item['id']:43} {item['layer']:11} {item['component']}{opt_in}")
                return 0
        if args.command == 'report':
            display_report(load_report(args.report), args.human_results)
            return 0
        if args.command == 'compare':
            comparison = compare_reports(load_report(args.before), load_report(args.after))
            print(json.dumps(comparison, indent=2))
            return int(bool(comparison['regressions'] or comparison['coverage_gaps'] or comparison['human_failures']))
        if args.command == 'record':
            destination = args.output or str(Path(args.report).with_suffix('.human.json'))
            record_observation(load_report(args.report), args.check_id, args.status, args.note, destination)
            print(destination)
            return 0
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == '__main__':
    sys.exit(main())
