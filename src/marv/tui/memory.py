"""Host memory readings for the status bar (macOS / Apple Silicon).

The values come from read-only macOS helpers (``sysctl``, ``vm_stat``, ``ps``)
rather than a third-party dependency: marv already shells out to ``ps``/``lsof``
for server lifecycle, and this keeps the runtime footprint small.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

_PAGE_SIZE_RE = re.compile(r"page size of (\d+) bytes")
_VM_STAT_RE = re.compile(r"^(?:Pages )?([A-Za-z ]+):\s+(\d+)\.", re.MULTILINE)
_TOP_MEM_ROW_RE = re.compile(r"^\s*(\d+)\s+([\d.]+)([KMG])\s*$")
_MEM_MULTIPLIER = {"K": 1024, "M": 1024**2, "G": 1024**3}
# Pages a running system can hand back without swapping: free, reclaimable
# file cache (inactive/speculative) and pages the kernel may evict.
_RECLAIMABLE = ("free", "inactive", "speculative", "purgeable")


def _run(argv: list[str]) -> str:
    """Run a read-only helper, returning stdout (empty string on any failure)."""
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout


def _read_rss_bytes(pid: int) -> int:
    """Resident size of a single `pid` in bytes (0 when unreadable)."""
    out = _run(["ps", "-o", "rss=", "-p", str(pid)]).strip()
    return int(out) * 1024 if out.isdigit() else 0


def read_total_bytes() -> int:
    """Installed physical memory in bytes, or 0 when it cannot be read."""
    out = _run(["sysctl", "-n", "hw.memsize"]).strip()
    return int(out) if out.isdigit() else 0


def read_available_bytes() -> int:
    """Memory available to new allocations, in bytes (0 when unreadable)."""
    out = _run(["vm_stat"])
    if not out:
        return 0
    page_match = _PAGE_SIZE_RE.search(out)
    page_size = int(page_match.group(1)) if page_match else 4096
    pages = {name.strip(): int(count) for name, count in _VM_STAT_RE.findall(out)}
    return sum(pages.get(name, 0) for name in _RECLAIMABLE) * page_size


def _process_table() -> list[tuple[int, int, int]]:
    """Rows of ``(pid, ppid, rss_bytes)`` for every process, or [] on failure."""
    rows: list[tuple[int, int, int]] = []
    for line in _run(["ps", "-ax", "-o", "pid=,ppid=,rss="]).splitlines():
        parts = line.split()
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            rows.append((int(parts[0]), int(parts[1]), int(parts[2]) * 1024))
    return rows


def read_process_footprints() -> dict[int, int]:
    """Physical memory footprint per pid, from `top`.

    Unlike `ps` RSS, the footprint includes memory wired to the GPU (Metal),
    which is where MLX model weights live — the same figure Activity Monitor
    shows. Returns {} when `top` is unavailable.
    """
    out = _run(["top", "-l", "1", "-stats", "pid,mem"])
    footprints: dict[int, int] = {}
    for line in out.splitlines():
        match = _TOP_MEM_ROW_RE.match(line)
        if match:
            pid, value, unit = match.groups()
            footprints[int(pid)] = int(float(value) * _MEM_MULTIPLIER[unit])
    return footprints


def _tree_total(
    memory_by_pid: dict[int, int],
    children: dict[int, list[int]],
    roots: list[int],
) -> int:
    """Sum `memory_by_pid` over the trees rooted at `roots`, counted once."""
    total = 0
    seen: set[int] = set()
    stack = list(roots)
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        total += memory_by_pid.get(pid, 0)
        stack.extend(children.get(pid, ()))
    return total


def _tree_children(rows: list[tuple[int, int, int]]) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for pid, ppid, _ in rows:
        children.setdefault(ppid, []).append(pid)
    return children


def read_process_tree_memory(root_pid: int) -> int:
    """Physical memory footprint of `root_pid` and every process below it.

    Falls back to `ps` RSS when the footprint table is unavailable. Footprints
    are preferred because they include GPU-wired model weights.
    """
    if root_pid <= 0:
        return 0
    rows = _process_table()
    if not rows:
        return _read_rss_bytes(root_pid)
    footprints = read_process_footprints()
    memory_by_pid = footprints if footprints else {pid: rss for pid, _, rss in rows}
    return _tree_total(memory_by_pid, _tree_children(rows), [root_pid])


def format_bytes(num_bytes: int) -> str:
    """Compact human-readable size, e.g. ``6.4G`` or ``512M``."""
    if num_bytes >= 1024**3:
        return f"{num_bytes / 1024**3:.1f}G"
    return f"{num_bytes / 1024**2:.0f}M"


@dataclass(frozen=True, slots=True)
class MemoryUsage:
    """A host memory snapshot for the status bar."""

    marv: int
    available: int
    total: int

    def label(self) -> str:
        """Render as ``RAM marv/total (available free)``."""
        marv = format_bytes(self.marv)
        total = format_bytes(self.total)
        available = format_bytes(self.available)
        return f"RAM {marv}/{total} ({available} free)"


def snapshot(server_pid: int | None = None) -> MemoryUsage:
    """Read host memory, attributing marv's process tree to ``marv``.

    `server_pid` is included even when the model server is not a child of this
    process (e.g. a server started outside marv); the shared tree walk avoids
    double-counting it when it is.
    """
    roots = [os.getpid()]
    if server_pid is not None and server_pid > 0:
        roots.append(server_pid)

    rows = _process_table()
    if not rows:
        marv = sum(_read_rss_bytes(pid) for pid in roots)
    else:
        # Footprints are preferred: they include GPU-wired model weights.
        footprints = read_process_footprints()
        memory_by_pid = footprints if footprints else {pid: rss for pid, _, rss in rows}
        marv = _tree_total(memory_by_pid, _tree_children(rows), roots)
    return MemoryUsage(
        marv=marv,
        available=read_available_bytes(),
        total=read_total_bytes(),
    )
