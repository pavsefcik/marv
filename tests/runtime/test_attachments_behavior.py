"""Behavior tests for loading @path image and PDF attachments."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from marv.runtime import attachments as attachments_module
from marv.runtime.attachments import (
    AttachmentError,
    AttachmentSpec,
    attachments_dir,
    hydrate_attachments,
    load_attachment,
    load_attachments,
    parse_attachment_paths,
    parse_attachment_spec,
)
from marv.runtime.message import ImageAttachment, Message
from marv.runtime.session import Session

PNG_BYTES = b"\x89PNG\r\n\x1a\n fake image bytes"
DOWNSCALED_BYTES = b"\x89PNG\r\n\x1a\n downscaled image bytes"


def write_png(path: Path, data: bytes = PNG_BYTES) -> Path:
    path.write_bytes(data)
    return path


def oversize_png() -> bytes:
    """Image bytes over the downscale threshold but under the hard cap."""
    return PNG_BYTES + b"\0" * attachments_module.DOWNSCALE_THRESHOLD_BYTES


def load_one(spec: AttachmentSpec, store: Path | None = None) -> ImageAttachment:
    """Load a spec that is expected to yield exactly one attachment."""
    loaded = load_attachment(spec, store=store)
    assert len(loaded) == 1
    return loaded[0]


class TestParseAttachmentPaths:
    """Tests for pulling @path tokens out of a prompt."""

    def test_existing_image_is_attached_and_removed_from_text(self, temp_dir):
        image = write_png(temp_dir / "shot.png")

        text, specs = parse_attachment_paths(f"describe @{image}", temp_dir)

        assert specs == [AttachmentSpec(image)]
        assert text == "describe"

    def test_relative_path_resolves_against_cwd(self, temp_dir):
        write_png(temp_dir / "shot.png")

        text, specs = parse_attachment_paths("look @shot.png please", temp_dir)

        assert specs == [AttachmentSpec(temp_dir / "shot.png")]
        assert text == "look please"

    def test_quoted_path_with_spaces_is_attached(self, temp_dir):
        image = write_png(temp_dir / "my shot.png")

        text, specs = parse_attachment_paths('summarize @"my shot.png"', temp_dir)

        assert specs == [AttachmentSpec(image)]
        assert text == "summarize"

    def test_missing_file_stays_in_the_prompt(self, temp_dir):
        text, specs = parse_attachment_paths("see @nope.png", temp_dir)

        assert specs == []
        assert text == "see @nope.png"

    def test_non_image_suffix_stays_in_the_prompt(self, temp_dir):
        (temp_dir / "notes.txt").write_text("hi")

        text, specs = parse_attachment_paths("read @notes.txt", temp_dir)

        assert specs == []
        assert text == "read @notes.txt"

    def test_duplicate_tokens_attach_once(self, temp_dir):
        image = write_png(temp_dir / "shot.png")

        _, specs = parse_attachment_paths(f"@{image} again @{image}", temp_dir)

        assert specs == [AttachmentSpec(image)]

    def test_prompt_without_tokens_is_untouched(self, temp_dir):
        text, specs = parse_attachment_paths("  just   text  ", temp_dir)

        assert specs == []
        assert text == "  just   text  "

    def test_multiline_prompt_keeps_its_newlines(self, temp_dir):
        image = write_png(temp_dir / "shot.png")

        text, specs = parse_attachment_paths(f"look at @{image}\nand explain", temp_dir)

        assert specs == [AttachmentSpec(image)]
        assert text == "look at\nand explain"


class TestParsePageSpec:
    """Tests for the `path:pages` PDF page syntax."""

    def test_single_page_is_captured(self, temp_dir):
        pdf = temp_dir / "report.pdf"
        pdf.write_bytes(b"%PDF-1.7")

        text, specs = parse_attachment_paths("read @report.pdf:3", temp_dir)

        assert specs == [AttachmentSpec(pdf, "3")]
        assert text == "read"

    def test_page_range_is_captured(self, temp_dir):
        pdf = temp_dir / "report.pdf"
        pdf.write_bytes(b"%PDF-1.7")

        _, specs = parse_attachment_paths("read @report.pdf:2-4", temp_dir)

        assert specs == [AttachmentSpec(pdf, "2-4")]

    def test_plain_path_yields_no_page_selection(self, temp_dir):
        pdf = temp_dir / "report.pdf"
        pdf.write_bytes(b"%PDF-1.7")

        _, specs = parse_attachment_paths("read @report.pdf", temp_dir)

        assert specs == [AttachmentSpec(pdf, None)]

    def test_non_numeric_colon_is_part_of_the_path(self, temp_dir):
        weird = temp_dir / "report:2024.pdf"
        weird.write_bytes(b"%PDF-1.7")

        _, specs = parse_attachment_paths("read @report:2024.pdf", temp_dir)

        assert specs == [AttachmentSpec(weird, None)]

    def test_quoted_path_with_spaces_and_a_page(self, temp_dir):
        pdf = temp_dir / "my report.pdf"
        pdf.write_bytes(b"%PDF-1.7")

        _, specs = parse_attachment_paths('read @"my report.pdf:2"', temp_dir)

        assert specs == [AttachmentSpec(pdf, "2")]


class TestParseAttachmentSpec:
    """Tests for turning a raw `--attach` value into a spec."""

    def test_flag_value_with_page_is_split(self, temp_dir):
        spec = parse_attachment_spec("report.pdf:2-4", temp_dir)

        assert spec == AttachmentSpec(temp_dir / "report.pdf", "2-4")

    def test_flag_value_without_page_has_no_selection(self, temp_dir):
        spec = parse_attachment_spec("report.png", temp_dir)

        assert spec == AttachmentSpec(temp_dir / "report.png", None)

    def test_non_numeric_colon_is_left_in_the_path(self, temp_dir):
        spec = parse_attachment_spec("report.pdf:final", temp_dir)

        assert spec == AttachmentSpec(temp_dir / "report.pdf:final", None)


class TestLoadAttachment:
    """Tests for reading image and PDF attachments."""

    def test_image_is_base64_encoded_with_its_media_type(self, temp_dir):
        image = write_png(temp_dir / "shot.png")

        attachment = load_one(AttachmentSpec(image))

        assert attachment.media_type == "image/png"
        assert base64.b64decode(attachment.data) == PNG_BYTES
        assert attachment.name == "shot.png"
        assert attachment.byte_size == len(PNG_BYTES)

    def test_jpeg_suffix_maps_to_image_jpeg(self, temp_dir):
        image = temp_dir / "photo.jpg"
        image.write_bytes(b"jpeg bytes")

        attachment = load_one(AttachmentSpec(image))

        assert attachment.media_type == "image/jpeg"

    def test_missing_file_raises(self, temp_dir):
        with pytest.raises(AttachmentError, match="not found"):
            load_attachment(AttachmentSpec(temp_dir / "absent.png"))

    def test_oversize_attachment_names_the_limit(self, temp_dir, monkeypatch):
        monkeypatch.setattr(attachments_module, "MAX_ATTACHMENT_BYTES", 4)
        image = write_png(temp_dir / "big.png", b"12345")

        with pytest.raises(AttachmentError, match="too large.*resize"):
            load_attachment(AttachmentSpec(image))

    def test_page_selection_on_a_non_pdf_is_rejected(self, temp_dir):
        image = write_png(temp_dir / "shot.png")

        with pytest.raises(AttachmentError, match="only supported for PDFs"):
            load_attachment(AttachmentSpec(image, "2"))

    def test_load_attachments_preserves_order(self, temp_dir):
        first = write_png(temp_dir / "a.png")
        second = write_png(temp_dir / "b.png")

        loaded = load_attachments([AttachmentSpec(first), AttachmentSpec(second)])

        assert [a.name for a in loaded] == ["a.png", "b.png"]


class TestAttachmentBlobStore:
    """Tests for content-addressed blob storage."""

    def test_data_is_in_memory_only_without_a_store(self, temp_dir):
        image = write_png(temp_dir / "shot.png")

        attachment = load_one(AttachmentSpec(image))

        assert attachment.ref is None
        assert attachment.data

    def test_loaded_blob_is_named_by_the_sha256_of_its_bytes(self, temp_dir):
        image = write_png(temp_dir / "shot.png")
        store = temp_dir / "attachments"

        attachment = load_one(AttachmentSpec(image), store=store)

        assert attachment.ref is not None
        assert attachment.ref == f"{hashlib.sha256(PNG_BYTES).hexdigest()}.png"
        assert (store / attachment.ref).read_bytes() == PNG_BYTES

    def test_identical_bytes_share_one_blob(self, temp_dir):
        first = write_png(temp_dir / "a.png")
        second = write_png(temp_dir / "b.png")
        store = temp_dir / "attachments"

        one = load_one(AttachmentSpec(first), store=store)
        two = load_one(AttachmentSpec(second), store=store)

        assert one.ref == two.ref
        assert len(list(store.iterdir())) == 1


class TestAttachmentPersistence:
    """A stored attachment round-trips through a session and hydrates back."""

    def test_ref_survives_a_session_round_trip_without_the_data(self, temp_dir):
        session_dir = temp_dir / "sessions"
        image = write_png(temp_dir / "shot.png")
        attachment = load_one(AttachmentSpec(image), store=attachments_dir(session_dir))
        session = Session.new(session_dir)
        session.append(Message.user("look", attachments=[attachment]))

        loaded = Session.load(session.path)

        stored = next(m for m in loaded.messages if m.role.value == "user").attachments[0]
        assert stored.data == ""
        assert stored.ref == attachment.ref
        assert stored.name == "shot.png"

    def test_hydration_restores_the_bytes(self, temp_dir):
        session_dir = temp_dir / "sessions"
        image = write_png(temp_dir / "shot.png")
        attachment = load_one(AttachmentSpec(image), store=attachments_dir(session_dir))
        session = Session.new(session_dir)
        session.append(Message.user("look", attachments=[attachment]))

        loaded = Session.load(session.path)
        hydrate_attachments(loaded.messages, session_dir)

        message = next(m for m in loaded.messages if m.role.value == "user")
        data = message.attachments[0].data
        assert base64.b64decode(data) == PNG_BYTES
        assert message.to_api_dict()["content"] == [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
        ]


class TestMissingBlob:
    """A blob that cannot be read must not fail the turn."""

    def test_missing_blob_degrades_to_no_image_part(self, temp_dir):
        session_dir = temp_dir / "sessions"
        store = attachments_dir(session_dir)
        image = write_png(temp_dir / "shot.png")
        attachment = load_one(AttachmentSpec(image), store=store)
        session = Session.new(session_dir)
        session.append(Message.user("look", attachments=[attachment]))
        assert attachment.ref is not None
        (store / attachment.ref).unlink()

        loaded = Session.load(session.path)
        hydrate_attachments(loaded.messages, session_dir)

        message = next(m for m in loaded.messages if m.role.value == "user")
        assert message.attachments[0].data == ""
        assert message.to_api_dict()["content"] == "look"


class TestDownscaleLargeImages:
    """Tests for shrinking an oversize image at attach time."""

    def test_large_image_is_downscaled_and_encoded(self, temp_dir, monkeypatch):
        original = oversize_png()
        image = write_png(temp_dir / "photo.png", original)
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        engine_input: list[bytes] = []
        engine_argv: list[list[str]] = []

        def fake_run(argv: list[str]) -> None:
            engine_argv.append(argv)
            engine_input.append(Path(argv[-1]).read_bytes())
            Path(argv[argv.index("--out") + 1]).write_bytes(DOWNSCALED_BYTES)

        monkeypatch.setattr(attachments_module, "_run_engine", fake_run)

        attachment = load_one(AttachmentSpec(image))

        assert attachment.media_type == "image/png"
        assert attachment.byte_size == len(DOWNSCALED_BYTES)
        assert base64.b64decode(attachment.data) == DOWNSCALED_BYTES
        assert engine_input == [original]
        assert engine_argv[0][:3] == ["/usr/bin/sips", "-Z", "1568"]
        assert "--out" in engine_argv[0]

    def test_small_image_is_not_downscaled(self, temp_dir, monkeypatch):
        image = write_png(temp_dir / "shot.png")
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        engine_calls: list[list[str]] = []
        monkeypatch.setattr(attachments_module, "_run_engine", engine_calls.append)

        attachment = load_one(AttachmentSpec(image))

        assert engine_calls == []
        assert attachment.byte_size == len(PNG_BYTES)
        assert base64.b64decode(attachment.data) == PNG_BYTES

    def test_missing_sips_keeps_the_original_bytes(self, temp_dir, monkeypatch):
        original = oversize_png()
        image = write_png(temp_dir / "photo.png", original)
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: None)
        engine_calls: list[list[str]] = []
        monkeypatch.setattr(attachments_module, "_run_engine", engine_calls.append)

        attachment = load_one(AttachmentSpec(image))

        assert engine_calls == []
        assert attachment.byte_size == len(original)
        assert base64.b64decode(attachment.data) == original

    def test_engine_failure_keeps_the_original_bytes(self, temp_dir, monkeypatch):
        original = oversize_png()
        image = write_png(temp_dir / "photo.png", original)
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: "/usr/bin/sips")

        def failing_run(argv: list[str]) -> None:
            raise AttachmentError("sips failed: nope")

        monkeypatch.setattr(attachments_module, "_run_engine", failing_run)

        attachment = load_one(AttachmentSpec(image))

        assert attachment.byte_size == len(original)
        assert base64.b64decode(attachment.data) == original

    def test_a_larger_downscale_result_keeps_the_original(self, temp_dir, monkeypatch):
        original = oversize_png()
        image = write_png(temp_dir / "photo.png", original)
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: "/usr/bin/sips")

        def bigger_run(argv: list[str]) -> None:
            Path(argv[argv.index("--out") + 1]).write_bytes(original + b"padding")

        monkeypatch.setattr(attachments_module, "_run_engine", bigger_run)

        attachment = load_one(AttachmentSpec(image))

        assert attachment.byte_size == len(original)
        assert base64.b64decode(attachment.data) == original


class TestPdfAttachment:
    """Tests for rendering PDF pages."""

    def test_pdftoppm_renders_the_first_page_by_default(self, temp_dir, monkeypatch):
        pdf = temp_dir / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        rendered_argv: list[list[str]] = []

        def fake_run(argv: list[str]) -> None:
            rendered_argv.append(argv)
            prefix = Path(argv[-1])
            prefix.with_name(f"{prefix.name}-1.png").write_bytes(PNG_BYTES)

        monkeypatch.setattr(attachments_module, "_run_engine", fake_run)

        attachment = load_one(AttachmentSpec(pdf))

        assert attachment.media_type == "image/png"
        assert base64.b64decode(attachment.data) == PNG_BYTES
        assert attachment.name == "doc.pdf p.1"
        argv = rendered_argv[0]
        assert argv[0] == "/usr/bin/pdftoppm"
        assert argv[2:6] == ["-f", "1", "-l", "1"]

    def test_requested_pages_render_one_attachment_each_in_order(self, temp_dir, monkeypatch):
        pdf = temp_dir / "report.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        rendered_argv: list[list[str]] = []

        def fake_run(argv: list[str]) -> None:
            rendered_argv.append(argv)
            first = int(argv[argv.index("-f") + 1])
            last = int(argv[argv.index("-l") + 1])
            prefix = Path(argv[-1])
            for page in range(first, last + 1):
                prefix.with_name(f"{prefix.name}-{page}.png").write_bytes(PNG_BYTES + bytes([page]))

        monkeypatch.setattr(attachments_module, "_run_engine", fake_run)

        loaded = load_attachment(AttachmentSpec(pdf, "2-4"))

        assert [a.name for a in loaded] == ["report.pdf p.2", "report.pdf p.3", "report.pdf p.4"]
        assert [base64.b64decode(a.data)[-1] for a in loaded] == [2, 3, 4]
        argv = rendered_argv[0]
        assert argv[argv.index("-f") + 1] == "2"
        assert argv[argv.index("-l") + 1] == "4"

    def test_page_beyond_one_without_pdftoppm_names_poppler(self, temp_dir, monkeypatch):
        pdf = temp_dir / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        monkeypatch.setattr(
            attachments_module.shutil,
            "which",
            lambda name: None if name == "pdftoppm" else f"/usr/bin/{name}",
        )

        with pytest.raises(AttachmentError, match="poppler"):
            load_attachment(AttachmentSpec(pdf, "3"))

    def test_page_range_without_pdftoppm_names_poppler(self, temp_dir, monkeypatch):
        pdf = temp_dir / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: None)

        with pytest.raises(AttachmentError, match="poppler"):
            load_attachment(AttachmentSpec(pdf, "2-4"))

    def test_too_many_pages_is_rejected(self, temp_dir):
        pdf = temp_dir / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.7")

        with pytest.raises(AttachmentError, match=f"limit {attachments_module.MAX_PDF_PAGES}"):
            load_attachment(AttachmentSpec(pdf, "1-11"))

    def test_sips_fallback_when_poppler_is_absent(self, temp_dir, monkeypatch):
        pdf = temp_dir / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        monkeypatch.setattr(
            attachments_module.shutil,
            "which",
            lambda name: None if name == "pdftoppm" else f"/usr/bin/{name}",
        )

        def fake_run(argv: list[str]) -> None:
            Path(argv[-2]).write_bytes(PNG_BYTES)

        monkeypatch.setattr(attachments_module, "_run_engine", fake_run)

        attachment = load_one(AttachmentSpec(pdf, "1"))

        assert base64.b64decode(attachment.data) == PNG_BYTES

    def test_missing_renderer_names_the_install_command(self, temp_dir, monkeypatch):
        pdf = temp_dir / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: None)

        with pytest.raises(AttachmentError, match="poppler"):
            load_attachment(AttachmentSpec(pdf))
