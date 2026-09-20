"""Browsers that outlive the backend.

Playwright launches Chromium as a grandchild, and nothing Windows owns cleans
that up: stopping the server from Task Manager, from a supervisor, or by
closing its terminal left every open browser running with no parent. Measured
on one machine after a fortnight of ordinary work — twenty-nine orphaned
`chrome-headless-shell` processes, the oldest twelve days old, and 119 MB of
abandoned profile directories. The visible symptom was the web tests going red
at random, because a fresh launch could not get through its three-minute
timeout against that much competition.

The arrangement itself is proved against a real browser rather than here; what
these cover is that the pieces around it cannot quietly stop working, which is
exactly what happened the first time: a missing ctypes signature made the
whole thing fail into its own except and report only that it had not worked.
"""

import ctypes
import subprocess
import sys

import pytest

import process_group

WINDOWS = sys.platform.startswith("win")


def test_it_is_safe_to_ask_twice():
    """The lifespan hook runs once, but nothing should break if it does not."""
    first = process_group.bind_children_to_this_process()
    assert process_group.bind_children_to_this_process() == first


def test_it_says_whether_the_arrangement_is_in_place():
    process_group.bind_children_to_this_process()
    assert process_group.is_bound() == (process_group._job is not None)


@pytest.mark.skipif(not WINDOWS, reason="job objects are a Windows facility")
def test_it_works_on_this_machine():
    """Not an aspiration: if this fails here, the browsers this backend opens
    will survive it being killed, and that is worth knowing from the suite
    rather than from a machine with thirty of them on it."""
    assert process_group.bind_children_to_this_process()
    assert process_group.is_bound()


@pytest.mark.skipif(not WINDOWS, reason="job objects are a Windows facility")
def test_the_handle_signatures_are_declared():
    """The bug that made the first version a no-op: without argtypes, ctypes
    guesses `int` for a HANDLE, and GetCurrentProcess returns the pseudo-handle
    -1, which overflows that guess and raises. The failure was invisible —
    caught by the caller's except and reported as "could not"."""
    kernel32 = process_group._kernel32()
    for name in ("CreateJobObjectW", "SetInformationJobObject",
                 "AssignProcessToJobObject", "GetCurrentProcess", "CloseHandle"):
        assert getattr(kernel32, name).argtypes is not None, name
    # The pseudo-handle survives the round trip it used to fail on.
    assert kernel32.AssignProcessToJobObject.argtypes[1] is ctypes.wintypes.HANDLE


@pytest.mark.skipif(not WINDOWS, reason="job objects are a Windows facility")
def test_the_limits_struct_is_the_size_windows_expects():
    """SetInformationJobObject rejects a struct of the wrong length, and the
    layout is easy to get wrong — Affinity is pointer-sized, not a DWORD."""
    size = ctypes.sizeof(process_group._JOBOBJECT_EXTENDED_LIMIT_INFORMATION())
    assert size == (144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112)


@pytest.mark.skipif(not WINDOWS, reason="the flag is a Windows one")
def test_appium_is_started_outside_the_job(monkeypatch, tmp_path):
    """Appium takes ten seconds to come up and the backend is restarted often
    while developing; a device session does not survive that restart anyway,
    so taking Appium down with the backend would cost time and buy nothing.

    It is the one child that opts out, so the flag is checked on the call
    rather than assumed from the source.
    """
    import process_manager

    spawned = {}

    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        spawned["argv"] = argv
        spawned["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(process_manager, "_process", None)
    monkeypatch.setattr(process_manager, "_resolve_executable", lambda: "appium.cmd")
    monkeypatch.setattr(process_manager, "LOG_FILE_PATH", str(tmp_path / "appium.log"))
    monkeypatch.setattr(process_manager.subprocess, "Popen", fake_popen)

    ok, _ = process_manager.start()
    assert ok
    flags = spawned["kwargs"]["creationflags"]
    assert flags & process_group.CREATE_BREAKAWAY_FROM_JOB
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
