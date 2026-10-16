"""User input widget with dropdown autocomplete and multi-line editing."""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import OptionList, TextArea
from textual.widgets.option_list import Option

from marv.runtime.attachments import IMAGE_SUFFIXES

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.events import Key

    from marv.prompts.loader import PromptTemplateLoader
    from marv.skills.loader import SkillLoader

# Keys that insert a newline instead of submitting.
NEWLINE_KEYS = {"shift+enter", "alt+enter", "meta+enter"}

#: A `@partial` token ending the document, quoted (`@"partial`) or bare. A token
#: that is not at the end (a finished `@file.png `, or a path fragment further
#: back in the prompt) is not being typed, so nothing is completed for it.
_ATTACHMENT_PARTIAL = re.compile(r'(?:^|(?<=\s))@(?:"[^"]*"?|\S*)\Z')

#: Most `@` choices offered at once; a longer partial is the way to narrow.
MAX_ATTACHMENT_OPTIONS = 20

#: File suffixes offered after `@`, matching what `parse_attachment_paths`
#: accepts, so a completed path is always attachable.
ATTACHABLE_SUFFIXES = IMAGE_SUFFIXES | {"pdf"}


# Built-in commands available for autocomplete
BUILTIN_COMMANDS = [
    "/clear",
    "/new",
    "/load",
    "/resume",
    "/fork",
    "/tree",
    "/context",
    "/compact",
    "/chat",
    "/tools",
    "/help",
    "/model",
    "/theme",
    "/quit",
]


class PromptTextArea(TextArea):
    """A TextArea tuned for the prompt line.

    Behaviour:
    - Enter submits, shift+enter (or alt/meta+enter) inserts a newline.
    - Up/down navigate suggestions or input history while the document is a
      single line, and move the caret once the prompt is multi-line.
    - ctrl+c is left unbound so it still quits the app.
    """

    # Strip bindings that would swallow the keys we handle ourselves.
    BINDINGS = [
        binding
        for binding in TextArea.BINDINGS
        if isinstance(binding, Binding)
        and binding.key
        not in {
            "up",
            "down",
            "ctrl+c,super+c",
            "ctrl+y,super+y",
        }
    ]

    def __init__(self, prompt: PromptInput, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._prompt = prompt

    async def _on_key(self, event: Key) -> None:  # noqa: N802 - Textual handler name
        key = event.key

        if key == "enter":
            event.stop()
            event.prevent_default()
            self._prompt._submit()
            return

        if key in NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            if not self.read_only:
                self.insert("\n")
            return

        if key in {"up", "down"}:
            if self._prompt._handle_vertical_key(event.key):
                event.stop()
                event.prevent_default()
            else:
                # Multi-line prompt: fall back to caret movement.
                event.stop()
                event.prevent_default()
                if key == "up":
                    self.action_cursor_up(select=False)
                else:
                    self.action_cursor_down(select=False)
            return

        # Everything else keeps default TextArea behaviour (printable input,
        # editing bindings); unhandled keys bubble to the PromptInput widget.

    def set_text_and_caret(self, value: str) -> None:
        """Replace the document and put the caret at the end."""
        self.text = value
        with contextlib.suppress(Exception):
            self.move_cursor(self.document.end)


class PromptInput(Widget, can_focus=False):
    """Multi-line input with dropdown command autocomplete."""

    class Submitted(Message):
        """Posted when user submits input."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

        @property
        def control(self) -> PromptInput:
            return self._sender  # type: ignore

    def __init__(
        self,
        skill_loader: SkillLoader | None = None,
        template_loader: PromptTemplateLoader | None = None,
        extension_commands: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._skill_loader = skill_loader
        self._template_loader = template_loader
        self._extension_commands = extension_commands or []
        self._history: list[str] = []
        self._history_index: int | None = None
        self._history_draft = ""
        self._suppress_history_reset = False
        self._models: list[str] = []

    def _set_input_value(self, value: str, *, reset_history: bool) -> None:
        input_widget = self.query_one("#prompt-inner", PromptTextArea)
        self._suppress_history_reset = True
        input_widget.set_text_and_caret(value)
        if reset_history:
            self._history_index = None
            self._history_draft = value

    def _get_slash_commands(self) -> list[str]:
        """Build list of slash commands (templates, built-ins, extensions)."""
        commands = list(BUILTIN_COMMANDS)

        # Add template names as commands
        if self._template_loader:
            for name in self._template_loader.list_templates():
                commands.append(f"/{name}")

        # Add extension commands
        for name in self._extension_commands:
            commands.append(f"/{name}")

        return sorted(set(commands))

    def _get_skill_commands(self) -> list[str]:
        """Build list of skill commands ($skill-name)."""
        commands: list[str] = []
        if self._skill_loader:
            for skill in self._skill_loader.skills.values():
                commands.append(f"${skill.name}")
        return sorted(set(commands))

    def set_extension_commands(self, commands: list[str]) -> None:
        """Update available extension commands."""
        self._extension_commands = commands

    def set_models(self, models: list[str]) -> None:
        """Update the model list offered by the inline `/model` dropdown."""
        self._models = sorted(models)

    def _get_attachment_options(self, value: str) -> list[Option] | None:
        """Path choices for a trailing `@partial` token, or None when absent.

        Entries come from the working directory: directories first, then by
        name, with dotfiles only once the partial itself starts with a dot.
        Only files that can be attached (`ATTACHABLE_SUFFIXES`) are listed —
        `@` is the attachment syntax, so an unrelated source file is not a
        choice; a partial matching no such file leaves the dropdown hidden. A
        partial may already contain directories (`@sub/fi`), in which case the
        deepest existing directory is listed; applying a directory appends the
        slash and no trailing space, so the next level can be completed.

        The option id is the whole replacement document — the text before the
        `@`, the path (quoted when it contains whitespace), and a trailing
        space for a file — because Tab, Enter, and clicks all rewrite the
        input from the id.
        """
        match = _ATTACHMENT_PARTIAL.search(value)
        if match is None:
            return None

        partial = match.group(0)[1:]
        quoted = partial.startswith('"')
        if quoted:
            partial = partial[1:]

        directory_part, _, name_part = partial.rpartition("/")
        base = Path.cwd() / directory_part if directory_part else Path.cwd()
        if not base.is_dir():
            return None

        try:
            entries = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except OSError:
            return None

        prefix = value[: match.start()]
        options: list[Option] = []
        for entry in entries:
            name = entry.name
            if name.startswith(".") and not name_part.startswith("."):
                continue
            if not name.startswith(name_part):
                continue
            is_dir = entry.is_dir()
            if not is_dir and entry.suffix.lower().lstrip(".") not in ATTACHABLE_SUFFIXES:
                continue
            relative = f"{directory_part}/{name}" if directory_part else name
            if is_dir:
                prompt = f"@{relative}/"
                written = f"{relative}/"
                suffix = ""  # keep typing after the slash to descend
            else:
                prompt = f"@{relative}"
                written = relative
                suffix = " "
            if quoted or " " in written:
                written = f'"{written}"'
            options.append(Option(prompt, id=f"{prefix}@{written}{suffix}"))
            if len(options) == MAX_ATTACHMENT_OPTIONS:
                break
        return options or None

    def _get_model_options(self, value: str) -> list[Option] | None:
        """Inline model choices for `/model`, or None when not applicable.

        Each option's id is the command to run; the prompt is the label.
        With no filter, the settings entry is listed first so plain Enter
        keeps opening the full picker (thinking level lives there).
        """
        if value == "/model":
            arg = ""
        elif value.startswith("/model "):
            arg = value[len("/model ") :].strip().lower()
        else:
            return None

        options: list[Option] = []
        if not arg:
            options.append(Option("⚙ model settings…", id="/model "))
        matches = [m for m in self._models if arg in m.lower()]
        for model in matches[:20]:
            options.append(Option(model, id=f"/model {model} "))
        return options or None

    def compose(self) -> ComposeResult:
        yield PromptTextArea(
            self,
            placeholder="\u258c Type here… (shift+enter for a new line)",
            id="prompt-inner",
            soft_wrap=True,
            tab_behavior="focus",
            show_line_numbers=False,
        )
        yield OptionList(id="suggestions")

    def on_mount(self) -> None:
        """Hide suggestions on mount."""
        self.query_one("#suggestions", OptionList).display = False

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Show/hide suggestions based on input."""
        option_list = self.query_one("#suggestions", OptionList)
        value = event.text_area.text
        if self._suppress_history_reset:
            self._suppress_history_reset = False
        else:
            if self._history_index is not None:
                self._history_index = None
            self._history_draft = value

        if value.startswith("/model"):
            # Inline model picker: choices render in the dropdown above the
            # input line instead of the full-screen modal.
            model_options = self._get_model_options(value)
            filtered = [str(o.id) for o in model_options] if model_options else []
            option_objects = model_options
        elif (attachment_options := self._get_attachment_options(value)) is not None:
            # `@partial` file completion: like model choices, the id is the
            # document to write back, the label is what the user reads.
            filtered = [str(o.id) for o in attachment_options]
            option_objects = attachment_options
        elif value.startswith("/"):
            # Filter commands matching input
            query = value.lower()
            all_commands = self._get_slash_commands()
            filtered = [cmd for cmd in all_commands if cmd.lower().startswith(query)]
            option_objects = None
        elif value.startswith("$"):
            query = value.lower()
            all_commands = self._get_skill_commands()
            filtered = [cmd for cmd in all_commands if cmd.lower().startswith(query)]
            option_objects = None
        else:
            filtered = []
            option_objects = None

        if filtered:
            option_list.clear_options()
            if option_objects is not None:
                option_list.add_options(option_objects)
            else:
                option_list.add_options([Option(cmd) for cmd in filtered])
            option_list.highlighted = 0
            # Position dropdown above input - adjust offset based on item count
            # Each item is 1 row, plus 2 for border, plus gap to reach -13 at max
            item_count = min(len(filtered), 10)  # max-height is 10
            offset_y = -(item_count + 7)
            option_list.styles.offset = (0, offset_y)
            option_list.display = True
        else:
            option_list.display = False

    def _apply_highlighted(self) -> None:
        """Fill the input with the highlighted suggestion and hide the list."""
        option_list = self.query_one("#suggestions", OptionList)
        input_widget = self.query_one("#prompt-inner", PromptTextArea)
        if not option_list.display or option_list.highlighted is None:
            return
        option = option_list.get_option_at_index(option_list.highlighted)
        if option is None:
            return
        # The option id is the command to run (model options carry a command
        # that differs from the visible label); plain options have no id.
        value = str(option.id) if option.id is not None else str(option.prompt)
        if not value.endswith(" "):
            value += " "
        input_widget.set_text_and_caret(value)
        option_list.display = False

    def _submit(self) -> None:
        """Handle enter key - pick the highlighted suggestion, then submit.

        A single Enter (or click on a suggestion) both applies the suggestion
        and runs it; Tab stays apply-only.
        """
        option_list = self.query_one("#suggestions", OptionList)
        input_widget = self.query_one("#prompt-inner", PromptTextArea)

        if option_list.display and option_list.highlighted is not None:
            self._apply_highlighted()

        # Submit the input
        value = input_widget.text.strip()
        if value:
            lower = value.lower()
            excluded = {
                "/clear",
                "/new",
                "/load",
                "/resume",
                "/fork",
                "/tree",
                "/quit",
                "/help",
                "/context",
                "/model",
                "/chat",
                "/tools",
            }
            if lower.startswith("/model "):
                excluded.add(lower.split(" ", 1)[0])
            if lower not in excluded and (not self._history or value != self._history[-1]):
                self._history.append(value)
            self._history_index = None
            self._history_draft = ""
            # The submit originates from the inner TextArea's key handler, so
            # the implicit sender would be the TextArea; make the message
            # explicitly come from this widget so "#prompt-input" selectors
            # on Submitted keep working.
            self.post_message(self.Submitted(value).set_sender(self))

    def _handle_vertical_key(self, key: str) -> bool:
        """Handle up/down for suggestions and history.

        Returns True when the key was consumed. History navigation only applies
        while the prompt is a single line; multi-line prompts fall back to
        caret movement.
        """
        option_list = self.query_one("#suggestions", OptionList)
        input_widget = self.query_one("#prompt-inner", PromptTextArea)
        single_line = "\n" not in input_widget.text

        if not option_list.display:
            if not single_line:
                return False
            if not self._history:
                return False
            if key == "up":
                if self._history_index is None:
                    self._history_draft = input_widget.text
                    self._history_index = len(self._history) - 1
                elif self._history_index > 0:
                    self._history_index -= 1
                value = self._history[self._history_index]
                self._set_input_value(value, reset_history=False)
            else:
                if self._history_index is None:
                    return False
                if self._history_index < len(self._history) - 1:
                    self._history_index += 1
                    value = self._history[self._history_index]
                    self._set_input_value(value, reset_history=False)
                else:
                    self._history_index = None
                    self._set_input_value(self._history_draft, reset_history=False)
            return True

        if key == "up":
            if option_list.highlighted is not None and option_list.highlighted > 0:
                option_list.highlighted -= 1
            elif option_list.option_count > 0:
                option_list.highlighted = option_list.option_count - 1
            return True

        if key == "down":
            if option_list.highlighted is not None:
                if option_list.highlighted < option_list.option_count - 1:
                    option_list.highlighted += 1
                else:
                    option_list.highlighted = 0
            elif option_list.option_count > 0:
                option_list.highlighted = 0
            return True

        return False

    def on_key(self, event: Key) -> None:
        """Handle remaining keys that bubble up from the TextArea."""
        option_list = self.query_one("#suggestions", OptionList)

        def apply_suggestion() -> None:
            if option_list.highlighted is None:
                return
            option = option_list.get_option_at_index(option_list.highlighted)
            if not option:
                return
            # Options that carry an id (model choices, `@path` completions) store
            # the document to write back there, same as Enter and clicks.
            value = str(option.id) if option.id is not None else str(option.prompt)
            self._set_input_value(value, reset_history=True)
            option_list.display = False

        if event.key == "tab":
            event.stop()
            # Tab selects the current suggestion
            apply_suggestion()

        elif event.key == "escape":
            if option_list.display:
                event.stop()
                option_list.display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle mouse click on suggestion: apply it and run it."""
        event.stop()
        option_list = self.query_one("#suggestions", OptionList)
        input_widget = self.query_one("#prompt-inner", PromptTextArea)
        value = str(event.option.id) if event.option.id is not None else str(event.option.prompt)
        if not value.endswith(" "):
            value += " "
        input_widget.set_text_and_caret(value)
        option_list.display = False
        self._submit()

    def focus(self, scroll_visible: bool = True) -> PromptInput:
        """Focus the inner input."""
        self.query_one("#prompt-inner", PromptTextArea).focus(scroll_visible)
        return self

    def clear(self) -> None:
        """Clear the input."""
        self.query_one("#prompt-inner", PromptTextArea).set_text_and_caret("")
