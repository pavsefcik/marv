"""Behavior tests for model capability policy."""

from marv.llm.models import (
    is_model_valid_for_provider,
    resolve_capability_provider,
    supports_reasoning,
    supports_xhigh,
)
from marv.runtime.settings import ThinkingLevel, get_available_thinking_levels


class TestModelCapabilities:
    """Tests for model capability policy and normalization."""

    def test_supports_xhigh_for_current_gpt_5_family(self) -> None:
        assert supports_xhigh("gpt-5.3-codex", provider="openai")
        assert supports_xhigh("gpt-5.4", provider="openai")

    def test_gpt_5_supports_reasoning_but_not_xhigh(self) -> None:
        assert supports_reasoning("gpt-5", provider="openai")
        assert not supports_xhigh("gpt-5", provider="openai")

    def test_openai_compat_prefix_supports_reasoning_and_xhigh_for_openai_models(self) -> None:
        assert supports_reasoning("openai/gpt-5.3-codex", provider="openai-compat")
        assert supports_xhigh("openai/gpt-5.3-codex", provider="openai-compat")

    def test_provider_resolution_for_capability_checks(self) -> None:
        assert resolve_capability_provider("openai") == "openai"
        assert resolve_capability_provider("openai-codex") == "openai"
        assert resolve_capability_provider("openrouter") == "openai-compat"
        assert resolve_capability_provider(None) is None

    def test_available_thinking_levels_are_model_aware(self) -> None:
        non_xhigh = get_available_thinking_levels("gpt-5", provider="openai")
        xhigh = get_available_thinking_levels("gpt-5.4", provider="openai")

        assert ThinkingLevel.XHIGH not in non_xhigh
        assert ThinkingLevel.XHIGH in xhigh

    def test_model_validation_is_provider_aware(self) -> None:
        assert is_model_valid_for_provider("gpt-5.4", "openai")
        assert is_model_valid_for_provider("gpt-5-codex", "openai-codex")
        assert not is_model_valid_for_provider("gpt-4o", "openai-codex")
