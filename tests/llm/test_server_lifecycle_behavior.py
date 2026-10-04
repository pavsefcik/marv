"""Behavior tests for model-server teardown on exit."""

from __future__ import annotations

from marv.llm import server_lifecycle


def test_stop_all_runs_registered_callbacks_once():
    calls: list[str] = []
    server_lifecycle.register_stop(lambda: calls.append("stop"))

    server_lifecycle.stop_all()
    server_lifecycle.stop_all()

    assert calls == ["stop"]


def test_unregistered_callback_is_not_run():
    calls: list[str] = []
    callback = lambda: calls.append("stop")  # noqa: E731

    server_lifecycle.register_stop(callback)
    server_lifecycle.unregister_stop(callback)
    server_lifecycle.stop_all()

    assert calls == []
