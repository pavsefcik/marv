"""Behavior tests for `marv doctor`."""

from __future__ import annotations

from typer.testing import CliRunner

from marv import cli
from marv.cli.doctor import (
    FAIL,
    OK,
    WARN,
    CheckResult,
    DoctorReport,
    check_extensions_load,
    format_report,
    run_checks,
)
from marv.config import Config
from tests.test_doubles.llm_provider_fake import LLMProviderFake


def build_config(temp_dir, **overrides) -> Config:
    base = {
        "provider": "openai",
        "model": "fake-model",
        "api_key": "test",
        "session_dir": temp_dir,
    }
    base.update(overrides)
    return Config(**base)


def test_report_names_a_model_missing_for_a_cloud_provider(temp_dir):
    config = build_config(temp_dir, model=None, provider="openai")

    report = run_checks(config, LLMProviderFake(name="openai", model=""))

    model = next(result for result in report.results if result.name == "model")
    assert model.status == FAIL


def test_report_warns_when_no_guardrail_is_configured(temp_dir):
    config = build_config(temp_dir)

    report = run_checks(config, LLMProviderFake())

    guardrails = next(result for result in report.results if result.name == "guardrails")
    assert guardrails.status == WARN


def test_report_notes_read_only_mode_instead_of_warning(temp_dir):
    config = build_config(temp_dir, read_only=True)

    report = run_checks(config, LLMProviderFake())

    guardrails = next(result for result in report.results if result.name == "guardrails")
    assert guardrails.status == OK
    assert "read" in guardrails.detail


def test_report_flags_a_missing_extension_path(temp_dir):
    config = build_config(temp_dir, extensions=[temp_dir / "nope.py"])

    report = run_checks(config, LLMProviderFake())

    ext = next(result for result in report.results if result.name == "extensions")
    assert ext.status == FAIL


def test_extension_load_failure_is_reported_as_a_check(temp_dir):
    config = build_config(temp_dir, extensions=[temp_dir / "ext.py"])

    result = check_extensions_load(config, lambda: ["boom"])

    assert result.status == FAIL
    assert "boom" in result.detail


def test_extension_load_never_raises(temp_dir):
    config = build_config(temp_dir, extensions=[temp_dir / "ext.py"])

    def explode() -> list[str]:
        raise RuntimeError("kaboom")

    result = check_extensions_load(config, explode)

    assert result.status == FAIL
    assert "kaboom" in result.detail


def test_doctor_without_extensions_does_not_load_any(temp_dir):
    config = build_config(temp_dir)
    calls: list[int] = []

    report = run_checks(config, LLMProviderFake(), extension_loader=lambda: calls.append(1) or [])

    assert calls == []
    assert not any(result.name == "extension load" for result in report.results)


def test_format_report_summarizes_failures():
    report = DoctorReport(
        results=[
            CheckResult("provider", OK, "marv-mlx"),
            CheckResult("mlx server", FAIL, "not installed"),
        ]
    )

    text = format_report(report)

    assert "FAIL" in text
    assert "1 item(s) need attention" in text


def test_format_report_says_when_everything_passes():
    report = DoctorReport(results=[CheckResult("provider", OK, "marv-mlx")])

    assert "all checks passed" in format_report(report)


def test_doctor_cli_is_reachable(temp_dir, monkeypatch):
    monkeypatch.chdir(temp_dir)
    captured: dict[str, object] = {}

    def fake_doctor_command(*, config, create_llm_provider, as_json):
        captured["as_json"] = as_json

    monkeypatch.setattr("marv.cli.doctor.doctor_command", fake_doctor_command)

    result = CliRunner().invoke(cli.app, ["doctor", "--json"])

    assert result.exit_code == 0
    assert captured["as_json"] is True
