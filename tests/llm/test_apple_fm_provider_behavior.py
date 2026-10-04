"""Behavior tests for the apple-fm provider."""

from __future__ import annotations

import asyncio

from marv.llm.apple_fm import BUILTIN_MODELS, DEFAULT_FM_PORT, AppleFMProvider, resolve_fm_command


def make_provider(**kwargs) -> AppleFMProvider:
    return AppleFMProvider(fm_command=kwargs.pop("fm_command", "/usr/bin/fm"), **kwargs)


def test_provider_is_chat_only():
    provider = make_provider()

    assert provider.name == "apple-fm"
    assert provider.supports_tools is False
    assert provider.supports_thinking() is False


def test_launch_argv_serves_on_the_configured_port():
    provider = make_provider()

    assert provider._launch_argv("system", 1976) == ["/usr/bin/fm", "serve", "--port", "1976"]


def test_default_base_url_uses_the_fm_port():
    assert make_provider().base_url == f"http://127.0.0.1:{DEFAULT_FM_PORT}"


def test_resolve_fm_command_honours_an_explicit_override():
    assert resolve_fm_command("/custom/fm") == "/custom/fm"


def test_list_models_falls_back_to_builtins_when_server_is_down(monkeypatch):
    from marv.llm import apple_fm

    monkeypatch.setattr(apple_fm, "list_served_models", lambda base_url: [])

    assert asyncio.run(make_provider().list_models()) == BUILTIN_MODELS


def test_list_models_reports_what_the_server_advertises(monkeypatch):
    from marv.llm import apple_fm

    monkeypatch.setattr(apple_fm, "list_served_models", lambda base_url: ["system", "pcc"])

    assert asyncio.run(make_provider().list_models()) == ["system", "pcc"]


def test_is_serving_uses_server_reachability(monkeypatch):
    from marv.llm import apple_fm

    monkeypatch.setattr(apple_fm, "server_reachable", lambda base_url: True)

    # One server serves every model, so any selected model counts as serving.
    assert make_provider(model="pcc").is_serving() is True


def test_missing_fm_cli_raises_a_helpful_error(monkeypatch):
    from marv.llm import apple_fm

    monkeypatch.setattr(apple_fm, "resolve_fm_command", lambda explicit=None: None)
    provider = AppleFMProvider(fm_command=None)

    try:
        provider._launch_argv("system", 1976)
    except RuntimeError as exc:
        assert "macOS 27" in str(exc)
    else:
        raise AssertionError("expected a RuntimeError when fm is unavailable")
