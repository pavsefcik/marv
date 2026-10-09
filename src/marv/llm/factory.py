"""Factory for constructing LLM providers from runtime config."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from marv.llm.defaults import default_model_for_provider

if TYPE_CHECKING:
    from marv.llm.provider import LLMProvider


class ProviderOverride(Protocol):
    """Shape for provider override values supplied by delivery config."""

    base_url: str
    model: str | None
    api_key: str | None


ProviderOverrides = Mapping[str, ProviderOverride]


@dataclass(slots=True)
class ResolvedProviderConfig:
    """Resolved settings for constructing a provider implementation."""

    base_url: str
    model: str | None
    api_key: str | None = None


#: Providers that serve a local, unauthenticated endpoint.
_LOCAL_PROVIDERS = ("marv-mlx", "apple-fm")


#: Provider-specific base-URL env vars. These are fallbacks used when neither an
#: explicit ``base_url`` nor a provider override supplies one. The harness owns the
#: ``AGENT_*`` namespace (the runtime owns ``MARV_MLX_*``).
_ENV_BASE_URLS = {
    "marv-mlx": "AGENT_MLX_BASE_URL",
    "apple-fm": "APPLE_FM_BASE_URL",
    "openai-compat": "OPENAI_COMPAT_BASE_URL",
    "ollama": "OLLAMA_BASE_URL",
}


def _env_base_url(provider: str) -> str | None:
    """Provider-specific base URL from the environment, if set."""
    env_var = _ENV_BASE_URLS.get(provider)
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value
        # One-release deprecation: MARV_MLX_BASE_URL was the old name. The
        # runtime owns MARV_MLX_* now; the harness uses AGENT_MLX_*. Accept and
        # warn, then drop next release.
        if provider == "marv-mlx":
            legacy = os.environ.get("MARV_MLX_BASE_URL")
            if legacy:
                print(
                    "marv: MARV_MLX_BASE_URL is deprecated; use AGENT_MLX_BASE_URL",
                    file=sys.stderr,
                )
                return legacy
    return None


def _env_provider_key(provider: str) -> str | None:
    mapping = {
        "marv-mlx": "AGENT_MLX_BASE_URL",
        "apple-fm": "APPLE_FM_BASE_URL",
        "openai-compat": "OPENAI_COMPAT_BASE_URL",
        "ollama": "OLLAMA_BASE_URL",
    }
    env_var = mapping.get(provider)
    if env_var:
        return os.environ.get(env_var)
    return None


def _resolve_api_key(default: str | None, provider: str) -> str | None:
    if provider in _LOCAL_PROVIDERS:
        # Local endpoints are unauthenticated; never send a key.
        return default or ""
    env_key = _env_provider_key(provider)
    if env_key:
        return env_key
    agent_key = os.environ.get("AGENT_API_KEY")
    if agent_key:
        return agent_key
    return default or ""


def _resolve_model(provider: str, model: str | None, fallback_model: str | None) -> str | None:
    if model is not None:
        return model
    if fallback_model is not None:
        return fallback_model
    return default_model_for_provider(provider)


def resolve_provider_config(
    *,
    provider: str,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    provider_overrides: ProviderOverrides | None = None,
) -> ResolvedProviderConfig:
    """Resolve concrete provider config from flat provider bootstrap params."""
    if provider_overrides and provider in provider_overrides:
        override = provider_overrides[provider]
        return ResolvedProviderConfig(
            base_url=base_url or override.base_url,
            model=_resolve_model(provider, model, override.model),
            api_key=_resolve_api_key(api_key or override.api_key, provider),
        )
    default_configs = {
        "marv-mlx": ResolvedProviderConfig(
            base_url="http://localhost:11500",
            model=None,
            api_key="",
        ),
        "apple-fm": ResolvedProviderConfig(
            base_url="http://127.0.0.1:1976",
            model="system",
            api_key="",
        ),
        "ollama": ResolvedProviderConfig(
            base_url="http://localhost:11434",
            model=None,
            api_key="",
        ),
    }

    if provider in default_configs:
        provider_config = default_configs[provider]
        return ResolvedProviderConfig(
            base_url=base_url or _env_base_url(provider) or provider_config.base_url,
            model=_resolve_model(provider, model, provider_config.model),
            api_key=_resolve_api_key(api_key or provider_config.api_key, provider),
        )

    return ResolvedProviderConfig(
        base_url=base_url or _env_base_url(provider) or "http://localhost:11500",
        model=_resolve_model(provider, model, None),
        api_key=_resolve_api_key(api_key, provider),
    )


def create_provider(
    *,
    provider: str,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    temperature: float,
    max_output_tokens: int,
    provider_overrides: ProviderOverrides | None = None,
    server_manager: str = "embedded",
) -> LLMProvider:
    """Create a concrete provider instance from flat provider bootstrap params."""
    from marv.llm.models import is_model_valid_for_provider

    prov_config = resolve_provider_config(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        provider_overrides=provider_overrides,
    )

    # marv-mlx: when no model is configured, fall back to the currently-serving
    # one; if none is serving either, still build the provider with an empty
    # model so the TUI can open and the model picker can select one.
    if prov_config.model is None and provider in ("marv-mlx", "openai-compat", "ollama"):
        from marv.llm.mlx_models import running_model_id

        prov_config.model = running_model_id(prov_config.base_url)
        if prov_config.model is None and provider == "marv-mlx":
            prov_config.model = ""

    if prov_config.model is None:
        raise ValueError(
            f"Model is required for provider '{provider}'. "
            "Set --model or configure provider-specific model override."
        )

    if not is_model_valid_for_provider(prov_config.model, provider):
        raise ValueError(f"Model '{prov_config.model}' is not valid for provider '{provider}'")

    if provider == "marv-mlx":
        from marv.llm.marv_mlx import MarvMlxProvider

        return MarvMlxProvider(
            base_url=prov_config.base_url,
            api_key=prov_config.api_key or "",
            model=prov_config.model,
            temperature=temperature,
            max_tokens=max_output_tokens,
            server_manager=server_manager,
        )

    if provider == "apple-fm":
        from marv.llm.apple_fm import AppleFMProvider

        return AppleFMProvider(
            base_url=prov_config.base_url,
            api_key=prov_config.api_key or "",
            model=prov_config.model,
            temperature=temperature,
            max_tokens=max_output_tokens,
        )

    from marv.llm.openai_compat import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        name=provider,
        base_url=prov_config.base_url,
        api_key=prov_config.api_key or "",
        model=prov_config.model,
        temperature=temperature,
        max_tokens=max_output_tokens,
    )
