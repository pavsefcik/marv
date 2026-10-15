"""PDF operations driven by external engines: poppler, Ghostscript, and qpdf.

No Python PDF library is involved. Each action shells out to the engine that
does that job well, which keeps marv dependency-free at the cost of a clear
"install this" error when an engine is missing.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from marv.tools.base import BaseTool, ToolError

PdfAction = Literal["info", "text", "render", "compress", "merge", "split", "rotate"]
ImageFormat = Literal["png", "jpeg"]
Quality = Literal["screen", "ebook", "printer", "prepress"]

#: Seconds one engine invocation may run before it is killed.
DEFAULT_TIMEOUT = 120

#: Every external engine the tool drives, mapped to the Homebrew package that
#: provides it. `marv doctor` reads the same mapping, so the hints cannot drift.
ENGINES: dict[str, str] = {
    "pdfinfo": "poppler",
    "pdftotext": "poppler",
    "pdftoppm": "poppler",
    "gs": "ghostscript",
    "qpdf": "qpdf",
}

#: pdftoppm's image flag and the extension it writes for it (jpeg means `.jpg`).
_IMAGE_OUTPUT = {"png": ("-png", "png"), "jpeg": ("-jpeg", "jpg")}

#: Rotations qpdf can apply as a single positive quarter-turn multiple.
_ROTATIONS = (90, 180, 270)

_MERGE_SEPARATORS = re.compile(r"[,\n]")


class PdfParams(BaseModel):
    """Parameters for pdf tool."""

    action: PdfAction = Field(
        description=("Operation to run: info, text, render, compress, merge, split or rotate")
    )
    input: str = Field(
        description="Input PDF path; for merge, comma- or newline-separated PDF paths"
    )
    output: str | None = Field(
        default=None,
        description=(
            "Output path. Required for merge; other writing actions default to a "
            "sibling file. For render this is a file name prefix (pdftoppm appends "
            "`-1.png`)."
        ),
    )
    pages: str | None = Field(
        default=None,
        description="Page range such as `1-3` or `2`, used by render and split",
    )
    dpi: int = Field(default=150, description="Render resolution in dots per inch")
    image_format: ImageFormat = Field(default="png", description="Render image format")
    quality: Quality = Field(
        default="ebook", description="Ghostscript -dPDFSETTINGS preset used by compress"
    )
    rotate: int = Field(
        default=90, description="Rotation for rotate: 90, 180 or 270 degrees clockwise"
    )


class PdfTool(BaseTool[PdfParams]):
    """Read, extract, render, and rewrite PDFs with external engines."""

    name = "pdf"
    description = (
        "Work with PDF files by driving external engines (poppler, Ghostscript, qpdf). "
        "Actions: info (metadata), text (extract text), render (pages to images), "
        "compress, merge, split, rotate. Writes go to a new file - the input is never "
        "overwritten - and a missing engine names the exact install command."
    )
    parameters = PdfParams

    async def execute(self, params: PdfParams) -> str:
        """Execute the pdf tool."""
        if params.action == "merge":
            return await self._merge(params)

        source = Path(params.input).expanduser()
        if not source.is_file():
            raise ToolError(f"Input not found: {params.input}")

        match params.action:
            case "info":
                return await self._info(source)
            case "text":
                return await self._text(source, params)
            case "render":
                return await self._render(source, params)
            case "compress":
                return await self._compress(source, params)
            case "split":
                return await self._split(source, params)
            case _:
                return await self._rotate(source, params)

    async def _info(self, source: Path) -> str:
        """`pdfinfo`: document metadata, printed as text."""
        stdout, _ = await _run([_engine("pdfinfo"), str(source)])
        return stdout.strip() or "`pdfinfo` produced no output"

    async def _text(self, source: Path, params: PdfParams) -> str:
        """`pdftotext -layout`: text on stdout, or written to `output`."""
        argv = [_engine("pdftotext"), "-layout", str(source)]
        if params.output is None:
            stdout, _ = await _run([*argv, "-"])
            return stdout.rstrip("\n") or "`pdftotext` produced no text"
        output = _output_path(params.output, None, action="text", inputs=[source])
        await _run([*argv, str(output)])
        return _written(_verify([output], "pdftotext"))

    async def _render(self, source: Path, params: PdfParams) -> str:
        """`pdftoppm`: one image per page, named `<prefix>-<page>.<ext>`."""
        engine = _engine("pdftoppm")
        flag, extension = _IMAGE_OUTPUT[params.image_format]
        prefix = Path(params.output).expanduser() if params.output else source.with_suffix("")
        prefix.parent.mkdir(parents=True, exist_ok=True)

        argv = [engine, "-r", str(params.dpi), flag]
        if (bounds := _page_bounds(params.pages)) is not None:
            first, last = bounds
            argv += ["-f", str(first), "-l", str(last)]
        await _run([*argv, str(source), str(prefix)])

        produced = sorted(prefix.parent.glob(f"{prefix.name}-[0-9]*.{extension}"))
        if not produced:
            raise ToolError(f"`pdftoppm` wrote no images for prefix {prefix}")
        return _written(_verify(produced, "pdftoppm"))

    async def _compress(self, source: Path, params: PdfParams) -> str:
        """`gs -sDEVICE=pdfwrite`: rewrite the PDF at one quality preset."""
        engine = _engine("gs")
        output = _output_path(
            params.output, _sibling(source, "-compressed"), action="compress", inputs=[source]
        )
        await _run(
            [
                engine,
                "-sDEVICE=pdfwrite",
                f"-dPDFSETTINGS=/{params.quality}",
                "-o",
                str(output),
                str(source),
            ]
        )
        return _written(_verify([output], "gs"))

    async def _merge(self, params: PdfParams) -> str:
        """`qpdf --empty --pages`: concatenate whole PDFs in the given order."""
        engine = _engine("qpdf")
        sources = _merge_inputs(params.input)
        for source in sources:
            if not source.is_file():
                raise ToolError(f"Input not found: {source}")
        if params.pages:
            raise ToolError("merge takes whole files; `pages` is only used by render and split")

        output = _output_path(params.output, None, action="merge", inputs=sources)
        await _run(
            [engine, "--empty", "--pages", *(str(source) for source in sources), "--", str(output)]
        )
        return _written(_verify([output], "qpdf"))

    async def _split(self, source: Path, params: PdfParams) -> str:
        """`qpdf --pages` for one range, `qpdf --split-pages` for one file per page."""
        engine = _engine("qpdf")
        if params.pages:
            output = _output_path(
                params.output, _sibling(source, "-split"), action="split", inputs=[source]
            )
            await _run(
                [engine, str(source), "--pages", str(source), params.pages, "--", str(output)]
            )
            return _written(_verify([output], "qpdf"))

        # qpdf demands a `%d` placeholder when a split produces more than one file.
        output = _output_path(
            params.output, _sibling(source, "-%d"), action="split", inputs=[source]
        )
        await _run([engine, str(source), "--split-pages", str(output)])
        return _written(_verify(_split_outputs(output), "qpdf"))

    async def _rotate(self, source: Path, params: PdfParams) -> str:
        """`qpdf --rotate`: rotate every page by a positive quarter-turn multiple."""
        engine = _engine("qpdf")
        if params.rotate not in _ROTATIONS:
            allowed = ", ".join(str(value) for value in _ROTATIONS)
            raise ToolError(f"rotate must be one of {allowed}; got {params.rotate}")

        output = _output_path(
            params.output, _sibling(source, "-rotated"), action="rotate", inputs=[source]
        )
        await _run([engine, str(source), f"--rotate=+{params.rotate}:1-z", "--", str(output)])
        return _written(_verify([output], "qpdf"))


async def run_argv(argv: list[str], *, timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    """Run one engine command and return (exit code, stdout, stderr).

    Never goes through a shell. A child that outlives *timeout* is killed and
    TimeoutError is raised. Tests monkeypatch this module-level seam.
    """
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    code = process.returncode if process.returncode is not None else 0
    return (
        code,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def _run(argv: list[str]) -> tuple[str, str]:
    """Run an engine, turning a timeout or a non-zero exit into a ToolError."""
    try:
        code, stdout, stderr = await run_argv(argv)
    except TimeoutError as err:
        raise ToolError(
            f"`{Path(argv[0]).name}` timed out after {DEFAULT_TIMEOUT} seconds"
        ) from err
    if code != 0:
        detail = stderr.strip() or stdout.strip() or f"exit code {code}"
        raise ToolError(f"`{Path(argv[0]).name}` failed (exit {code}): {detail}")
    return stdout, stderr


def _engine(binary: str) -> str:
    """Absolute path to an engine, or a ToolError naming the install command."""
    path = shutil.which(binary)
    if path is None:
        package = ENGINES[binary]
        raise ToolError(f"`{binary}` not found. Install {package}: brew install {package}")
    return path


def _page_bounds(pages: str | None) -> tuple[int, int] | None:
    """Turn `1-3` or `2` into pdftoppm's first/last page pair."""
    text = (pages or "").strip()
    if not text:
        return None

    first_text, separator, last_text = text.partition("-")
    if separator and first_text.isdigit() and last_text.isdigit():
        first, last = int(first_text), int(last_text)
        if first < 1 or last < first:
            raise ToolError(f"Invalid page range: {pages!r}; pages are 1-based and ascending")
        return first, last
    if not separator and text.isdigit() and int(text) >= 1:
        page = int(text)
        return page, page
    raise ToolError(f"Invalid page range: {pages!r} (expected a single page or `first-last`)")


def _merge_inputs(raw: str) -> list[Path]:
    """Split merge's comma- or newline-separated input into PDF paths."""
    parts = [part.strip() for part in _MERGE_SEPARATORS.split(raw)]
    paths = [Path(part).expanduser() for part in parts if part]
    if not paths:
        raise ToolError("merge needs at least one input PDF path")
    return paths


def _output_path(
    output: str | None,
    fallback: Path | None,
    *,
    action: str,
    inputs: list[Path],
) -> Path:
    """Resolve an explicit output or a sibling fallback for *action*.

    Refuses to overwrite any input and creates the output's parent directory.
    """
    target: Path
    if output is not None:
        target = Path(output).expanduser()
    elif fallback is not None:
        target = fallback
    else:
        raise ToolError(f"{action} needs an `output` path")
    if target.resolve() in {path.resolve() for path in inputs}:
        raise ToolError(f"Refusing to overwrite the input: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _sibling(source: Path, suffix: str) -> Path:
    """A new file beside the input, e.g. `report-compressed.pdf`."""
    return source.with_name(f"{source.stem}{suffix}{source.suffix}")


def _split_outputs(output: Path) -> list[Path]:
    """The files `qpdf --split-pages` wrote for *output* (a `%d` pattern or one name)."""
    if "%d" in output.name:
        return sorted(output.parent.glob(output.name.replace("%d", "*")))
    return [output] if output.exists() else []


def _verify(paths: list[Path], binary: str) -> list[Path]:
    """Confirm the engine really wrote the files it was asked for."""
    if not paths:
        raise ToolError(f"`{binary}` wrote no output")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ToolError(f"`{binary}` wrote no output: {', '.join(missing)}")
    return paths


def _written(paths: list[Path]) -> str:
    """Report the produced files as path + size."""
    listing = "\n".join(f"  {path} ({_format_size(path.stat().st_size)})" for path in paths)
    heading = "Wrote 1 file" if len(paths) == 1 else f"Wrote {len(paths)} files"
    return f"{heading}:\n{listing}"


def _format_size(size: int) -> str:
    """Human-readable file size."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{int(value)}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}PB"
