"""Session menus — pick a session to load, a fork point, or a leaf entry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from marv.runtime.message import Role
from marv.runtime.session import (
    CompactionEntry,
    MessageEntry,
    ModelChangeEntry,
    Session,
    SessionStateEntry,
)
from marv.tui.panel import MenuPanel

if TYPE_CHECKING:
    from pathlib import Path

    from marv.runtime.session import SessionEntry


class SessionLoadPanel(MenuPanel):
    """Menu for selecting a session to load; dismisses with its file path."""

    def __init__(self, session_dir: Path, limit: int = 50) -> None:
        super().__init__(
            "load session",
            self._build_options(session_dir, limit),
            hint="enter to load · esc to cancel",
            empty="no sessions found",
        )

    @staticmethod
    def _build_options(session_dir: Path, limit: int) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = []
        for path in Session.list_sessions(session_dir, limit=limit):
            try:
                sess = Session.load(path)
                created = sess.metadata.created_at.strftime("%Y-%m-%d %H:%M:%S")
                msg_count = len(sess.messages)
                cwd = sess.metadata.cwd
                label = f"{created} | id:{sess.metadata.id} | msgs:{msg_count} | cwd:{cwd}"
            except Exception:  # noqa: BLE001 - a broken session stays selectable
                label = f"{path.name} | (error reading session)"
            options.append((str(path), label))
        return options


class SessionForkPanel(MenuPanel):
    """Menu for picking a fork point; dismisses with the message id."""

    def __init__(self, session: Session, limit: int = 200) -> None:
        options = self._build_options(session, limit)
        super().__init__(
            "fork from message",
            options,
            subtitle=self._build_summary(session),
            hint="enter to fork · esc to cancel",
            empty="no messages available to fork",
            initial_id=self._default_message_id(session),
        )

    @staticmethod
    def _build_summary(session: Session) -> str:
        return f"session {session.metadata.id} | {len(session.messages)} message(s)"

    @staticmethod
    def _default_message_id(session: Session) -> str | None:
        for msg in reversed(session.messages):
            if msg.role == Role.ASSISTANT:
                return msg.id
        if session.messages:
            return session.messages[-1].id
        return None

    @staticmethod
    def _build_options(session: Session, limit: int) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = []
        messages = session.messages
        start = max(0, len(messages) - limit)
        for idx, msg in enumerate(messages[start:], start=start):
            snippet = msg.content.replace("\n", " ").strip()
            if len(snippet) > 60:
                snippet = snippet[:57] + "..."
            label = f"[{idx}] {msg.role.value.upper():<9} {msg.id} {snippet}"
            options.append((msg.id, label))
        return options


class SessionTreePanel(MenuPanel):
    """Menu of session entries (indented by branch depth).

    Dismisses with the chosen entry id to move the active leaf there.
    """

    def __init__(self, session: Session, limit: int | None = 200) -> None:
        super().__init__(
            "session tree",
            self._build_options(session, limit),
            subtitle=self._build_summary(session),
            hint="enter to branch there · esc to cancel",
            empty="no entries available",
            initial_id=session.leaf_id,
        )

    @staticmethod
    def _build_summary(session: Session) -> str:
        leaf = session.leaf_id or "(unknown)"
        entry_count = len([e for e in session.entries if not isinstance(e, SessionStateEntry)])
        return f"session {session.metadata.id} | entries: {entry_count} | leaf: {leaf}"

    @staticmethod
    def _build_options(session: Session, limit: int | None) -> list[tuple[str, str]]:
        entries: list[SessionEntry] = [
            e for e in session.entries if not isinstance(e, SessionStateEntry)
        ]
        if limit is not None and len(entries) > limit:
            entries = entries[-limit:]

        children: dict[str | None, list[SessionEntry]] = {}
        for entry in entries:
            children.setdefault(entry.parent_id, []).append(entry)

        options: list[tuple[str, str]] = []
        seen: set[str] = set()

        def walk(parent_id: str | None, depth: int) -> None:
            for entry in children.get(parent_id, []):
                seen.add(entry.id)
                prefix = "  " * depth + ("└ " if depth else "")
                label = f"{prefix}{_format_entry(entry, session.leaf_id)}"
                options.append((entry.id, label))
                walk(entry.id, depth + 1)

        walk(None, 0)
        # Entries cut off by ``limit`` lose their parent; keep them reachable.
        for entry in entries:
            if entry.id not in seen:
                options.append((entry.id, _format_entry(entry, session.leaf_id)))
        return options


def _format_entry(entry: SessionEntry, leaf_id: str | None) -> str:
    leaf_marker = " (leaf)" if entry.id == leaf_id else ""
    if isinstance(entry, MessageEntry):
        msg = entry.message
        snippet = msg.content.replace("\n", " ").strip()
        if len(snippet) > 60:
            snippet = snippet[:57] + "..."
        return f"{msg.role.value.upper()} {entry.id} {snippet}{leaf_marker}"
    if isinstance(entry, ModelChangeEntry):
        return f"MODEL {entry.id} {entry.provider}/{entry.model_id}{leaf_marker}"
    if isinstance(entry, CompactionEntry):
        summary = entry.summary.replace("\n", " ").strip()
        if len(summary) > 60:
            summary = summary[:57] + "..."
        tokens = ""
        if entry.tokens_before is not None or entry.tokens_after is not None:
            tokens = f" {entry.tokens_before}->{entry.tokens_after}"
        return f"COMPACT {entry.id}{tokens} {summary}{leaf_marker}"
    return f"{entry.type} {entry.id}{leaf_marker}"
