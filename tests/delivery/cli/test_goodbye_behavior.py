"""Behavior tests for the TUI's exit sign-off.

The block is the last thing a user sees, so it must only ever state what marv
observed: a stopped model is reported as stopped, a running one as running, and
a session without a model never gets a fabricated teardown line.
"""

from __future__ import annotations

from io import StringIO

from marv.cli.goodbye import (
    CLEAR_SCREEN,
    GoodbyeFacts,
    announce,
    goodbye_text,
    render_goodbye,
)


def test_unloaded_model_is_reported_as_terminated():
    facts = GoodbyeFacts(
        version="1.2.3",
        session_id="s-abc",
        model="mlx-community/Qwen3-4B",
        port=11500,
        memory_bytes=int(5.2 * 1024**3),
        unloaded=True,
    )

    text = goodbye_text(facts)

    assert "marv says bye!" in text
    assert "session saved" in text and "s-abc" in text
    assert "model unloaded" in text
    assert "mlx-community/Qwen3-4B :11500" in text
    assert "memory released" in text and "5.2 GB" in text
    assert "all systems terminated" in text
    assert "1.2.3" in text


def test_a_still_running_model_is_not_claimed_as_terminated():
    facts = GoodbyeFacts(
        version="1.2.3",
        session_id="s-abc",
        model="mlx-community/Qwen3-4B",
        port=11500,
        unloaded=False,
        still_running=True,
    )

    text = goodbye_text(facts)

    assert "model still running" in text
    assert "marv-mlx stop" in text
    # The honest part: nothing was unloaded, so nothing says it was.
    assert "all systems terminated" not in text
    assert "memory released" not in text


def test_a_session_without_a_model_says_so_instead_of_inventing_an_unload():
    facts = GoodbyeFacts(version="1.2.3", session_id="s-abc")

    text = goodbye_text(facts)

    assert "no model was running" in text
    assert "all systems terminated" in text
    assert "model unloaded" not in text


def test_colour_is_opt_out_and_plain_text_has_no_escapes():
    facts = GoodbyeFacts(version="1.2.3", session_id="s-abc", unloaded=True)

    plain = render_goodbye(facts, color=False)
    coloured = render_goodbye(facts, color=True)

    assert "\x1b[" not in plain
    assert plain == goodbye_text(facts)
    assert "\x1b[1;38;5;212m" in coloured  # the marv-mlx magenta wordmark


def test_announce_clears_the_screen_then_prints_the_block():
    facts = GoodbyeFacts(version="1.2.3", session_id="s-abc", unloaded=True)
    stream = StringIO()

    announce(facts, stream=stream, color=False)

    written = stream.getvalue()
    assert written.startswith(CLEAR_SCREEN)
    assert "marv says bye!" in written
    assert written.endswith("\n")


class _Tty(StringIO):
    """A stdout stand-in that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


class _FakeApp:
    """Stands in for AgentApp so the exit path can be driven without Textual."""

    def __init__(self, config, *, provider, session) -> None:
        self._provider = provider

    def run(self) -> None:
        pass

    def exit_summary(self):
        from marv.tui.app import ExitSummary

        return ExitSummary(session_id="s-1", model="m", memory_bytes=1024)


def _drive_exit(monkeypatch, *, goodbye: bool, tty: bool):
    """Run the TUI exit path with fakes; return the captured sign-off facts."""
    from marv.cli import _run_tui
    from marv.config import Config
    from marv.llm.local_server import StoppedServer

    captured: list[GoodbyeFacts] = []
    monkeypatch.setattr("marv.cli.announce", lambda facts, **kwargs: captured.append(facts))
    monkeypatch.setattr("marv.tui.app.AgentApp", _FakeApp)
    monkeypatch.setattr("marv.llm.model_download.start_catalog_refresh", lambda: None)
    monkeypatch.setattr("marv.llm.server_lifecycle.install_handlers", lambda: None)

    class _Provider:
        def unload(self):
            return StoppedServer(model="m", port=11500)

        def is_serving(self) -> bool:
            return False

    import sys as _sys

    if tty:
        monkeypatch.setattr(_sys, "stdout", _Tty())
    _run_tui(Config(goodbye=goodbye), None, _Provider())
    return captured


def test_tui_exit_announces_what_was_unloaded(monkeypatch):
    captured = _drive_exit(monkeypatch, goodbye=True, tty=True)

    assert len(captured) == 1
    facts = captured[0]
    assert facts.unloaded is True
    assert facts.model == "m"
    assert facts.port == 11500
    assert facts.session_id == "s-1"
    assert facts.memory_bytes == 1024
    assert facts.still_running is False


def test_tui_exit_is_silent_when_the_sign_off_is_configured_off(monkeypatch):
    assert _drive_exit(monkeypatch, goodbye=False, tty=True) == []


def test_tui_exit_is_silent_when_stdout_is_not_a_terminal(monkeypatch):
    assert _drive_exit(monkeypatch, goodbye=True, tty=False) == []
