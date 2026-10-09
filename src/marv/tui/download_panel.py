"""Model download menu — pick a model and watch it download at the input line.

Shown when marv has no usable model (nothing downloaded in the HF hub) or when
the remembered/selected model is missing. It offers the curated suggestions for
this machine's RAM tier, a field for any Hugging Face id, and a live progress
readout while downloading, so the fetch is never a silent background surprise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual.widgets import Input, OptionList, ProgressBar, Static
from textual.widgets.option_list import Option

from marv.llm.model_download import (
    DownloadError,
    DownloadProgress,
    suggested_models,
    wait_for_catalog_refresh,
)
from marv.tui.panel import DockPanel

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.app import ComposeResult

    from marv.llm.model_download import CuratedModel, DownloadHandle

#: Sentinel option id for "download a custom Hugging Face id".
CUSTOM_OPTION_ID = "__custom__"


class DownloadPanel(DockPanel[str]):
    """Pick a model to download, then watch it download with live progress.

    Dismisses with the model id once it is present in the local hub, or with
    ``None`` if the user cancelled.
    """

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
        self._suggestions = self._load_suggestions(total_ram_bytes)
        self._download: DownloadHandle | None = None
        self._timer: Any = None
        self._stage = "pick"

    @staticmethod
    def _load_suggestions(total_ram_bytes: int | None) -> list[CuratedModel]:
        """Suggestions for this machine, from the freshest catalog we can get.

        The startup refresh runs in the background; if it is still in flight,
        wait for it (bounded) so the first picker of a session shows the current
        list instead of the pre-refresh cache.
        """
        wait_for_catalog_refresh()
        return suggested_models(total_ram_bytes)

    # ---- compose --------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(self._intro(), id="download-intro", classes="panel-subtitle")
        yield OptionList(*self._options(), id="download-options", classes="panel-menu")
        yield Input(
            placeholder="e.g. mlx-community/Qwen3.5-4B-MLX-4bit",
            id="download-custom",
            classes="hidden",
        )
        yield Static("", id="download-progress", classes="panel-subtitle hidden")
        yield ProgressBar(total=None, id="download-bar", classes="hidden")
        yield Static("", id="download-hint", classes="panel-hint")

    def on_mount(self) -> None:
        self._show_hint("enter to download · type a custom id below · esc to close")
        self.focus_target()

    def focus_target(self) -> None:
        if self._stage == "pick":
            self.query_one("#download-options", OptionList).focus()
        else:
            self.focus()

    def _intro(self) -> str:
        lines: list[str] = []
        if self._remembered and self._preselected:
            lines.append(
                f"{self._preselected} is not downloaded yet. "
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
                    Text(f"{self._preselected}  // remembered, not downloaded"),
                    id=self._preselected,
                )
            )
            seen.add(self._preselected)

        for suggestion in self._suggestions:
            if suggestion.model_id in seen:
                continue
            seen.add(suggestion.model_id)
            options.append(Option(self._suggestion_text(suggestion), id=suggestion.model_id))

        options.append(Option(Text("Custom Hugging Face id…"), id=CUSTOM_OPTION_ID))
        return options

    @staticmethod
    def _suggestion_text(suggestion: CuratedModel) -> Text:
        """Two-line entry: the model id, then its tagline dimmed beneath it."""
        text = Text(suggestion.model_id)
        if suggestion.description:
            text.append("\n    ")
            text.append(suggestion.description, style="dim")
        return text

    def _show_hint(self, text: str) -> None:
        self.query_one("#download-hint", Static).update(Text(text))

    # ---- choosing -------------------------------------------------------

    def action_cancel(self) -> None:
        """Escape: cancel a running download, step back, or close."""
        if self._download is not None and not self._download.finished:
            self._download.cancel()
            self.dismiss(None)
            return
        if self._stage == "custom":
            self._show_pick()
            return
        self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter or a click starts the highlighted download."""
        event.stop()
        if event.option.id == CUSTOM_OPTION_ID:
            self._show_custom()
            return
        if event.option.id:
            self._begin(str(event.option.id))

    def _show_pick(self) -> None:
        self._stage = "pick"
        self.query_one("#download-options", OptionList).display = True
        self.query_one("#download-custom", Input).display = False
        self._show_hint("enter to download · type a custom id below · esc to close")
        self.focus_target()

    def _show_custom(self) -> None:
        self._stage = "custom"
        self.query_one("#download-options", OptionList).display = False
        custom = self.query_one("#download-custom", Input)
        custom.display = True
        custom.value = ""
        self._show_hint("enter to download this id · esc back")
        custom.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the custom field starts the download for that id."""
        model_id = event.value.strip()
        if model_id:
            self._begin(model_id)
        else:
            self._show_pick()

    # ---- downloading ----------------------------------------------------

    def _begin(self, model_id: str) -> None:
        self._stage = "progress"
        self._download = self._create_download(model_id)
        self.query_one("#download-options", OptionList).display = False
        self.query_one("#download-custom", Input).display = False
        self.query_one("#download-bar", ProgressBar).remove_class("hidden")
        progress = self.query_one("#download-progress", Static)
        progress.remove_class("hidden")
        progress.update(Text(f"Downloading {model_id}…"))
        self._show_hint("esc cancels the download")
        self.focus()
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
        self.query_one("#download-progress", Static).update(
            Text(f"Downloading… {progress.label()}")
        )

    def _finish(self) -> None:
        download = self._download
        if download is None:
            return
        self._stop_timer()
        if download.state == "done":
            self.dismiss(download.model_id)
            return
        if download.state == "cancelled":
            self.dismiss(None)
            return
        self._fail(download.error or "download failed")

    def _fail(self, message: str) -> None:
        self._stop_timer()
        self._download = None
        self.query_one("#download-bar", ProgressBar).add_class("hidden")
        progress = self.query_one("#download-progress", Static)
        progress.remove_class("hidden")
        progress.update(Text(f"Download failed: {message}\nPick another model, or try again."))
        self._show_pick()

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def dismiss(self, result: str | None) -> None:
        self._stop_timer()
        super().dismiss(result)
