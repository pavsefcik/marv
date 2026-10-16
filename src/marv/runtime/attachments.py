"""Load images (and chosen PDF pages) into model-ready attachments.

No Python image or PDF library is involved: an image is read and base64-encoded
directly, and PDF pages are rendered by an external engine that the machine may
already have (`pdftoppm` from poppler, or macOS `sips`) — with a clear "install
this" error when neither is present. `sips` can only render page 1; any page
beyond that needs `pdftoppm`.

An image above `DOWNSCALE_THRESHOLD_BYTES` is scaled to `MAX_IMAGE_DIMENSION`
on the long edge with `sips` before it is encoded, best-effort: without `sips`
the original bytes are sent.

Base64 lives in memory only. When a `store` is given each blob is written there
content-addressed and the attachment keeps a `ref` filename, so a session JSONL
carries a pointer instead of megabytes of pixels and a resumed session re-reads
the bytes from disk (`hydrate_attachments`).
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from marv.runtime.message import ImageAttachment, Message
from marv.tools.pdf import ENGINES

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

#: Long edge a large image is scaled to, in pixels. The common vision-encoder
#: working size: anything bigger is discarded by the model anyway.
MAX_IMAGE_DIMENSION = 1568

#: Only images above this size are re-encoded, so an already-small screenshot is
#: passed through untouched rather than paying a lossy round trip.
DOWNSCALE_THRESHOLD_BYTES = 1_500_000

#: Most pages one PDF attachment may render. Each page is a full image paid for
#: on every turn's prefill, so a whole document is not an attachment.
MAX_PDF_PAGES = 10

#: `@path` or `@"path with spaces"`, matched anywhere in a prompt.
_ATTACHMENT_TOKEN = re.compile(r'@(?:"(?P<quoted>[^"]+)"|(?P<bare>\S+))')

#: A trailing `:<pages>` is a page request only when it looks like one.
_PAGE_SPEC = re.compile(r"\d+(?:-\d+)?")


class AttachmentError(Exception):
    """An attachment could not be loaded or rendered."""


@dataclass(frozen=True, slots=True)
class AttachmentSpec:
    """One attachment request: a file and, for a PDF, the pages to render."""

    path: Path
    pages: str | None = None


def parse_attachment_paths(text: str, cwd: Path) -> tuple[str, list[AttachmentSpec]]:
    """Split `@path` attachment tokens out of a prompt.

    A token counts as an attachment only when it names an existing file whose
    suffix is an image or `.pdf`; anything else (an email address, a mention of
    a source file) is left in the text untouched. Relative paths resolve
    against ``cwd``. A trailing `:3` or `:2-4` selects PDF pages.

    Returns the text with attachment tokens removed and the ordered, unique
    specs that were recognised.
    """
    specs: list[AttachmentSpec] = []
    seen: set[AttachmentSpec] = set()
    spans: list[tuple[int, int]] = []

    for match in _ATTACHMENT_TOKEN.finditer(text):
        raw = match.group("quoted") or match.group("bare") or ""
        if not raw:
            continue
        spec = parse_attachment_spec(raw, cwd)
        if not spec.path.is_file():
            continue
        if spec.path.suffix.lower().lstrip(".") not in IMAGE_SUFFIXES | {"pdf"}:
            continue
        spans.append(match.span())
        if spec not in seen:
            seen.add(spec)
            specs.append(spec)

    if not spans:
        return text, []

    cleaned = _remove_spans(text, spans)
    return cleaned, specs


def parse_attachment_spec(raw: str, cwd: Path) -> AttachmentSpec:
    """Turn one `path[:pages]` string into a spec, resolving against ``cwd``.

    The `:<pages>` suffix is split only when the remainder matches `\\d+(-\\d+)?`,
    so a path that itself contains a colon is passed through untouched.
    """
    path_text, pages = _split_page_spec(raw)
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return AttachmentSpec(path=path, pages=pages)


def attachments_dir(session_dir: Path) -> Path:
    """Content-addressed blob directory that sits beside the sessions directory."""
    return session_dir.parent / "attachments"


def _split_page_spec(raw: str) -> tuple[str, str | None]:
    """Split a trailing `:<page-spec>` when it looks like one, else leave *raw*."""
    path_text, separator, page_text = raw.rpartition(":")
    if separator and _PAGE_SPEC.fullmatch(page_text):
        return path_text, page_text
    return raw, None


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Drop the given character spans, tidying only horizontal whitespace.

    Newlines are preserved: a multi-line prompt must not be flattened just
    because an image was attached to it.
    """
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(text[cursor:start])
        out.append(" ")
        cursor = end
    out.append(text[cursor:])
    cleaned = re.sub(r"[^\S\n]+", " ", "".join(out))
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    return cleaned.strip()


def load_attachment(spec: AttachmentSpec, store: Path | None = None) -> list[ImageAttachment]:
    """Read one attachment request into one attachment per rendered page.

    An image yields a single attachment; a PDF yields one per requested page
    (page 1 when no page is named). When ``store`` is given each blob is written
    there content-addressed and ``ref`` is set, so the session can point at the
    bytes instead of carrying the base64; ``data`` is always populated for the
    current turn.

    Raises:
        AttachmentError: the file is missing, too large, or a PDF page beyond
            page 1 was requested with no `pdftoppm` to render it.
    """
    path = spec.path
    if not path.is_file():
        raise AttachmentError(f"attachment not found: {path}")

    suffix = path.suffix.lower().lstrip(".")
    if suffix == "pdf":
        return _load_pdf(spec, store)
    if spec.pages is not None:
        raise AttachmentError(f"pages are only supported for PDFs: {path.name}:{spec.pages}")

    data = path.read_bytes()
    return [_prepare(data, _media_type_for(suffix), path.name, suffix, store)]


def load_attachments(
    specs: list[AttachmentSpec], store: Path | None = None
) -> list[ImageAttachment]:
    """Load every spec in order, flattening multi-page ranges (blocking)."""
    loaded: list[ImageAttachment] = []
    for spec in specs:
        loaded.extend(load_attachment(spec, store=store))
    return loaded


def hydrate_attachments(messages: list[Message], session_dir: Path) -> None:
    """Read persisted blobs back into each attachment's in-memory ``data``.

    A resumed session's messages carry only a ``ref``; the bytes live in
    ``attachments_dir(session_dir)``. A missing or unreadable blob is left with
    empty ``data``, so ``to_api_dict`` skips that image and the turn still runs.
    """
    store = attachments_dir(session_dir)
    for message in messages:
        for attachment in message.attachments:
            if attachment.data or not attachment.ref:
                continue
            try:
                blob = (store / attachment.ref).read_bytes()
            except OSError:
                continue
            attachment.data = base64.b64encode(blob).decode("ascii")


def _load_pdf(spec: AttachmentSpec, store: Path | None) -> list[ImageAttachment]:
    """Render the requested PDF pages, one attachment each."""
    path = spec.path
    first, last = _page_bounds(spec.pages)
    requested = last - first + 1
    if requested > MAX_PDF_PAGES:
        raise AttachmentError(
            f"{path.name}: {requested} pages requested (limit {MAX_PDF_PAGES}) — attach fewer pages"
        )

    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        pages = _render_pdf_pages(pdftoppm, path, first, last)
        return [
            _prepare(data, "image/png", f"{path.name} p.{number}", "png", store)
            for number, data in pages
        ]

    # Without pdftoppm only page 1 is reachable, and only through `sips`.
    if (first, last) != (1, 1):
        package = ENGINES["pdftoppm"]
        raise AttachmentError(
            f"cannot attach pages {first}-{last} of {path.name}: `pdftoppm` is needed for "
            f"anything but page 1. Install {package} (`brew install {package}`)."
        )

    sips = shutil.which("sips")
    if sips is None:
        package = ENGINES["pdftoppm"]
        raise AttachmentError(
            "cannot attach a PDF: no PDF renderer found. "
            f"Install {package} (`brew install {package}`) or use macOS `sips`. "
            "Only the first page is attached."
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "page.png"
        _run_engine([sips, "-s", "format", "png", "--out", str(out), str(path)])
        if not out.is_file():
            raise AttachmentError(f"sips produced no image for {path.name}")
        data = out.read_bytes()
    return [_prepare(data, "image/png", f"{path.name} p.1", "png", store)]


def _render_pdf_pages(pdftoppm: str, path: Path, first: int, last: int) -> list[tuple[int, bytes]]:
    """Render pages ``first``..``last`` to PNG bytes, one ``(page, bytes)`` each."""
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "page"
        _run_engine(
            [
                pdftoppm,
                "-png",
                "-f",
                str(first),
                "-l",
                str(last),
                "-r",
                str(PDF_RENDER_DPI),
                str(path),
                str(prefix),
            ]
        )
        rendered = sorted(Path(tmp).glob("page-*.png"), key=_rendered_page_number)
        if not rendered:
            raise AttachmentError(f"pdftoppm produced no page for {path.name}")
        return [(_rendered_page_number(png), png.read_bytes()) for png in rendered]


def _rendered_page_number(png: Path) -> int:
    """The page number pdftoppm encoded in a rendered filename (`page-7.png`)."""
    match = re.search(r"-(\d+)\.png$", png.name)
    return int(match.group(1)) if match else 0


def _page_bounds(pages: str | None) -> tuple[int, int]:
    """Turn a page spec (`3`, `2-4`, or none) into an inclusive first/last pair."""
    text = (pages or "").strip()
    if not text:
        return 1, 1

    first_text, separator, last_text = text.partition("-")
    if separator:
        if not (first_text.isdigit() and last_text.isdigit()):
            raise AttachmentError(f"invalid page range: {pages!r}")
        first, last = int(first_text), int(last_text)
        if first < 1 or last < first:
            raise AttachmentError(f"invalid page range: {pages!r}; pages are 1-based and ascending")
        return first, last
    if text.isdigit() and int(text) >= 1:
        page = int(text)
        return page, page
    raise AttachmentError(f"invalid page spec: {pages!r} (expected `N` or `first-last`)")


def _prepare(
    data: bytes,
    media_type: str,
    name: str,
    suffix: str,
    store: Path | None,
) -> ImageAttachment:
    """Downscale, size-check, store, and encode one rendered blob."""
    if len(data) > DOWNSCALE_THRESHOLD_BYTES:
        downscaled = _downscale_image(data, suffix)
        # Best-effort: a re-encode can grow an already-small/highly-compressed
        # file, so only take the result when it actually got smaller.
        if len(downscaled) < len(data):
            data = downscaled

    if len(data) > MAX_ATTACHMENT_BYTES:
        megabytes = MAX_ATTACHMENT_BYTES / (1024 * 1024)
        raise AttachmentError(
            f"attachment {name} is too large "
            f"({len(data) / (1024 * 1024):.1f} MiB; limit {megabytes:g} MiB) — "
            "resize it and try again"
        )

    ref = _store_blob(data, suffix, store) if store is not None else None
    return ImageAttachment(
        media_type=media_type,
        data=base64.b64encode(data).decode("ascii"),
        name=name,
        byte_size=len(data),
        ref=ref,
    )


def _store_blob(data: bytes, suffix: str, store: Path) -> str:
    """Write *data* content-addressed under *store* and return its filename.

    Identical bytes map to the same sha256 name, so re-attaching a file (or the
    same page rendered twice) reuses one blob rather than growing the store.
    """
    filename = f"{hashlib.sha256(data).hexdigest()}.{suffix}"
    store.mkdir(parents=True, exist_ok=True)
    target = store / filename
    if not target.is_file():
        target.write_bytes(data)
    return filename


def _media_type_for(suffix: str) -> str:
    """Resolve an image suffix to a media type, with a known-good fallback."""
    if suffix in _FALLBACK_MEDIA_TYPES:
        return _FALLBACK_MEDIA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(f"file.{suffix}")
    return guessed or "application/octet-stream"


def _downscale_image(data: bytes, suffix: str) -> bytes:
    """Scale an oversize image down to `MAX_IMAGE_DIMENSION` on the long edge.

    Best-effort by design: a machine without `sips`, and any engine or file
    failure, returns ``data`` unchanged. Losing the attachment to a resize
    attempt would be worse than sending the bytes as they are.
    """
    sips = shutil.which("sips")
    if sips is None:
        return data

    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / f"image.{suffix}"
            out = Path(tmp) / f"image-scaled.{suffix}"
            source.write_bytes(data)
            _run_engine([sips, "-Z", str(MAX_IMAGE_DIMENSION), "--out", str(out), str(source)])
            if not out.is_file():
                return data
            return out.read_bytes()
    except Exception:  # noqa: BLE001 - downscaling is best-effort
        return data


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
