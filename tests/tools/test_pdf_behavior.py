"""Behavior tests for the pdf tool."""

from __future__ import annotations

import shutil
import sys
from typing import TYPE_CHECKING

import pytest

from marv.tools import pdf
from marv.tools.base import ToolError
from marv.tools.pdf import DEFAULT_TIMEOUT, ENGINES, PdfTool, run_argv

if TYPE_CHECKING:
    from pathlib import Path


def install(monkeypatch: pytest.MonkeyPatch, *binaries: str) -> None:
    """Make `shutil.which` report only *binaries* as installed."""
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: f"/engines/{name}" if name in binaries else None,
    )


class FakeEngine:
    """Stand-in for `run_argv`: records argv, replays a result, writes fake files."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        produces: tuple[Path, ...] = (),
        fails: BaseException | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.produces = produces
        self.fails = fails

    async def __call__(self, argv: list[str], *, timeout: float = DEFAULT_TIMEOUT):
        self.calls.append(list(argv))
        if self.fails is not None:
            raise self.fails
        if self.returncode == 0:
            for path in self.produces:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"%PDF-fake")
        return self.returncode, self.stdout, self.stderr


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    """A fake engine runner with every engine binary reported as installed."""
    install(monkeypatch, *ENGINES)
    fake = FakeEngine()
    monkeypatch.setattr(pdf, "run_argv", fake)
    return fake


@pytest.fixture
def source(temp_dir: Path) -> Path:
    """An input PDF on disk."""
    path = temp_dir / "report.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    return path


@pytest.mark.parametrize(
    ("action", "binary", "package"),
    [
        ("info", "pdfinfo", "poppler"),
        ("text", "pdftotext", "poppler"),
        ("render", "pdftoppm", "poppler"),
        ("compress", "gs", "ghostscript"),
        ("merge", "qpdf", "qpdf"),
        ("split", "qpdf", "qpdf"),
        ("rotate", "qpdf", "qpdf"),
    ],
)
@pytest.mark.asyncio
async def test_missing_engine_names_the_binary_and_the_install_command(
    temp_dir: Path,
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    binary: str,
    package: str,
):
    install(monkeypatch, *(name for name in ENGINES if name != binary))

    with pytest.raises(
        ToolError,
        match=rf"`{binary}` not found\. Install {package}: brew install {package}",
    ):
        await PdfTool().execute(
            PdfTool.parameters(
                action=action,
                input=str(source),
                output=str(temp_dir / "out.pdf"),
            )
        )


@pytest.mark.asyncio
async def test_info_returns_the_pdfinfo_metadata(source: Path, engine: FakeEngine):
    engine.stdout = "Pages:          3\nProducer:       LaTeX\n"

    result = await PdfTool().execute(PdfTool.parameters(action="info", input=str(source)))

    assert "Pages:" in result
    assert "LaTeX" in result
    assert engine.calls == [["/engines/pdfinfo", str(source)]]


@pytest.mark.asyncio
async def test_text_without_output_returns_stdout(source: Path, engine: FakeEngine):
    engine.stdout = "hello pdf\n"

    result = await PdfTool().execute(PdfTool.parameters(action="text", input=str(source)))

    assert result == "hello pdf"
    assert engine.calls == [["/engines/pdftotext", "-layout", str(source), "-"]]


@pytest.mark.asyncio
async def test_text_with_output_reports_the_written_file(
    source: Path, temp_dir: Path, engine: FakeEngine
):
    out = temp_dir / "notes.txt"
    engine.produces = (out,)

    result = await PdfTool().execute(
        PdfTool.parameters(action="text", input=str(source), output=str(out))
    )

    assert engine.calls == [["/engines/pdftotext", "-layout", str(source), str(out)]]
    assert f"Wrote 1 file:\n  {out} (9B)" in result


@pytest.mark.asyncio
async def test_render_passes_dpi_format_and_page_range(
    source: Path, temp_dir: Path, engine: FakeEngine
):
    prefix = temp_dir / "page"
    images = (temp_dir / "page-1.jpg", temp_dir / "page-2.jpg")
    engine.produces = images

    result = await PdfTool().execute(
        PdfTool.parameters(
            action="render",
            input=str(source),
            output=str(prefix),
            pages="1-2",
            dpi=300,
            image_format="jpeg",
        )
    )

    assert engine.calls == [
        [
            "/engines/pdftoppm",
            "-r",
            "300",
            "-jpeg",
            "-f",
            "1",
            "-l",
            "2",
            str(source),
            str(prefix),
        ]
    ]
    assert "Wrote 2 files" in result
    assert all(str(image) in result for image in images)


@pytest.mark.asyncio
async def test_render_defaults_to_a_png_prefix_beside_the_input(source: Path, engine: FakeEngine):
    engine.produces = (source.with_name("report-1.png"),)

    await PdfTool().execute(PdfTool.parameters(action="render", input=str(source)))

    assert engine.calls == [
        [
            "/engines/pdftoppm",
            "-r",
            "150",
            "-png",
            str(source),
            str(source.with_suffix("")),
        ]
    ]


@pytest.mark.asyncio
async def test_render_reports_when_the_engine_produces_no_images(source: Path, engine: FakeEngine):
    with pytest.raises(ToolError, match="`pdftoppm` wrote no images for prefix"):
        await PdfTool().execute(PdfTool.parameters(action="render", input=str(source)))


@pytest.mark.parametrize("pages", ["0", "3-1", "one", "2-"])
@pytest.mark.asyncio
async def test_render_rejects_an_unusable_page_range(source: Path, engine: FakeEngine, pages: str):
    with pytest.raises(ToolError, match="Invalid page range"):
        await PdfTool().execute(PdfTool.parameters(action="render", input=str(source), pages=pages))


@pytest.mark.asyncio
async def test_compress_uses_the_ghostscript_quality_preset(source: Path, engine: FakeEngine):
    out = source.with_name("report-compressed.pdf")
    engine.produces = (out,)

    result = await PdfTool().execute(
        PdfTool.parameters(action="compress", input=str(source), quality="printer")
    )

    assert engine.calls == [
        [
            "/engines/gs",
            "-sDEVICE=pdfwrite",
            "-dPDFSETTINGS=/printer",
            "-o",
            str(out),
            str(source),
        ]
    ]
    assert str(out) in result


@pytest.mark.asyncio
async def test_merge_accepts_comma_and_newline_separated_inputs(temp_dir: Path, engine: FakeEngine):
    inputs = [temp_dir / name for name in ("a.pdf", "b.pdf", "c.pdf")]
    for path in inputs:
        path.write_bytes(b"%PDF-1.4\n")
    out = temp_dir / "merged.pdf"
    engine.produces = (out,)

    await PdfTool().execute(
        PdfTool.parameters(
            action="merge",
            input=f"{inputs[0]}, {inputs[1]}\n{inputs[2]}",
            output=str(out),
        )
    )

    assert engine.calls == [
        [
            "/engines/qpdf",
            "--empty",
            "--pages",
            *(str(path) for path in inputs),
            "--",
            str(out),
        ]
    ]


@pytest.mark.asyncio
async def test_merge_needs_an_output_path(source: Path, engine: FakeEngine):
    with pytest.raises(ToolError, match="merge needs an `output` path"):
        await PdfTool().execute(PdfTool.parameters(action="merge", input=str(source)))


@pytest.mark.asyncio
async def test_merge_rejects_a_page_range(source: Path, temp_dir: Path, engine: FakeEngine):
    with pytest.raises(ToolError, match="`pages` is only used by render and split"):
        await PdfTool().execute(
            PdfTool.parameters(
                action="merge",
                input=str(source),
                output=str(temp_dir / "merged.pdf"),
                pages="1-3",
            )
        )


@pytest.mark.asyncio
async def test_split_with_pages_selects_them_with_qpdf(
    source: Path, temp_dir: Path, engine: FakeEngine
):
    out = temp_dir / "part.pdf"
    engine.produces = (out,)

    await PdfTool().execute(
        PdfTool.parameters(action="split", input=str(source), output=str(out), pages="2-4")
    )

    assert engine.calls == [
        ["/engines/qpdf", str(source), "--pages", str(source), "2-4", "--", str(out)]
    ]


@pytest.mark.asyncio
async def test_split_without_pages_writes_one_file_per_page(source: Path, engine: FakeEngine):
    pages = (source.with_name("report-1.pdf"), source.with_name("report-2.pdf"))
    engine.produces = pages

    result = await PdfTool().execute(PdfTool.parameters(action="split", input=str(source)))

    assert engine.calls == [
        ["/engines/qpdf", str(source), "--split-pages", str(source.with_name("report-%d.pdf"))]
    ]
    assert all(str(page) in result for page in pages)


@pytest.mark.asyncio
async def test_split_without_pages_accepts_a_plain_output_name(
    source: Path, temp_dir: Path, engine: FakeEngine
):
    out = temp_dir / "chunk.pdf"
    engine.produces = (out,)

    result = await PdfTool().execute(
        PdfTool.parameters(action="split", input=str(source), output=str(out))
    )

    assert engine.calls == [["/engines/qpdf", str(source), "--split-pages", str(out)]]
    assert str(out) in result


@pytest.mark.asyncio
async def test_split_reports_when_no_parts_appear(source: Path, engine: FakeEngine):
    with pytest.raises(ToolError, match="`qpdf` wrote no output"):
        await PdfTool().execute(PdfTool.parameters(action="split", input=str(source)))


@pytest.mark.asyncio
async def test_rotate_turns_every_page(source: Path, engine: FakeEngine):
    out = source.with_name("report-rotated.pdf")
    engine.produces = (out,)

    result = await PdfTool().execute(
        PdfTool.parameters(action="rotate", input=str(source), rotate=270)
    )

    assert engine.calls == [["/engines/qpdf", str(source), "--rotate=+270:1-z", "--", str(out)]]
    assert str(out) in result


@pytest.mark.asyncio
async def test_rotate_rejects_an_angle_outside_a_quarter_turn(source: Path, engine: FakeEngine):
    with pytest.raises(ToolError, match="rotate must be one of 90, 180, 270; got 45"):
        await PdfTool().execute(PdfTool.parameters(action="rotate", input=str(source), rotate=45))


@pytest.mark.asyncio
async def test_compress_refuses_to_overwrite_the_input(source: Path, engine: FakeEngine):
    with pytest.raises(ToolError, match="Refusing to overwrite the input"):
        await PdfTool().execute(
            PdfTool.parameters(action="compress", input=str(source), output=str(source))
        )

    assert engine.calls == []
    assert source.read_bytes() == b"%PDF-1.4\n"


@pytest.mark.asyncio
async def test_merge_refuses_to_overwrite_one_of_its_inputs(temp_dir: Path, engine: FakeEngine):
    first, second = (temp_dir / name for name in ("a.pdf", "b.pdf"))
    for path in (first, second):
        path.write_bytes(b"%PDF-1.4\n")

    with pytest.raises(ToolError, match="Refusing to overwrite the input"):
        await PdfTool().execute(
            PdfTool.parameters(action="merge", input=f"{first},{second}", output=str(first))
        )

    assert engine.calls == []


@pytest.mark.asyncio
async def test_writing_creates_the_output_parent_directory(
    source: Path, temp_dir: Path, engine: FakeEngine
):
    out = temp_dir / "nested" / "deep" / "small.pdf"
    engine.produces = (out,)

    result = await PdfTool().execute(
        PdfTool.parameters(action="compress", input=str(source), output=str(out))
    )

    assert out.is_file()
    assert str(out) in result


@pytest.mark.asyncio
async def test_a_missing_output_is_reported_as_an_error(source: Path, engine: FakeEngine):
    with pytest.raises(ToolError, match="`gs` wrote no output"):
        await PdfTool().execute(PdfTool.parameters(action="compress", input=str(source)))


@pytest.mark.asyncio
async def test_missing_input_is_reported(source: Path, temp_dir: Path, engine: FakeEngine):
    with pytest.raises(ToolError, match="Input not found"):
        await PdfTool().execute(PdfTool.parameters(action="info", input=str(temp_dir / "nope.pdf")))


@pytest.mark.asyncio
async def test_a_nonzero_exit_reports_stderr(source: Path, engine: FakeEngine):
    engine.returncode = 1
    engine.stderr = "Syntax Error: Couldn't read xref table\n"

    with pytest.raises(
        ToolError, match=r"`pdftotext` failed \(exit 1\): Syntax Error: Couldn't read xref table"
    ):
        await PdfTool().execute(PdfTool.parameters(action="text", input=str(source)))


@pytest.mark.asyncio
async def test_a_timeout_reports_the_engine_and_the_limit(source: Path, engine: FakeEngine):
    engine.fails = TimeoutError()

    with pytest.raises(ToolError, match="`pdfinfo` timed out after 120 seconds"):
        await PdfTool().execute(PdfTool.parameters(action="info", input=str(source)))


@pytest.mark.asyncio
async def test_run_argv_passes_through_the_exit_code_and_streams():
    code, stdout, stderr = await run_argv(
        [sys.executable, "-c", "import sys; print('hi'); sys.stderr.write('warn'); sys.exit(3)"]
    )

    assert code == 3
    assert stdout.strip() == "hi"
    assert stderr.strip() == "warn"


@pytest.mark.asyncio
async def test_run_argv_kills_a_command_that_overruns_its_timeout():
    with pytest.raises(TimeoutError):
        await run_argv(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.5,
        )
