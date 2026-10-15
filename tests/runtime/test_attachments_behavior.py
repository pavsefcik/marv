"""Behavior tests for loading @path image and PDF attachments."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from marv.runtime import attachments as attachments_module
from marv.runtime.attachments import (
    AttachmentError,
    load_attachment,
    load_attachments,
    parse_attachment_paths,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n fake image bytes"


def write_png(path: Path, data: bytes = PNG_BYTES) -> Path:
    path.write_bytes(data)
    return path


class TestParseAttachmentPaths:
    """Tests for pulling @path tokens out of a prompt."""

    def test_existing_image_is_attached_and_removed_from_text(self, temp_dir):
        image = write_png(temp_dir / "shot.png")

        text, paths = parse_attachment_paths(f"describe @{image}", temp_dir)

        assert paths == [image]
        assert text == "describe"

    def test_relative_path_resolves_against_cwd(self, temp_dir):
        write_png(temp_dir / "shot.png")

        text, paths = parse_attachment_paths("look @shot.png please", temp_dir)

        assert paths == [temp_dir / "shot.png"]
        assert text == "look please"

    def test_quoted_path_with_spaces_is_attached(self, temp_dir):
        image = write_png(temp_dir / "my shot.png")

        text, paths = parse_attachment_paths('summarize @"my shot.png"', temp_dir)

        assert paths == [image]
        assert text == "summarize"

    def test_missing_file_stays_in_the_prompt(self, temp_dir):
        text, paths = parse_attachment_paths("see @nope.png", temp_dir)

        assert paths == []
        assert text == "see @nope.png"

    def test_non_image_suffix_stays_in_the_prompt(self, temp_dir):
        (temp_dir / "notes.txt").write_text("hi")

        text, paths = parse_attachment_paths("read @notes.txt", temp_dir)

        assert paths == []
        assert text == "read @notes.txt"

    def test_duplicate_tokens_attach_once(self, temp_dir):
        image = write_png(temp_dir / "shot.png")

        _, paths = parse_attachment_paths(f"@{image} again @{image}", temp_dir)

        assert paths == [image]

    def test_prompt_without_tokens_is_untouched(self, temp_dir):
        text, paths = parse_attachment_paths("  just   text  ", temp_dir)

        assert paths == []
        assert text == "  just   text  "


class TestLoadAttachment:
    """Tests for reading image and PDF attachments."""

    def test_image_is_base64_encoded_with_its_media_type(self, temp_dir):
        image = write_png(temp_dir / "shot.png")

        attachment = load_attachment(image)

        assert attachment.media_type == "image/png"
        assert base64.b64decode(attachment.data) == PNG_BYTES
        assert attachment.name == "shot.png"
        assert attachment.byte_size == len(PNG_BYTES)

    def test_jpeg_suffix_maps_to_image_jpeg(self, temp_dir):
        image = temp_dir / "photo.jpg"
        image.write_bytes(b"jpeg bytes")

        attachment = load_attachment(image)

        assert attachment.media_type == "image/jpeg"

    def test_missing_file_raises(self, temp_dir):
        with pytest.raises(AttachmentError, match="not found"):
            load_attachment(temp_dir / "absent.png")

    def test_oversize_attachment_names_the_limit(self, temp_dir, monkeypatch):
        monkeypatch.setattr(attachments_module, "MAX_ATTACHMENT_BYTES", 4)
        image = write_png(temp_dir / "big.png", b"12345")

        with pytest.raises(AttachmentError, match="too large.*resize"):
            load_attachment(image)

    def test_load_attachments_preserves_order(self, temp_dir):
        first = write_png(temp_dir / "a.png")
        second = write_png(temp_dir / "b.png")

        loaded = load_attachments([first, second])

        assert [a.name for a in loaded] == ["a.png", "b.png"]


class TestPdfAttachment:
    """Tests for rendering only page 1 of a PDF."""

    def test_pdftoppm_renders_the_first_page(self, temp_dir, monkeypatch):
        pdf = temp_dir / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        rendered_argv: list[list[str]] = []

        def fake_run(argv: list[str]) -> None:
            rendered_argv.append(argv)
            prefix = Path(argv[-1])
            prefix.with_name(f"{prefix.name}-1.png").write_bytes(PNG_BYTES)

        monkeypatch.setattr(attachments_module, "_run_engine", fake_run)

        attachment = load_attachment(pdf)

        assert attachment.media_type == "image/png"
        assert base64.b64decode(attachment.data) == PNG_BYTES
        argv = rendered_argv[0]
        assert argv[0] == "/usr/bin/pdftoppm"
        assert argv[2:6] == ["-f", "1", "-l", "1"]

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

        attachment = load_attachment(pdf)

        assert base64.b64decode(attachment.data) == PNG_BYTES

    def test_missing_renderer_names_the_install_command(self, temp_dir, monkeypatch):
        pdf = temp_dir / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        monkeypatch.setattr(attachments_module.shutil, "which", lambda name: None)

        with pytest.raises(AttachmentError, match="poppler"):
            load_attachment(pdf)
