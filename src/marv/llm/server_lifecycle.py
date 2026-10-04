"""Best-effort teardown of the marv-mlx model server when marv exits or dies.

The local model server is launched detached (so it survives automation), which
means marv is responsible for unloading it. Clean shutdown goes through
``MarvMlxProvider.close()``; this module adds the safety net for exits that skip
normal teardown:

* ``atexit`` — normal interpreter exit, including unhandled exceptions;
* ``SIGTERM`` / ``SIGHUP`` — ``kill`` or closing the terminal window.

``SIGKILL`` cannot be intercepted, so a ``kill -9`` still leaves the server
running.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

_stop_callbacks: list[Callable[[], None]] = []
_lock = threading.Lock()
_atexit_installed = False
_signals_installed = False


def install_handlers() -> None:
    """Install the atexit hook and signal handlers.

    Signal handlers can only be installed from the main thread; calling this
    from elsewhere is a no-op for signals (the atexit hook still applies).
    """
    global _atexit_installed, _signals_installed

    with _lock:
        install_atexit = not _atexit_installed
        _atexit_installed = True
        install_signals = not _signals_installed

    if install_atexit:
        atexit.register(stop_all)

    if install_signals and _try_install_signals():
        with _lock:
            _signals_installed = True


def register_stop(callback: Callable[[], None]) -> None:
    """Register a teardown callback to run if marv exits without cleanup."""
    with _lock:
        if callback not in _stop_callbacks:
            _stop_callbacks.append(callback)
    install_handlers()


def unregister_stop(callback: Callable[[], None]) -> None:
    """Remove a previously registered teardown callback."""
    with _lock:
        if callback in _stop_callbacks:
            _stop_callbacks.remove(callback)


def stop_all() -> None:
    """Run every registered teardown callback once, swallowing failures."""
    with _lock:
        callbacks = list(_stop_callbacks)
        _stop_callbacks.clear()
    for callback in callbacks:
        with contextlib.suppress(Exception):
            callback()


def _try_install_signals() -> bool:
    installed = False
    for signum in (signal.SIGTERM, signal.SIGHUP):
        try:
            previous = signal.getsignal(signum)
            signal.signal(signum, _make_handler(signum, previous))
        except (ValueError, OSError):
            # Not on the main thread, or the signal is unsupported here.
            continue
        installed = True
    return installed


def _make_handler(signum: int, previous: Any) -> Callable[[int, Any], None]:
    def handler(received: int, frame: Any) -> None:
        stop_all()
        if callable(previous) and previous not in (signal.SIG_IGN, signal.SIG_DFL):
            previous(received, frame)
            return
        signal.signal(received, signal.SIG_DFL)
        os.kill(os.getpid(), received)

    return handler
