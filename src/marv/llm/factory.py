"""Factory for constructing LLM providers from runtime config."""

from __future__ import annotations

import os
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


def _env_provider_key(provider: str) -> str | None:
    mapping = {
        "ymlx": "YMLX_BASE_URL",
        "openai-compat": "OPENAI_COMPAT_BASE_URL",
        "ollama": "OLLAMA_BASE_URL",
    }
    env_var = mapping.get(provider)
    if env_var:
        return os.environ.get(env_var)
    return None


def _resolve_api_key(default: str | None, provider: str) -> str | None:
    if provider == "ymlx":
        # YMLX serves an unauthenticated local endpoint; never send a key.
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
        "ymlx": ResolvedProviderConfig(
            base_url="http://localhost:11500",
            model=None,
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
            base_url=base_url or provider_config.base_url,
            model=_resolve_model(provider, model, provider_config.model),
            api_key=_resolve_api_key(api_key or provider_config.api_key, provider),
        )

    return ResolvedProviderConfig(
        base_url=base_url or "http://localhost:11500",
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

    # YMLX: when no model is configured, fall back to the currently-serving one;
    # if none is serving either, still build the provider with an empty model so
    # the TUI can open and the model picker can select one.
    if prov_config.model is None and provider in ("ymlx", "openai-compat", "ollama"):
        from marv.llm.ymlx_models import running_model_id

        prov_config.model = running_model_id(prov_config.base_url)
        if prov_config.model is None and provider == "ymlx":
            prov_config.model = ""

    if prov_config.model is None:
        raise ValueError(
            f"Model is required for provider '{provider}'. "
            "Set --model or configure provider-specific model override."
        )

    if not is_model_valid_for_provider(prov_config.model, provider):
        raise ValueError(f"Model '{prov_config.model}' is not valid for provider '{provider}'")

    if provider == "ymlx":
        from marv.llm.ymlx import YMLXProvider

        return YMLXProvider(
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
