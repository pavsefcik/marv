"""YMLX model-family classification and hub discovery.

Mirrors the logic in ymlx's `ymlx-helpers.zsh` (`_ymlx_model_family`,
`_ymlx_thinking_spec`, `_ymlx_ministral_base`) so the agent reasons about
MLX models the same way the ymlx TUI does: thinking support and markers are
per family, and Ministral ships as an Instruct+Reasoning pair.

Kept dependency-free so both the capability registry (`models.py`) and the
provider (`ymlx.py`) can import it without cycles.
"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_HUB_DIR = Path.home() / ".cache" / "huggingface" / "hub"

# Families that can emit a reasoning trace.
_THINKING_FAMILIES = {"qwen", "gemma", "ministral-reasoning"}


def _model_name(model_id: str) -> str:
    return model_id.split("/")[-1]


def _read_model_type(hub_dir: Path, model_id: str) -> str | None:
    """Read `model_type` from the local HF snapshot config.json, if present."""
    snapshot_dir = hub_dir / f"models--{model_id.replace('/', '--')}" / "snapshots"
    if not snapshot_dir.is_dir():
        return None
    snapshots = sorted(snapshot_dir.iterdir())
    if not snapshots:
        return None
    config_file = snapshots[0] / "config.json"
    if not config_file.is_file():
        return None
    try:
        text = config_file.read_text()
    except OSError:
        return None
    m = re.search(r'"model_type"\s*:\s*"([^"]*)"', text)
    return m.group(1) if m else None


def ymlx_model_family(model_id: str, hub_dir: Path | None = None) -> str:
    """Classify a model into a thinking family.

    Returns one of: qwen | gemma | ministral-reasoning | ministral-instruct |
    lfm | generic.
    """
    base = _model_name(model_id)
    model_type = _read_model_type(hub_dir or DEFAULT_HUB_DIR, model_id)
    if model_type:
        if model_type.startswith("qwen"):
            return "qwen"
        if model_type.startswith("gemma"):
            return "gemma"
        if model_type.startswith("lfm"):
            return "lfm"
        if model_type in {"mistral3", "ministral3"}:
            if "-Reasoning-" in base:
                return "ministral-reasoning"
            return "ministral-instruct"

    if "qwen" in base.lower():
        return "qwen"
    if "gemma" in base.lower():
        return "gemma"
    if "lfm" in base.lower():
        return "lfm"
    if "ministral" in base.lower() or "mistral" in base.lower():
        reasoning = re.search(r"-[Rr]easoning-", base) is not None
        return "ministral-reasoning" if reasoning else "ministral-instruct"
    return "generic"


def ymlx_supports_thinking(model_id: str, hub_dir: Path | None = None) -> bool:
    """Whether the model can emit a reasoning trace (even display-only)."""
    return ymlx_model_family(model_id, hub_dir) in _THINKING_FAMILIES


def ymlx_thinking_spec(model_id: str, hub_dir: Path | None = None) -> tuple[str, str, bool]:
    """Return (control, markers, reasoning_first) for a model, ymlx-style.

    control: enable_thinking (template bool) | variant (id decides) | none
    markers: think | channel | bracket | none
    reasoning_first: True when the trace starts immediately (Ministral Reasoning)
    """
    spec = {
        "qwen": ("enable_thinking", "think", False),
        "gemma": ("enable_thinking", "channel", False),
        "ministral-reasoning": ("variant", "bracket", True),
        "ministral-instruct": ("variant", "none", False),
        "lfm": ("none", "think", False),
    }
    family = ymlx_model_family(model_id, hub_dir)
    return spec.get(family, ("enable_thinking", "think", False))


def ministral_sibling(model_id: str) -> str | None:
    """For a Ministral Instruct/Reasoning half, return the sibling full id."""
    base = _model_name(model_id)
    if "-Instruct-" in base:
        sib = base.replace("-Instruct-", "-Reasoning-")
    elif "-Reasoning-" in base:
        sib = base.replace("-Reasoning-", "-Instruct-")
    else:
        return None
    org = model_id.rsplit("/", 1)[0] if "/" in model_id else ""
    return f"{org}/{sib}" if org else sib


def ministral_base(model_id: str) -> str:
    """Collapse a Ministral half into its display base name."""
    base = _model_name(model_id)
    return re.sub(r"-([Ii]nstruct|[Rr]easoning)-[^-]+-", "-", base)


def discover_yaml_model_ids(hub_dir: Path | None = None) -> list[str]:
    """Enumerate ymlx-managed model ids from the local HF hub directory.

    Scans `models--org--name` folders and normalizes to `org/name`. Ministral
    Instruct+Reasoning halves are collapsed into a single logical entry (the
    Instruct half).
    """
    hub_dir = hub_dir or DEFAULT_HUB_DIR
    if not hub_dir.is_dir():
        return []
    found: set[str] = set()
    for entry in sorted(hub_dir.glob("models--*")):
        if not entry.is_dir():
            continue
        model_id = entry.name[len("models--") :].replace("--", "/", 2)
        if "-Reasoning-" in model_id:
            # Collapse to the Instruct half of a Ministral pair.
            sibling = ministral_sibling(model_id)
            if sibling and (hub_dir / f"models--{sibling.replace('/', '--')}").is_dir():
                model_id = sibling
        found.add(model_id)
    return sorted(found)


def running_model_id(base_url: str) -> str | None:
    """Return the currently loaded model id reported by a ymlx server, if any."""
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            import json

            data = json.load(resp)
        for model in data.get("data", []):
            model_id = model.get("id")
            if model.get("loaded") and isinstance(model_id, str):
                return model_id
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return None
