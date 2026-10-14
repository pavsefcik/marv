"""Behavior tests for the GitHub release check and self-update."""

from __future__ import annotations

import subprocess

from marv.updates import (
    Release,
    check_for_update,
    install_release,
    install_requirement,
    is_newer,
    parse_version,
    release_from_api,
)


def test_parse_version_reads_dotted_tags_with_suffixes():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("2.0") == (2, 0)
    assert parse_version("1.120.1-rc1") == (1, 120, 1)
    assert parse_version("not-a-version") == ()


def test_is_newer_compares_numerically_not_lexically():
    assert is_newer("1.9.0", "1.10.0") is False
    assert is_newer("1.10.0", "1.9.0") is True
    assert is_newer("2.0.0", "1.999.999") is True
    assert is_newer("1.2.3", "1.2.3") is False
    assert is_newer("garbage", "1.2.3") is False


def test_release_from_api_prefers_the_attached_wheel():
    release = release_from_api(
        {
            "tag_name": "v1.121.0",
            "assets": [
                {"name": "marv-1.121.0.tar.gz", "browser_download_url": "https://x/sdist"},
                {
                    "name": "marv-1.121.0-py3-none-any.whl",
                    "browser_download_url": "https://x/wheel",
                },
            ],
        }
    )

    assert release == Release(
        version="1.121.0",
        tag="v1.121.0",
        wheel_url="https://x/wheel",
    )


def test_release_from_api_falls_back_to_the_conventional_wheel_url():
    release = release_from_api({"tag_name": "v1.122.0", "assets": []})

    assert release is not None
    assert release.version == "1.122.0"
    assert release.wheel_url.endswith("/v1.122.0/marv-1.122.0-py3-none-any.whl")


def test_release_from_api_rejects_a_payload_without_a_tag():
    assert release_from_api({"assets": []}) is None
    assert release_from_api([]) is None


def test_check_for_update_returns_the_newer_release_and_caches_it(temp_dir):
    calls: list[int] = []

    def fetcher() -> Release:
        calls.append(1)
        return Release("1.50.0", "v1.50.0", "https://x/wheel")

    cache = temp_dir / "update_check.json"

    first = check_for_update("1.49.0", cache_path=cache, now=1000.0, fetcher=fetcher)
    second = check_for_update("1.49.0", cache_path=cache, now=1001.0, fetcher=fetcher)

    assert first is not None and first.version == "1.50.0"
    assert second is not None and second.version == "1.50.0"
    assert calls == [1]  # the second call was served from cache


def test_check_for_update_does_not_report_equal_or_older_releases(temp_dir):
    release = Release("1.49.0", "v1.49.0", "https://x/wheel")

    result = check_for_update(
        "1.49.0",
        cache_path=temp_dir / "cache.json",
        now=1000.0,
        fetcher=lambda: release,
    )

    assert result is None


def test_check_for_update_force_bypasses_the_interval(temp_dir):
    calls: list[int] = []

    def fetcher() -> Release:
        calls.append(1)
        return Release("1.50.0", "v1.50.0", "https://x/wheel")

    cache = temp_dir / "cache.json"
    check_for_update("1.49.0", cache_path=cache, now=1000.0, fetcher=fetcher)
    check_for_update("1.49.0", cache_path=cache, now=1001.0, force=True, fetcher=fetcher)

    assert calls == [1, 1]


def test_check_for_update_failure_does_not_poison_the_cache(temp_dir):
    cache = temp_dir / "cache.json"

    assert check_for_update("1.49.0", cache_path=cache, now=1000.0, fetcher=lambda: None) is None
    assert not cache.exists()


def test_install_release_runs_uv_tool_install_with_the_wheel(temp_dir):
    seen: list[list[str]] = []

    def runner(argv, **_kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    outcome = install_release(
        Release("1.50.0", "v1.50.0", "https://x/marv-1.50.0-py3-none-any.whl"),
        runner=runner,
    )

    assert outcome.ok is True
    assert seen == [
        [
            "uv",
            "tool",
            "install",
            "--force",
            "--python",
            "3.14",
            "marv @ https://x/marv-1.50.0-py3-none-any.whl",
        ]
    ]


def test_install_requirement_reports_a_failed_run():
    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="boom")

    outcome = install_requirement("marv @ https://x/w.whl", runner=runner)

    assert outcome.ok is False
    assert outcome.returncode == 2
    assert outcome.stderr == "boom"


def test_install_requirement_reports_a_missing_uv():
    def runner(argv, **_kwargs):
        raise FileNotFoundError("uv")

    outcome = install_requirement("marv @ https://x/w.whl", runner=runner)

    assert outcome.ok is False
    assert "uv" in outcome.stderr
