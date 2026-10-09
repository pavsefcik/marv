"""Behavior tests for Seam B: delegating lifecycle to the marv-mlx runtime CLI.

The provider is exercised through the CLI boundary (``MarvMlxCli``), the same
seam a real ``marv-mlx`` binary would sit behind, so no subprocess is spawned.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from marv.llm import marv_mlx, marv_mlx_cli
from marv.llm.marv_mlx import MarvMlxProvider
from marv.llm.marv_mlx_cli import MarvMlxCli, resolve_marv_mlx_binary

if TYPE_CHECKING:
    from pathlib import Path

Qwen = "mlx-community/Qwen3.5-4B-MLX-4bit"


def make_provider(tmp_path: Path, *, server_manager: str = "marv-mlx") -> MarvMlxProvider:
    return MarvMlxProvider(
        base_url="http://localhost:11500",
        api_key="",
        model=Qwen,
        hub_dir=tmp_path,
        server_command="mlx_vlm.server",
        server_manager=server_manager,
    )


class FakeCli:
    """Records lifecycle calls and returns canned query answers."""

    def __init__(
        self,
        *,
        models: list[str] | None = None,
        status: object = None,
        fail_run: bool = False,
    ) -> None:
        self.models = models or []
        self._status = status
        self.fail_run = fail_run
        self.run_calls: list[str] = []
        self.stop_calls: list[str | None] = []

    def list_models(self) -> list[str]:
        return list(self.models)

    def status(self) -> object:
        return self._status

    def info(self, model: str) -> object:
        return None

    def run(self, model: str) -> None:
        self.run_calls.append(model)
        if self.fail_run:
            from marv.llm.marv_mlx_cli import MarvMlxCliError

            raise MarvMlxCliError("boom")

    def stop(self, model: str | None = None) -> None:
        self.stop_calls.append(model)


def test_uses_runtime_cli_only_when_binary_present(monkeypatch, tmp_path):
    monkeypatch.setattr(marv_mlx, "resolve_marv_mlx_binary", lambda: None)
    provider = make_provider(tmp_path)
    assert provider.uses_runtime_cli is False

    monkeypatch.setattr(marv_mlx, "resolve_marv_mlx_binary", lambda: "/usr/local/bin/marv-mlx")
    provider = make_provider(tmp_path)
    assert provider.uses_runtime_cli is True


def test_embedded_mode_never_uses_the_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(marv_mlx, "resolve_marv_mlx_binary", lambda: "/usr/local/bin/marv-mlx")
    provider = make_provider(tmp_path, server_manager="embedded")
    assert provider.uses_runtime_cli is False


def test_list_models_uses_runtime_cli_when_configured(monkeypatch, tmp_path):
    provider = make_provider(tmp_path)
    fake = FakeCli(models=[Qwen, "mlx-community/gemma-4-12B"])
    provider._cli = fake  # type: ignore[assignment]

    assert provider.list_models.__self__ is provider
    import asyncio

    models = asyncio.run(provider.list_models())
    assert models == sorted([Qwen, "mlx-community/gemma-4-12B"])


def test_ensure_running_delegates_to_the_runtime_run(monkeypatch, tmp_path):
    provider = make_provider(tmp_path)
    fake = FakeCli()
    provider._cli = fake  # type: ignore[assignment]
    # Make is_serving() report "not serving" via the embedded running-model probe.
    monkeypatch.setattr(marv_mlx, "running_model_id", lambda _url: None)

    started = provider.ensure_running()

    assert started is True
    assert fake.run_calls == [Qwen]


def test_ensure_running_falls_back_to_embedded_when_cli_fails(monkeypatch, tmp_path):
    provider = make_provider(tmp_path)
    fake = FakeCli(fail_run=True)
    provider._cli = fake  # type: ignore[assignment]
    monkeypatch.setattr(marv_mlx, "running_model_id", lambda _url: None)

    fallback_calls: list[str] = []

    def fake_embedded(self) -> bool:
        fallback_calls.append(self.model)
        return True

    monkeypatch.setattr(marv_mlx.LocalServerProvider, "ensure_running", fake_embedded)

    assert provider.ensure_running() is True
    assert fallback_calls == [Qwen]


def test_stop_delegates_to_the_runtime_cli(monkeypatch, tmp_path):
    provider = make_provider(tmp_path)
    fake = FakeCli()
    provider._cli = fake  # type: ignore[assignment]

    provider.stop()

    assert fake.stop_calls == [None]


def test_resolve_binary_prefers_explicit_path():
    assert resolve_marv_mlx_binary("/opt/custom/marv-mlx") == "/opt/custom/marv-mlx"


# ---- CLI client behavior (subprocess boundary faked) -----------------------


def _completed(
    stdout: str, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["marv-mlx"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_cli_client_parses_status_json(monkeypatch):
    cli = MarvMlxCli("/usr/local/bin/marv-mlx")
    payload = {"model": Qwen, "port": 11500, "pid": 4242, "base_url": "http://127.0.0.1:11500/v1"}
    monkeypatch.setattr(cli, "_run", lambda args, timeout: _completed(json.dumps(payload)))

    status = cli.status()

    assert status is not None
    assert status.model == Qwen
    assert status.port == 11500
    assert status.pid == 4242


def test_cli_client_status_is_none_when_idle(monkeypatch):
    cli = MarvMlxCli("/usr/local/bin/marv-mlx")
    monkeypatch.setattr(cli, "_run", lambda args, timeout: _completed("null", returncode=1))

    assert cli.status() is None


def test_cli_client_run_raises_on_failure(monkeypatch):
    import pytest

    from marv.llm.marv_mlx_cli import MarvMlxCliError

    cli = MarvMlxCli("/usr/local/bin/marv-mlx")
    monkeypatch.setattr(
        marv_mlx_cli.subprocess,
        "run",
        lambda *a, **k: _completed("", returncode=1, stderr="no model"),
    )

    with pytest.raises(MarvMlxCliError, match="no model"):
        cli.run(Qwen)
