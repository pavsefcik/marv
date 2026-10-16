"""Rename a marv-launched Python model server to the model it is running.

Python auto-imports any ``sitecustomize`` found on ``sys.path`` at interpreter
startup. marv puts this directory on ``PYTHONPATH`` and sets
``MARV_PROCTITLE=<model-id>`` when it launches a Python model server, so the
server process shows the model name in Activity Monitor / ``ps`` instead of a
generic "Python".

Deliberately best-effort: if ``setproctitle`` is not importable in the server's
interpreter the process is left with its default name and nothing else changes.

This hook also chain-loads the next ``sitecustomize`` on ``sys.path``: Python
imports only the first one it finds, and this directory sits on ``PYTHONPATH``
ahead of site-packages, so without chaining it silently shadows any
``sitecustomize`` installed in the server's environment — notably the marv-mlx
APC (prefix-cache) defaults installed into the mlx-vlm tool env's
site-packages, whose loss quietly disabled prefix caching for every
marv-launched server (``cached_tokens=0`` on every turn, full re-prefill of
the conversation each time).
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


def _chain_sitecustomize() -> None:
    """Execute the first ``sitecustomize.py`` from another ``sys.path`` entry."""
    import importlib.util
    import sys
    from pathlib import Path

    try:
        here = Path(__file__).resolve().parent
    except Exception:  # noqa: BLE001 - never fail interpreter startup
        return
    for entry in list(sys.path):
        if not entry:
            continue
        try:
            if Path(entry).resolve() == here:
                continue
            candidate = Path(entry) / "sitecustomize.py"
            if not candidate.is_file() or candidate.resolve() == here / "sitecustomize.py":
                continue
        except Exception:  # noqa: BLE001 - unreadable path entries are skipped
            continue
        try:
            spec = importlib.util.spec_from_file_location("_marv_chained_sitecustomize", candidate)
            if spec is not None and spec.loader is not None:
                spec.loader.exec_module(importlib.util.module_from_spec(spec))
        except Exception:  # noqa: BLE001 - a broken foreign hook must not break startup
            pass
        return  # first other sitecustomize found — chained or not, done


_chain_sitecustomize()
