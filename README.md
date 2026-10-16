# Hardware checks with pytest

A small **pytest plugin**, plus an independent [Yoga Book suite](checks/yogabook/README.md). The framework does not import suites, register device tests, select a board, or define GPS/camera/audio options.

- `src/ybcheck/`: generic shell collection, bounded command fixtures, result metadata and report utilities.
- `checks/yogabook/`: ordinary Python/shell checks, board-local helpers and `conftest.py` options.
- `tests/`: deterministic regression tests for the framework and suite parsers; no real hardware required.

Reuses [pytest](https://docs.pytest.org/en/stable/) for discovery, assertions, parametrization, fixtures, selection and exit codes, and [pytest-json-report](https://github.com/numirias/pytest-json-report) for raw results, tracebacks and machine-readable evidence. There is no assertion DSL or central test registry. Other pytest plugins, including testinfra, can be used normally.

## Install and run

Python 3.10 or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .

# Supply any suite; the framework has no built-in default suite.
.venv/bin/yb-check list checks/yogabook
.venv/bin/yb-check run checks/yogabook --report reports/baseline.json -v
.venv/bin/yb-check report reports/baseline.json
```

Or invoke pytest directly; installing the package registers the plugin:

```sh
.venv/bin/python -m pytest checks/yogabook -v --json-report \
  --json-report-file=reports/baseline.json
```

`run` and `list` forward unknown arguments to pytest, including paths, `-k`, `-m`, and suite-defined options. Without paths, normal pytest discovery/configuration applies. `list --json` emits collected metadata to standard output without executing test bodies. `run` refuses to overwrite an existing report.

## Assertion contract

| Result | Ordinary Python | Standalone shell exit | Meaning |
|---|---|---:|---|
| `pass` | Assertions succeed | `0` | The stated assertion was exercised successfully. |
| `fail` | Test-body `assert` or `pytest.fail(...)` | `1` | An exercised requirement was not met. |
| `error` | Unexpected exception or fixture failure | Other nonzero codes | The check itself could not complete correctly. |
| `warn` | `warnings.warn(message, HardwareWarning)` | `79` | Non-fatal limitation; **not a clean pass**. |
| `blocked` | `pytest.skip("blocked: reason")` | `78` | A prerequisite, permission or exclusive device access is unavailable. |
| `skip` | Other `pytest.skip(...)` | `77` | Explicit opt-out, attachment unavailable or intentionally inapplicable. |
| `pending` | `pytest.skip("human pending: instructions")` or `human` fixture | — | A physical observation has not been recorded. |

Raw pytest outcomes remain unchanged: warnings do not fail pytest, and skipped/blocked/pending checks do not make pytest return nonzero. Automation must inspect `ybcheck.summary`/individual statuses if it requires complete coverage; **exit zero alone does not certify hardware**. Python setup/teardown errors remain errors even if the test body passed. `-W error` retains normal pytest warning escalation.

Import the warning category with `from ybcheck.plugin import HardwareWarning`; ordinary unrelated Python warnings retain normal pytest behavior.

### Python checks

Use normal pytest functions. Metadata adds stable identity and independent evidence layers; it does not implement assertions:

```python
import pytest

@pytest.mark.hardware(id="system.kernel.query", component="system", layer="headless")
def test_kernel_query(shell, evidence):
    result = shell("uname", "-s", timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Linux"
    evidence("reported_system", result.stdout.strip())
```

The `layer` is a suite-defined string. The Yoga Book suite uses `enumeration`, `headless`, `userspace` and `human`. Each layer is a separate check: a pending desktop observation must not downgrade a successfully exercised kernel interface. Duplicate stable IDs fail collection. Unmarked Python tests still run normally and retain their ordinary pytest JSON results, but do not appear in the layered catalogue.

Fixtures:

- `shell(*argv, timeout=10, check=False, input=None)`: `subprocess.CompletedProcess`, text stdout/stderr; arguments are **not** interpreted by a shell. Explicitly use `shell("sh", "-c", script)` when needed. Commands and output become evidence. Timeouts terminate/reap the owned process group, not unrelated processes.
- `evidence(key, value)`: attach JSON-serializable diagnostic evidence.
- `require_program(name)` / `require_root()`: skip as blocked when prerequisites are missing.
- `human`: consume an explicitly supplied observation for the marked check, otherwise pending.

Use pytest fixtures/context managers and `try/finally` for test-specific restoration. Do not swallow failed restoration. `@pytest.mark.active("group")` requires `--active group` or `--active all`; group names belong to the suite. Marking is a safety convention, **not a sandbox**. Hardware suites must run serially; xdist parallel workers are rejected when hardware checks are collected.

### Shell checks

Any `test_*.sh` is collected as a complete program. An optional JSON comment in the first 25 lines supplies metadata and timeout; without it, ID derives from the file path, component is `custom`, layer is `headless`, and timeout is 10 seconds.

```sh
#!/bin/sh
# ybcheck: {"id":"system.process_status","component":"system","layer":"headless","timeout":5}
[ -r /proc/self/status ] || { echo "process status interface is unreadable"; exit 1; }
cat /proc/self/status || exit 2
exit 0
```

The shebang chooses the interpreter; absent a shebang, `/bin/sh` is used. Shell code needs no framework imports, helper functions or executable bit: `sh test_process_status.sh` also runs it directly. Stdout/stderr are diagnostics, not parsed assertion syntax. Add `"active":"group"` for opt-in checks. Timeout must be positive and at most 600 seconds. Shell exits 2 (syntax/usage errors), 126/127 (execution failures), signals and timeouts are errors, never warnings.

## Reports, comparison and observations

The normal pytest-json-report document gains a `ybcheck` section with schema version, run/boot/kernel/machine identity, collected catalogue, selected flags, per-check results/evidence, and status counts. Raw pytest test outcomes and failures remain available alongside it.

```sh
.venv/bin/yb-check compare reports/before.json reports/after.json
.venv/bin/yb-check record reports/after.json human.display.image pass \
  --note 'Observed stable image and correct rotation on the physical panel'
.venv/bin/yb-check report reports/after.json \
  --human-results reports/after.human.json
```

`compare` reports automatic regressions, lost coverage, new/changed checks, pending human checks and failed human observations separately. Regression, lost coverage or an explicitly failed human check returns 1; pending human acceptance alone does not. Missing/skipped checks cannot masquerade as maintained passes. Reports from different machines are rejected. Human passes are never carried to a new boot automatically.

`record` creates a **separate observation file**; it never rewrites historical pytest outcomes or permits an automatic check to be manually greened. Observations include a note, time, recorder and source run; revisions retain history. They are bound to boot ID, kernel and machine. Explicit `--human-results FILE` on a subsequent pytest invocation of the same boot can assert those observations; different-boot files are rejected as blocked. Do not record `pass` without actually observing the stated behavior.

## Development

```sh
.venv/bin/python -m pytest
```

Local reports, raw captures, traces, venvs and build outputs are ignored. Reports can contain hostnames, MAC addresses, SSIDs, command output and location evidence; review/redact before sharing. Do not commit proprietary firmware or runtime binaries.
