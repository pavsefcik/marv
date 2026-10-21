"""Tests for the session module."""

from datetime import UTC, datetime
from pathlib import Path

from marv.runtime.message import Message, Role
from marv.runtime.session import Session, SessionMetadata


class TestSession:
    """Tests for Session class."""

    def test_new_session(self, temp_dir: Path):
        """A new session exists in memory but leaves no file until used."""
        session = Session.new(temp_dir)

        assert not session.path.exists()
        assert session.metadata.id
        assert session.metadata.cwd == str(Path.cwd())
        assert len(session.messages) == 0

    def test_new_session_writes_on_the_first_prompt_and_answer(self, temp_dir: Path):
        """A prompt alone is not enough; the answer starts the file."""
        session = Session.new(temp_dir)
        session.append(Message.system("You are a coding assistant."))
        session.append(Message.user("Fix the parser bug in src/parser.py"))

        assert not session.path.exists()

        session.append(Message.assistant("Done."))

        assert session.path.exists()
        loaded = Session.load(session.path)
        assert [message.role for message in loaded.messages] == [
            Role.SYSTEM,
            Role.USER,
            Role.ASSISTANT,
        ]

    def test_a_greeting_with_an_answer_is_saved(self, temp_dir: Path):
        """Even a short 'hi' exchange counts once it has a prompt and answer."""
        session = Session.new(temp_dir)
        session.append(Message.system("You are a friendly assistant."))
        session.append(Message.user("hi"))
        session.append(Message.assistant("Hi there! How's your day going?"))

        assert session.path.exists()
        loaded = Session.load(session.path)
        assert [message.content for message in loaded.messages if message.role is Role.USER] == [
            "hi"
        ]

    def test_new_session_keeps_earlier_model_changes_when_it_starts_persisting(
        self, temp_dir: Path
    ):
        session = Session.new(temp_dir)
        session.append_model_change("marv-mlx", "mlx-community/Qwen3.5-4B-MLX-4bit")
        session.append(Message.user("List the failing tests"))

        assert not session.path.exists()

        session.append(Message.assistant("Here they are."))

        loaded = Session.load(session.path)
        assert loaded.get_model_selection() == (
            "marv-mlx",
            "mlx-community/Qwen3.5-4B-MLX-4bit",
        )

    def test_append_message(self, temp_dir: Path):
        """Test appending a message."""
        session = Session.new(temp_dir)

        msg = Message.user("Hello")
        session.append(msg)

        assert len(session.messages) == 1
        assert session.messages[0].content == "Hello"
        assert session.messages[0].role == Role.USER

    def test_load_session(self, temp_dir: Path):
        """Test loading an existing session."""
        # Create a session and add messages
        session = Session.new(temp_dir)
        session.append(Message.user("Fix the parser bug in src/parser.py"))
        session.append(Message.assistant("Hi there"))

        # Load the session
        loaded = Session.load(session.path)

        assert loaded.metadata.id == session.metadata.id
        assert len(loaded.messages) == 2
        assert loaded.messages[0].content == "Fix the parser bug in src/parser.py"
        assert loaded.messages[1].content == "Hi there"

    def test_list_sessions(self, temp_dir: Path):
        """Test listing sessions."""
        for prompt in ("First", "Second", "Third"):
            session = Session.new(temp_dir)
            session.append(Message.user(prompt))
            session.append(Message.assistant("Done"))

        sessions = Session.list_sessions(temp_dir)

        assert len(sessions) == 3

    def test_get_latest_skips_sessions_without_an_answer(self, temp_dir: Path):
        """Resuming goes to the latest session that has a prompt and answer."""
        complete = Session.new(temp_dir)
        complete.append(Message.user("Fix the parser bug in src/parser.py"))
        complete.append(Message.assistant("Fixed"))
        incomplete = Session.new(temp_dir)
        incomplete.append(Message.user("Leave this one unanswered"))

        retrieved = Session.get_latest(temp_dir)

        assert retrieved is not None
        assert retrieved.metadata.id == complete.metadata.id

    def test_a_prompt_and_answer_make_a_session_resumable(self, temp_dir: Path):
        session = Session.new(temp_dir)
        session.append(Message.user("hi"))
        session.append(Message.assistant("hello"))

        assert session.is_resumable()

    def test_a_prompt_without_an_answer_is_not_resumable(self, temp_dir: Path):
        session = Session.new(temp_dir)
        session.append(Message.user("hi"))

        assert not session.is_resumable()

    def test_description_is_one_line_from_the_first_prompt(self, temp_dir: Path):
        session = Session.new(temp_dir)
        session.append(Message.user("Explain\n\nthe parser\nplease"))
        session.append(Message.assistant("Sure"))

        assert session.description() == "Explain the parser please"
        assert session.description(limit=12) == "Explain the\u2026"

    def test_list_resumable_sessions_hides_incomplete_and_broken_files(self, temp_dir: Path):
        complete = Session.new(temp_dir)
        complete.append(Message.user("testing"))
        complete.append(Message.assistant("hi"))
        incomplete = Session.new(temp_dir)
        incomplete.append(Message.user("Investigate the flaky release workflow"))
        Session.new(temp_dir)
        (temp_dir / "broken.jsonl").write_text("not-json\n")

        sessions = Session.list_resumable_sessions(temp_dir)

        assert sessions == [complete.path]

    def test_fork_session(self, temp_dir: Path):
        """Test forking a session."""
        # Create original session
        original = Session.new(temp_dir)
        msg1 = Message.user("First")
        msg2 = Message.assistant("Second")
        msg3 = Message.user("Third")
        original.append(msg1)
        original.append(msg2)
        original.append(msg3)

        # Fork from second message
        forked = original.fork(msg2.id, temp_dir)

        assert forked.metadata.parent_session_id == original.metadata.id
        assert forked.metadata.provider == original.metadata.provider
        assert forked.metadata.model == original.metadata.model
        assert len(forked.messages) == 2  # First two messages
        assert forked.messages[0].content == "First"
        assert forked.messages[1].content == "Second"

        reloaded = Session.load(forked.path)
        assert len(reloaded.messages) == 2
        assert reloaded.messages[0].parent_id is None
        assert reloaded.messages[1].parent_id == reloaded.messages[0].id

    def test_set_leaf_moves_active_branch(self, temp_dir: Path):
        """Set leaf moves the active branch to the selected message."""
        session = Session.new(temp_dir)
        first = Message.user("First")
        second = Message.assistant("Second")
        third = Message.user("Third")
        session.append(first)
        session.append(second)
        session.append(third)

        session.set_leaf(second.id)

        assert [m.content for m in session.messages] == ["First", "Second"]

        loaded = Session.load(session.path)

        assert loaded.leaf_id == second.id
        assert [m.content for m in loaded.messages] == ["First", "Second"]
        assert [entry.id for entry in loaded.active_entries] == [first.id, second.id]

    def test_compaction_summary_rebuilds_context_from_first_kept(self, temp_dir: Path):
        """Compaction summary keeps messages from firstKeptEntryId onward."""
        session = Session.new(temp_dir)
        msg1 = Message.user("Old 1")
        msg2 = Message.assistant("Old 2")
        msg3 = Message.user("Recent 1")
        msg4 = Message.assistant("Recent 2")
        session.append(msg1)
        session.append(msg2)
        session.append(msg3)
        session.append(msg4)

        summary = "## Summary\n- condensed"
        TOKENS_BEFORE = 100

        session.append_compaction(summary, first_kept_entry_id=msg3.id, tokens_before=TOKENS_BEFORE)

        assert "Previous conversation summary" in session.messages[0].content
        assert [m.content for m in session.messages[1:]] == ["Recent 1", "Recent 2"]

        loaded = Session.load(session.path)

        assert "Previous conversation summary" in loaded.messages[0].content
        assert [m.content for m in loaded.messages[1:]] == ["Recent 1", "Recent 2"]


class TestSessionMetadata:
    """Tests for SessionMetadata class."""

    def test_metadata_creation(self):
        """Test creating session metadata."""
        metadata = SessionMetadata(
            id="test123",
            created_at=datetime.now(UTC),
            cwd="/home/user/project",
        )

        assert metadata.id == "test123"
        assert metadata.cwd == "/home/user/project"
        assert metadata.parent_session_id is None

    def test_metadata_with_parent(self):
        """Test metadata with parent session."""
        metadata = SessionMetadata(
            id="child",
            created_at=datetime.now(UTC),
            cwd="/home/user/project",
            parent_session_id="parent",
        )

        assert metadata.parent_session_id == "parent"
