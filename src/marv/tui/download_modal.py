"""Model download modal — pick a model and watch it download.

Shown when marv has no usable model (nothing downloaded in the HF hub) or when
the remembered/selected model is missing. It offers the curated suggestions for
this machine's RAM tier, a field for any Hugging Face id, and a live progress
readout while downloading, so the fetch is never a silent background surprise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, ProgressBar, Static
from textual.widgets.option_list import Option

from marv.llm.model_download import (
    DownloadError,
    DownloadProgress,
    suggested_models,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.app import ComposeResult

    from marv.llm.model_download import DownloadHandle

#: Sentinel option id for "download a custom Hugging Face id".
CUSTOM_OPTION_ID = "__custom__"


class DownloadModal(ModalScreen[str | None]):
    """Pick a model to download, then watch it download with live progress.

    Dismisses with the model id once it is present in the local hub, or with
    ``None`` if the user cancelled.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        create_download: Callable[[str], DownloadHandle],
        installed: set[str] | None = None,
        preselected: str | None = None,
        remembered: bool = False,
        total_ram_bytes: int | None = None,
        poll_interval: float = 0.15,
    ) -> None:
        super().__init__()
        self._create_download = create_download
        self._installed = set(installed or ())
        self._preselected = preselected
        self._remembered = remembered
        self._total_ram_bytes = total_ram_bytes
        self._poll_interval = poll_interval
        self._suggestions = suggested_models(total_ram_bytes)
        self._download: DownloadHandle | None = None
        self._timer: Any = None

    # ---- compose --------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Container(id="download-modal"):
            yield Static("Download a model", id="download-title")
            with VerticalScroll(id="download-content"):
                yield Static(self._intro(), id="download-intro")
                yield Static("[bold cyan]SUGGESTED FOR THIS MAC[/]", classes="section-header")
                yield OptionList(*self._options(), id="download-options")
                yield Static("[bold cyan]CUSTOM HUGGING FACE ID[/]", classes="section-header")
                yield Input(
                    placeholder="e.g. mlx-community/Qwen3.5-4B-MLX-4bit",
                    id="download-custom",
                )
                yield Static("", id="download-progress", classes="hidden")
                yield ProgressBar(total=None, id="download-bar", classes="hidden")
            with Horizontal(id="download-actions"):
                yield Button("Download", id="download-start-btn", variant="primary")
                yield Button("Cancel", id="download-cancel-btn")

    def _intro(self) -> str:
        lines: list[str] = []
        if self._remembered and self._preselected:
            lines.append(
                f"[yellow]{self._preselected}[/] is not downloaded yet. "
                "Pick a model below to fetch it, or choose another."
            )
        else:
            lines.append("No MLX model is downloaded in the local Hugging Face hub.")
        lines.append("Downloads can be several GB; progress is shown below (esc cancels).")
        return "\n".join(lines)

    def _options(self) -> list[Option]:
        options: list[Option] = []
        seen: set[str] = set()

        if self._preselected and self._preselected not in self._installed:
            options.append(
                Option(
                    f"{self._preselected}  // remembered, not downloaded",
                    id=self._preselected,
                )
            )
            seen.add(self._preselected)

        for suggestion in self._suggestions:
            if suggestion.model_id in seen:
                continue
            seen.add(suggestion.model_id)
            options.append(
                Option(
                    f"{suggestion.label} · {suggestion.model_id}  // {suggestion.tags}",
                    id=suggestion.model_id,
                )
            )

        options.append(Option("Custom Hugging Face id…", id=CUSTOM_OPTION_ID))
        return options

    async def on_mount(self) -> None:
        """Focus the option list."""
        self.query_one("#download-options", OptionList).focus()

    # ---- choosing -------------------------------------------------------

    def _selected_model(self) -> str | None:
        custom = self.query_one("#download-custom", Input).value.strip()
        options = self.query_one("#download-options", OptionList)
        highlighted = options.highlighted
        option_id: str | None = None
        if highlighted is not None:
            option = options.get_option_at_index(highlighted)
            option_id = option.id if option is not None else None

        if option_id == CUSTOM_OPTION_ID:
            return custom or None
        # A typed custom id wins only when it is the focused/active entry.
        if custom and (option_id is None or self.query_one("#download-custom", Input).has_focus):
            return custom
        return option_id or custom or None

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Start the highlighted download when a suggestion is clicked."""
        event.stop()
        if event.option.id == CUSTOM_OPTION_ID:
            self.query_one("#download-custom", Input).focus()
            return
        if event.option.id:
            self._begin(event.option.id)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle the Download and Cancel buttons."""
        if event.button.id == "download-start-btn":
            model = self._selected_model()
            if model:
                self._begin(model)
            else:
                self.query_one("#download-custom", Input).focus()
        elif event.button.id == "download-cancel-btn":
            self.action_cancel()

    def action_cancel(self) -> None:
        """Cancel the download (if running) and close the modal."""
        if self._download is not None and not self._download.finished:
            self._download.cancel()
        self._dismiss(None)

    # ---- downloading ----------------------------------------------------

    def _begin(self, model_id: str) -> None:
        self._download = self._create_download(model_id)
        self.query_one("#download-options", OptionList).disabled = True
        self.query_one("#download-custom", Input).disabled = True
        self.query_one("#download-start-btn", Button).disabled = True
        self.query_one("#download-bar", ProgressBar).remove_class("hidden")
        progress = self.query_one("#download-progress", Static)
        progress.remove_class("hidden")
        progress.update(f"Downloading [bold]{model_id}[/]…")
        self._download.start()
        self._timer = self.set_interval(self._poll_interval, self._poll_download)

    def _poll_download(self) -> None:
        download = self._download
        if download is None:
            return
        try:
            download.poll()
        except DownloadError as exc:
            self._fail(str(exc))
            return
        self._render_progress(download.progress)
        if download.finished:
            self._finish()

    def _render_progress(self, progress: DownloadProgress) -> None:
        bar = self.query_one("#download-bar", ProgressBar)
        if progress.total_bytes > 0:
            bar.update(total=float(progress.total_bytes), progress=float(progress.downloaded_bytes))
        self.query_one("#download-progress", Static).update(f"Downloading… {progress.label()}")

    def _finish(self) -> None:
        download = self._download
        if download is None:
            return
        self._stop_timer()
        if download.state == "done":
            self._dismiss(download.model_id)
            return
        if download.state == "cancelled":
            self._dismiss(None)
            return
        self._fail(download.error or "download failed")

    def _fail(self, message: str) -> None:
        self._stop_timer()
        self._download = None
        self.query_one("#download-options", OptionList).disabled = False
        self.query_one("#download-custom", Input).disabled = False
        self.query_one("#download-start-btn", Button).disabled = False
        self.query_one("#download-bar", ProgressBar).add_class("hidden")
        progress = self.query_one("#download-progress", Static)
        progress.remove_class("hidden")
        progress.update(f"[red]Download failed:[/] {message}\nPick another model, or try again.")

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _dismiss(self, result: str | None) -> None:
        self._stop_timer()
        if self.app.screen is self:
            self.dismiss(result)
