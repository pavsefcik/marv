"""Version check against GitHub releases and self-update.

marv is distributed as GitHub release wheels (installed through ``uv tool``).
The running harness has no idea a newer release exists unless it asks, so this
module compares the local version with the latest release and can reinstall
through the same requirement the installer uses.

Everything here is delivery-level: no runtime or provider imports. The network
call is injected (``fetcher``) so the logic is testable without a socket, and a
missing network, rate limit, or malformed response always degrades to "no
update" rather than blocking startup.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: Releases API for the upstream repository.
RELEASES_API = "https://api.github.com/repos/pavsefcik/marv/releases/latest"

#: How long a check result is trusted before asking GitHub again.
CHECK_INTERVAL_SECONDS = 24 * 60 * 60

#: Network timeout for the release probe (startup must never stall on this).
FETCH_TIMEOUT_SECONDS = 2.0

_VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)*)")


def parse_version(text: str) -> tuple[int, ...]:
    """Parse a dotted version into an integer tuple.

    Tags may carry a leading ``v`` and a suffix (``1.2.0-rc1``); only the
    leading dotted numeric run is compared. Unparseable text sorts as ``()``.
    """
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(remote: str, current: str) -> bool:
    """True when ``remote`` is a strictly newer version than ``current``."""
    remote_parts = parse_version(remote)
    current_parts = parse_version(current)
    if not remote_parts or not current_parts:
        return False
    return remote_parts > current_parts


@dataclass(frozen=True, slots=True)
class Release:
    """A published release, reduced to what an update needs."""

    version: str
    tag: str
    wheel_url: str


def default_cache_path() -> Path:
    """Where the last check result is remembered."""
    return Path.home() / ".cache" / "marv" / "update_check.json"


def release_from_api(payload: object) -> Release | None:
    """Extract a :class:`Release` from a GitHub release JSON payload."""
    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        return None
    version = tag.lstrip("vV")
    wheel_url = _wheel_url_from_assets(payload.get("assets")) or _default_wheel_url(tag, version)
    if not wheel_url:
        return None
    return Release(version=version, tag=tag, wheel_url=wheel_url)


def _wheel_url_from_assets(assets: object) -> str | None:
    """Pick the marv wheel download URL from a release's asset list."""
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if (
            isinstance(name, str)
            and name.startswith("marv-")
            and name.endswith(".whl")
            and isinstance(url, str)
            and url
        ):
            return url
    return None


def _default_wheel_url(tag: str, version: str) -> str:
    """Construct the conventional wheel URL when the asset list is unusable."""
    return (
        f"https://github.com/pavsefcik/marv/releases/download/{tag}/marv-{version}-py3-none-any.whl"
    )


def fetch_latest_release(timeout: float = FETCH_TIMEOUT_SECONDS) -> Release | None:
    """Fetch the latest release from GitHub, or None on any failure."""
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "marv-update-check",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return release_from_api(payload)


@dataclass(slots=True)
class UpdateCache:
    """The remembered result of the last successful probe."""

    last_checked: float = 0.0
    latest: Release | None = None


def load_cache(path: Path) -> UpdateCache:
    """Load the cached probe result (empty when absent or corrupt)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UpdateCache()
    if not isinstance(data, dict):
        return UpdateCache()
    last_checked = data.get("last_checked")
    latest_data = data.get("latest")
    latest = None
    if isinstance(latest_data, dict):
        version = latest_data.get("version")
        tag = latest_data.get("tag")
        wheel_url = latest_data.get("wheel_url")
        if (
            isinstance(version, str)
            and isinstance(tag, str)
            and isinstance(wheel_url, str)
            and version
            and tag
            and wheel_url
        ):
            latest = Release(version=version, tag=tag, wheel_url=wheel_url)
    return UpdateCache(
        last_checked=float(last_checked) if isinstance(last_checked, (int, float)) else 0.0,
        latest=latest,
    )


def save_cache(path: Path, cache: UpdateCache) -> None:
    """Persist the probe result; failures are swallowed (best-effort)."""
    data: dict[str, object] = {"last_checked": cache.last_checked}
    if cache.latest is not None:
        data["latest"] = {
            "version": cache.latest.version,
            "tag": cache.latest.tag,
            "wheel_url": cache.latest.wheel_url,
        }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        return


def check_for_update(
    current: str,
    *,
    cache_path: Path | None = None,
    now: float | None = None,
    interval: int = CHECK_INTERVAL_SECONDS,
    force: bool = False,
    fetcher: Callable[[], Release | None] = fetch_latest_release,
) -> Release | None:
    """Return the newer release when one exists, else None.

    The cached answer is reused inside ``interval`` so a normal startup costs
    no network. ``force`` bypasses the cache (used by ``marv update --check``).
    A failed probe leaves the cache untouched so a later run can retry.
    """
    path = cache_path if cache_path is not None else default_cache_path()
    moment = time.time() if now is None else now
    cache = load_cache(path)

    if not force and cache.last_checked > 0 and moment - cache.last_checked < interval:
        if cache.latest is not None and is_newer(cache.latest.version, current):
            return cache.latest
        return None

    release = fetcher()
    if release is None:
        return None
    save_cache(path, UpdateCache(last_checked=moment, latest=release))
    return release if is_newer(release.version, current) else None


@dataclass(frozen=True, slots=True)
class InstallOutcome:
    """Result of an installer invocation."""

    ok: bool
    returncode: int
    stderr: str


def install_release(
    release: Release,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> InstallOutcome:
    """Reinstall marv from the release wheel via ``uv tool install --force``.

    This mirrors what ``install.sh`` does. The running process keeps the old
    code, so callers must tell the user to restart marv afterwards.
    """
    return install_requirement(f"marv @ {release.wheel_url}", runner=runner)


def install_requirement(
    requirement: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> InstallOutcome:
    """Install an arbitrary ``uv tool`` requirement and report the outcome."""
    argv: Sequence[str] = [
        "uv",
        "tool",
        "install",
        "--force",
        "--python",
        "3.14",
        requirement,
    ]
    try:
        completed = runner(list(argv), check=False, capture_output=True, text=True)
    except OSError as exc:
        return InstallOutcome(ok=False, returncode=1, stderr=str(exc))
    return InstallOutcome(
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        stderr=(completed.stderr or "").strip(),
    )
