"""Tie the browsers this backend starts to this backend's own lifetime.

Playwright launches Chromium as a grandchild — the backend spawns a Node
driver, the driver spawns the browser — and neither is a child of anything
Windows will clean up. The FastAPI shutdown hook closes them on a graceful
exit, but nothing does on a hard one: stopping the server from Task Manager,
from a supervisor, or by closing the terminal it was started in leaves every
open browser running with no parent.

Measured on this machine after a fortnight of ordinary work: twenty-nine
orphaned `chrome-headless-shell` processes, the oldest twelve days old, and
119 MB of abandoned profile directories in the temp folder. They are not idle
— each holds a GPU process and its share of memory — and the visible symptom
was the web tests going red at random, because a fresh `BrowserType.launch`
could not get through within its three-minute timeout.

The fix is the one Windows provides for exactly this: a Job Object with
KILL_ON_JOB_CLOSE. Every process the backend starts joins the job, and when
the backend's last handle to it goes — which happens however the process dies,
including a kill it never sees — Windows terminates the rest of the job.

BREAKAWAY_OK is set alongside it so a process that should outlive the backend
can opt out. The Appium server does: it takes ten seconds to come up, the
backend is restarted often while developing, and a device session does not
survive the restart anyway, so there is nothing to be gained by taking Appium
down with it.

Nothing here raises. A machine that will not create a job object is a machine
the backend should still start on — it simply keeps the old behaviour.
"""

import ctypes
import sys
from ctypes import wintypes
from typing import Optional

IS_WINDOWS = sys.platform.startswith("win")

# CreateProcess flag for a child that is to be left out of the job. Exposed
# here so the one caller that needs it does not have to know the number.
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

# Held for the life of the process on purpose: the job dies with its last
# handle, so dropping this would undo the whole arrangement.
_job: Optional[int] = None


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    # Affinity is a ULONG_PTR, which is c_size_t here — pointer-sized, so the
    # struct is 144 bytes on x64 and SetInformationJobObject accepts it.
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32():
    """kernel32 with the signatures spelled out.

    Without argtypes, ctypes guesses `int` for a HANDLE, and
    `GetCurrentProcess()` returns the pseudo-handle -1 — which as an unsigned
    64-bit handle overflows that guess and raises. The whole arrangement then
    failed silently into the caller's except, reporting only that it had not
    worked.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def bind_children_to_this_process() -> bool:
    """Put this process, and so everything it starts, into a job that dies
    with it. Returns whether the arrangement is in place.

    Safe to call more than once; the second call is a no-op. On anything but
    Windows this does nothing and says so: there, Playwright's own signal
    handling already closes the browsers on SIGTERM and SIGINT.
    """
    global _job
    if not IS_WINDOWS:
        return False
    if _job is not None:
        return True

    try:
        kernel32 = _kernel32()
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False

        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
        if not kernel32.SetInformationJobObject(
            job, _JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits), ctypes.sizeof(limits),
        ):
            kernel32.CloseHandle(job)
            return False

        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            # Windows 8 and up allow nested jobs, so this is rare — a
            # sandbox or a service wrapper that forbids them. Nothing is
            # broken by it; the browsers simply outlive a hard kill as before.
            kernel32.CloseHandle(job)
            return False

        _job = job
        return True
    except Exception:  # noqa: BLE001
        return False


def is_bound() -> bool:
    """Whether the browsers this backend starts will die with it."""
    return _job is not None
