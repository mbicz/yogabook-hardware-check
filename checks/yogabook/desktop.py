"""Read-only queries in the active graphical user's session."""
import os

import pytest

from checks.yogabook.pen import graphical_session


def session_bus_call(shell, session, *argv, timeout=10):
    environment = ["env", f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{session['uid']}/bus",
                   f"XDG_RUNTIME_DIR=/run/user/{session['uid']}"]
    if os.geteuid() == session["uid"]:
        return shell(*environment, *argv, timeout=timeout)
    if os.geteuid() != 0:
        pytest.skip(f"blocked: run as root or as session user {session['user']} to query its desktop")
    return shell("runuser", "-u", session["user"], "--", *environment, *argv, timeout=timeout)


@pytest.fixture
def desktop_session(shell, require_program, evidence):
    loginctl = require_program("loginctl")
    listed = shell(loginctl, "list-sessions", "--no-legend", timeout=5)
    if listed.returncode and "Failed to connect to bus" in listed.stderr:
        pytest.skip(f"blocked: cannot query logind: {listed.stderr.strip()}")
    assert listed.returncode == 0, listed.stderr
    blocks = []
    for line in listed.stdout.splitlines():
        session_id = line.split()[0] if line.split() else None
        if session_id:
            shown = shell(loginctl, "show-session", session_id, "-p", "User", "-p", "Name", "-p", "Type",
                          "-p", "Class", "-p", "Active", "-p", "Seat", timeout=5)
            if shown.returncode == 0:
                blocks.append(shown.stdout)
    session = graphical_session(blocks)
    evidence("graphical_session", session)
    if session is None:
        pytest.skip("blocked: no active graphical user session; log in to GNOME for desktop checks")
    if os.geteuid() == 0 and os.geteuid() != session["uid"]:
        require_program("runuser")
    return session
