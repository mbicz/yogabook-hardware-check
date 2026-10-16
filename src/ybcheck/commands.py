"""Bounded local commands; no implicit shell and no unrelated process termination."""

import os
import signal
import subprocess


class CommandTimeout(RuntimeError):
    """The owned process group exceeded its deadline."""


def terminate_group(process, grace=2):
    """Reap the owner and terminate descendants, even if the leader has exited."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate()


def run_command(*argv, timeout=10, check=False, input=None):
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if isinstance(input, bytes):
        input = input.decode()
    process = subprocess.Popen(
        [str(arg) for arg in argv], stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = terminate_group(process)
        raise CommandTimeout(f"Command timed out after {timeout}s: {argv!r}\n{stdout}\n{stderr}") from exc
    except BaseException:
        terminate_group(process)
        raise
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result
