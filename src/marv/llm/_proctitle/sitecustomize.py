"""Rename a marv-launched Python model server to the model it is running.

Python auto-imports any ``sitecustomize`` found on ``sys.path`` at interpreter
startup. marv puts this directory on ``PYTHONPATH`` and sets
``MARV_PROCTITLE=<model-id>`` when it launches a Python model server, so the
server process shows the model name in Activity Monitor / ``ps`` instead of a
generic "Python".

Deliberately best-effort: if ``setproctitle`` is not importable in the server's
interpreter the process is left with its default name and nothing else changes.
"""

import os

_title = os.environ.get("MARV_PROCTITLE")
if _title:
    try:
        from setproctitle import setproctitle as _setproctitle  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - optional dependency, never fail startup
        pass
    else:
        _setproctitle(_title)
