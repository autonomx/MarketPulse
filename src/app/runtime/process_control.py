"""Private parent-pipe shutdown control shared by local process launchers."""
from __future__ import annotations

import sys
import time


def wait_for_parent_control() -> None:
    """Return after a line arrives or the private stdin pipe closes."""
    # sys.platform (not os.name) lets type checkers skip the Windows branch.
    if sys.platform != "win32":
        sys.stdin.readline()
        return
    # Avoid a blocking CRT read while native extensions load on another thread.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    peek.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    while True:
        available = wintypes.DWORD()
        if not peek(handle, None, 0, None, ctypes.byref(available), None) or available.value:
            return
        time.sleep(0.2)


__all__ = ["wait_for_parent_control"]

