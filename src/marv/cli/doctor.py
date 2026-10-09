"""`marv doctor` — one command that explains a broken setup.

Setup problems otherwise surface as a cryptic TUI error (an empty model list, a
server that never answers, an extension that silently failed to load). Each
check here is independent, prints a one-line verdict, and never raises: the
point is to make the failure self-diagnosing, not to fix it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from marv.llm.marv_mlx import resolve_max_output_tokens, resolve_mlx_server_command
from marv.llm.marv_mlx_cli import resolve_marv_mlx_binary
from marv.llm.mlx_models import discover_model_ids, running_model_id
from marv.llm.model_download import hub_model_present, is_hub_repo_id, resolve_download_interpreter
from marv.llm.server_process import listener_pid, server_reachable

if TYPE_CHECKING:
    from collections.abc import Callable

    from marv.config import Config
    from marv.llm.provider import LLMProvider

#: Status glyphs, kept ASCII-free only where the terminal is known UTF-8.
OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One diagnosis: a name, a verdict, and a human explanation."""

    name: str
    status: str
    detail: str


@dataclass(slots=True)
class DoctorReport:
    """The full set of checks for one run."""

    results: list[CheckResult]

    @property
    def failures(self) -> list[CheckResult]:
        """Checks that need attention (fail or warn)."""
        return [result for result in self.results if result.status != OK]

    def to_dict(self) -> list[dict[str, str]]:
        """Serializable view for ``--json``."""
        return [
            {"name": result.name, "status": result.status, "detail": result.detail}
            for result in self.results
        ]


def _check_config(config: Config) -> list[CheckResult]:
    """Resolved config: provider, model, tool set, approval mode."""
    results = [
        CheckResult("provider", OK, config.provider),
        CheckResult("session dir", OK, str(config.session_dir)),
    ]

    if config.model:
        results.append(CheckResult("model", OK, config.model))
    elif config.provider in ("marv-mlx", "openai-compat", "ollama"):
        results.append(
            CheckResult(
                "model",
                WARN,
                "none selected; the TUI will offer the local picker",
            )
        )
    else:
        results.append(CheckResult("model", FAIL, f"required for provider {config.provider!r}"))

    if config.read_only:
        results.append(
            CheckResult("guardrails", OK, "read-only: active tools are read, grep, find, ls")
        )
    elif config.approval_mode.value != "off":
        results.append(CheckResult("guardrails", OK, f"approval mode={config.approval_mode.value}"))
    else:
        results.append(
            CheckResult(
                "guardrails",
                WARN,
                "approval_mode=off and read_only=false: tools run unrestricted",
            )
        )
    return results


def _check_extensions(config: Config) -> CheckResult:
    """Extensions: every configured path exists and is importable as a file."""
    if not config.extensions:
        return CheckResult("extensions", OK, "none configured")

    missing = [str(path) for path in config.extensions if not path.exists()]
    if missing:
        return CheckResult(
            "extensions",
            FAIL,
            "missing: " + ", ".join(missing),
        )
    return CheckResult(
        "extensions",
        OK,
        f"{len(config.extensions)} configured (loaded on start)",
    )


def check_extensions_load(
    config: Config,
    load: Callable[[], list[str]],
) -> CheckResult:
    """Run the extension loader and report any errors it returns.

    Loading is injected so this stays testable without a runtime; a fresh
    extension host is created by the caller.
    """
    if not config.extensions:
        return CheckResult("extension load", OK, "none configured")
    try:
        errors = load()
    except Exception as exc:  # noqa: BLE001 - doctor never raises
        return CheckResult("extension load", FAIL, f"{type(exc).__name__}: {exc}")
    if errors:
        return CheckResult("extension load", FAIL, "; ".join(errors))
    return CheckResult("extension load", OK, f"{len(config.extensions)} loaded")


def _check_hub(config: Config) -> CheckResult:
    """The Hugging Face hub: is it present, and is the selected model in it?"""
    hub_dir = Path.home() / ".cache" / "huggingface" / "hub"
    if not hub_dir.is_dir():
        return CheckResult("hf hub", WARN, f"{hub_dir} does not exist yet (nothing downloaded)")

    models = discover_model_ids(hub_dir)
    if not config.model:
        return CheckResult("hf hub", OK, f"{len(models)} model(s) in {hub_dir}")
    if not is_hub_repo_id(config.model):
        return CheckResult("hf hub", OK, f"{config.model} is not a hub repo id (skipped)")
    if hub_model_present(hub_dir, config.model):
        return CheckResult("hf hub", OK, f"{config.model} is present")
    return CheckResult(
        "hf hub",
        WARN,
        f"{config.model} is not downloaded (the TUI will offer to fetch it)",
    )


def _check_download_tooling() -> CheckResult:
    """Whether a model could be downloaded at all, and by which interpreter."""
    interpreter = resolve_download_interpreter()
    if interpreter is None:
        return CheckResult(
            "download tooling",
            WARN,
            "no Python with huggingface_hub found; install mlx-vlm to enable downloads",
        )
    return CheckResult("download tooling", OK, " ".join(interpreter))


def _check_mlx_runtime(provider: LLMProvider) -> CheckResult:
    """The local MLX server: command present, reachable, and serving what we want."""
    name = getattr(provider, "name", "")
    base_url = getattr(provider, "base_url", "")
    if name != "marv-mlx" or not base_url:
        return CheckResult("mlx server", OK, "not used by the active provider")

    command = resolve_mlx_server_command()
    if command is None:
        return CheckResult(
            "mlx server",
            FAIL,
            "mlx_vlm.server not found; install it with "
            "`uv tool install mlx-vlm --with jinja2 --with setproctitle`",
        )

    if not server_reachable(base_url):
        return CheckResult("mlx server", OK, f"not running yet ({base_url})")

    served = running_model_id(base_url)
    pid = listener_pid(base_url)
    where = f"{base_url} (pid {pid})" if pid else base_url
    if served and getattr(provider, "model", ""):
        if served == provider.model:
            return CheckResult("mlx server", OK, f"serving {served} at {where}")
        return CheckResult(
            "mlx server",
            WARN,
            f"serving {served}, but the selected model is {provider.model}; "
            "the next request will swap it",
        )
    return CheckResult("mlx server", OK, f"running at {where}")


def _check_runtime_cli(config: Config) -> CheckResult:
    """Whether lifecycle delegation to the `marv-mlx` CLI would work."""
    if config.server_manager != "marv-mlx":
        return CheckResult("server manager", OK, "embedded (marv launches mlx_vlm.server)")
    binary = resolve_marv_mlx_binary()
    if binary is None:
        return CheckResult(
            "server manager",
            WARN,
            "configured marv-mlx, but the marv-mlx binary is missing; falling back to embedded",
        )
    return CheckResult("server manager", OK, f"marv-mlx ({binary})")


def _check_output_budget(config: Config) -> CheckResult:
    """The clamped local output budget, which bounds peak prefill memory."""
    clamped = resolve_max_output_tokens(config.max_output_tokens)
    if clamped == config.max_output_tokens:
        return CheckResult("output budget", OK, f"{clamped} tokens")
    return CheckResult(
        "output budget",
        OK,
        f"{clamped} tokens (clamped from {config.max_output_tokens}; "
        "set AGENT_MLX_MAX_OUTPUT_TOKENS to override)",
    )


def run_checks(
    config: Config,
    provider: LLMProvider,
    *,
    extension_loader: Callable[[], list[str]] | None = None,
) -> DoctorReport:
    """Run every diagnosis and return an ordered report."""
    results = [
        *_check_config(config),
        _check_hub(config),
        _check_mlx_runtime(provider),
        _check_runtime_cli(config),
        _check_output_budget(config),
        _check_download_tooling(),
        _check_extensions(config),
    ]
    if extension_loader is not None and config.extensions:
        results.append(check_extensions_load(config, extension_loader))
    return DoctorReport(results=results)


def _load_extensions_offline(provider: LLMProvider, config: Config) -> list[str]:
    """Load configured extensions through a throwaway agent (no network)."""
    from marv.runtime.agent import Agent
    from marv.runtime.settings import AgentSettings

    async def _run() -> list[str]:
        settings = AgentSettings(
            context_max_tokens=config.context_max_tokens,
            session_dir=config.session_dir,
            extensions=list(config.extensions),
        )
        agent = Agent(settings, provider, cwd=Path.cwd())
        from marv.extensions.host import ExtensionHost

        host = ExtensionHost(agent, paths=list(config.extensions))
        try:
            return await host.load_extensions()
        finally:
            await agent.close()

    return asyncio.run(_run())


def doctor_command(
    *,
    config: Config,
    create_llm_provider: Callable[[Config], LLMProvider],
    as_json: bool = False,
) -> DoctorReport:
    """Run ``marv doctor`` and print the report."""
    provider = create_llm_provider(config)
    loader: Callable[[], list[str]] | None = None
    if config.extensions:
        loader = lambda: _load_extensions_offline(provider, config)  # noqa: E731
    report = run_checks(config, provider, extension_loader=loader)

    if as_json:
        import json

        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report(report))
    return report


def format_report(report: DoctorReport) -> str:
    """Human-readable doctor output."""
    lines = ["marv doctor", ""]
    for result in report.results:
        marker = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}[result.status]
        lines.append(f"  [{marker}] {result.name}: {result.detail}")
    lines.append("")
    if report.failures:
        lines.append(f"{len(report.failures)} item(s) need attention")
    else:
        lines.append("all checks passed")
    return "\n".join(lines)
