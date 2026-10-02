"""Shared default model configuration for providers."""

# YMLX is model-agnostic: the default model is discovered dynamically from the
# managed hub, so there is no hardcoded cloud default. A caller can still pass
# an explicit model override.
DEFAULT_MODEL_BY_PROVIDER: dict[str, str] = {}


def default_model_for_provider(provider: str) -> str | None:
    """Return default model for a provider family."""
    return DEFAULT_MODEL_BY_PROVIDER.get(provider)
