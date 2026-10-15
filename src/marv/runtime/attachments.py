"""Load images (and the first page of a PDF) into model-ready attachments.

No Python image or PDF library is involved: an image is read and base64-encoded
directly, and a PDF page is rendered by an external engine that the machine may
already have (`pdftoppm` from poppler, or macOS `sips`) — with a clear "install
this" error when neither is present.
"""

from __future__ import annotations

import base64
import mimetypes
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from marv.runtime.message import ImageAttachment

#: Image formats accepted as attachments, without the leading dot.
IMAGE_SUFFIXES = frozenset(
    {"png", "jpg", "jpeg", "gif", "webp", "bmp", "heic", "heif", "tif", "tiff"}
)

#: Suffix → media type for formats `mimetypes` may not know on every machine.
_FALLBACK_MEDIA_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "heic": "image/heic",
    "heif": "image/heif",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}

#: PDF page render resolution. 150 dpi is legible for screenshots and charts
#: without producing a multi-megabyte payload.
PDF_RENDER_DPI = 150

#: Largest decoded attachment accepted, in bytes. Over this the base64 payload
#: grows by another third and would crowd out the text context.
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024

#: `@path` or `@"path with spaces"`, matched anywhere in a prompt.
_ATTACHMENT_TOKEN = re.compile(r'@(?:"(?P<quoted>[^"]+)"|(?P<bare>\S+))')


class AttachmentError(Exception):
    """An attachment could not be loaded or rendered."""


def parse_attachment_paths(text: str, cwd: Path) -> tuple[str, list[Path]]:
    """Split `@path` attachment tokens out of a prompt.

    A token counts as an attachment only when it names an existing file whose
    suffix is an image or `.pdf`; anything else (an email address, a mention of
    a source file) is left in the text untouched. Relative paths resolve
    against ``cwd``.

    Returns the text with attachment tokens removed and the ordered, unique
    paths that were recognised.
    """
    paths: list[Path] = []
    seen: set[Path] = set()
    spans: list[tuple[int, int]] = []

    for match in _ATTACHMENT_TOKEN.finditer(text):
        raw = match.group("quoted") or match.group("bare") or ""
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if not candidate.is_file():
            continue
        if candidate.suffix.lower().lstrip(".") not in IMAGE_SUFFIXES | {"pdf"}:
            continue
        spans.append(match.span())
        if candidate not in seen:
            seen.add(candidate)
            paths.append(candidate)

    if not spans:
        return text, []

    cleaned = _remove_spans(text, spans)
    return cleaned, paths


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Drop the given character spans and normalise the leftover whitespace."""
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(text[cursor:start])
        cursor = end
    out.append(text[cursor:])
    return " ".join("".join(out).split())


def load_attachment(path: Path) -> ImageAttachment:
    """Read one image or PDF page into an attachment.

    Raises:
        AttachmentError: the file is missing, too large, or a PDF with no
            usable render engine installed.
    """
    if not path.is_file():
        raise AttachmentError(f"attachment not found: {path}")

    suffix = path.suffix.lower().lstrip(".")
    if suffix == "pdf":
        data = _render_pdf_first_page(path)
        media_type = "image/png"
    else:
        data = path.read_bytes()
        media_type = _media_type_for(suffix)

    if len(data) > MAX_ATTACHMENT_BYTES:
        megabytes = MAX_ATTACHMENT_BYTES / (1024 * 1024)
        raise AttachmentError(
            f"attachment {path.name} is too large "
            f"({len(data) / (1024 * 1024):.1f} MiB; limit {megabytes:g} MiB) — "
            "resize it and try again"
        )

    return ImageAttachment(
        media_type=media_type,
        data=base64.b64encode(data).decode("ascii"),
        name=path.name,
        byte_size=len(data),
    )


def load_attachments(paths: list[Path]) -> list[ImageAttachment]:
    """Load every path in order (blocking; run in a thread off the UI loop)."""
    return [load_attachment(path) for path in paths]


def _media_type_for(suffix: str) -> str:
    """Resolve an image suffix to a media type, with a known-good fallback."""
    if suffix in _FALLBACK_MEDIA_TYPES:
        return _FALLBACK_MEDIA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(f"file.{suffix}")
    return guessed or "application/octet-stream"


def _render_pdf_first_page(path: Path) -> bytes:
    """Render page 1 of a PDF to PNG bytes with an external engine.

    Only the first page is rendered: marv has no page picker yet, and a whole
    PDF would blow the prefill budget. The limitation is stated in the error
    when no engine is available so the user knows what to install.
    """
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "page"
            _run_engine(
                [
                    pdftoppm,
                    "-png",
                    "-f",
                    "1",
                    "-l",
                    "1",
                    "-r",
                    str(PDF_RENDER_DPI),
                    str(path),
                    str(prefix),
                ]
            )
            rendered = sorted(Path(tmp).glob("page-*.png"))
            if not rendered:
                raise AttachmentError(f"pdftoppm produced no page for {path.name}")
            return rendered[0].read_bytes()

    sips = shutil.which("sips")
    if sips:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "page.png"
            _run_engine([sips, "-s", "format", "png", "--out", str(out), str(path)])
            if not out.is_file():
                raise AttachmentError(f"sips produced no image for {path.name}")
            return out.read_bytes()

    raise AttachmentError(
        "cannot attach a PDF: no PDF renderer found. "
        "Install poppler (`brew install poppler`) or use macOS `sips`. "
        "Only the first page is attached."
    )


def _run_engine(argv: list[str]) -> None:
    """Run one external engine invocation.

    Kept as a module-level seam so tests can stub the subprocess boundary.
    """
    try:
        subprocess.run(argv, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise AttachmentError(f"{Path(argv[0]).name} failed: {detail or exc}") from exc
    except OSError as exc:  # engine vanished or is not executable
        raise AttachmentError(f"could not run {Path(argv[0]).name}: {exc}") from exc
