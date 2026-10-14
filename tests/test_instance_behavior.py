"""Behavior tests for the single-instance registry and conflict warning."""

from __future__ import annotations

import json

from marv.instance import (
    InstanceRecord,
    active_other,
    clear_record,
    conflict_warning,
    read_record,
    register,
    write_record,
)


def test_record_round_trips_through_the_registry_file(temp_dir):
    path = temp_dir / "instance.json"
    write_record(path, InstanceRecord(pid=4321, model="org/model", provider="marv-mlx"))

    record = read_record(path)

    assert record is not None
    assert record.pid == 4321
    assert record.model == "org/model"
    assert record.provider == "marv-mlx"


def test_read_record_ignores_missing_and_corrupt_files(temp_dir):
    path = temp_dir / "instance.json"
    assert read_record(path) is None

    path.write_text("{not json", encoding="utf-8")
    assert read_record(path) is None

    path.write_text(json.dumps({"model": "x"}), encoding="utf-8")  # no pid
    assert read_record(path) is None


def test_active_other_ignores_our_own_pid(temp_dir, monkeypatch):
    monkeypatch.setattr("marv.instance.process_alive", lambda pid: True)
    path = temp_dir / "instance.json"
    write_record(path, InstanceRecord(pid=123, model="a"))

    assert active_other(path, pid=123) is None
    assert active_other(path, pid=999) is not None


def test_active_other_ignores_a_dead_process(temp_dir, monkeypatch):
    monkeypatch.setattr("marv.instance.process_alive", lambda pid: False)
    path = temp_dir / "instance.json"
    write_record(path, InstanceRecord(pid=123, model="a"))

    assert active_other(path, pid=999) is None


def test_register_records_this_process(temp_dir):
    path = temp_dir / "nested" / "instance.json"

    record = register(path, model="org/model", provider="marv-mlx")

    assert record.pid > 0
    assert read_record(path) == record


def test_clear_record_removes_the_file_and_tolerates_absence(temp_dir):
    path = temp_dir / "instance.json"
    write_record(path, InstanceRecord(pid=1))

    clear_record(path)
    clear_record(path)  # second call is a no-op

    assert not path.exists()


def test_conflict_warning_reports_a_different_live_model():
    warning = conflict_warning(
        selected_model="new/model",
        other=InstanceRecord(pid=77, model="old/model"),
    )

    assert warning is not None
    assert "old/model" in warning
    assert "77" in warning


def test_conflict_warning_is_silent_for_the_same_model():
    assert (
        conflict_warning(
            selected_model="same/model",
            other=InstanceRecord(pid=77, model="same/model"),
        )
        is None
    )


def test_conflict_warning_reports_a_differently_served_model():
    warning = conflict_warning(selected_model="new/model", served_model="old/model")

    assert warning is not None
    assert "old/model" in warning


def test_conflict_warning_needs_a_selected_model_for_the_server_case():
    assert conflict_warning(selected_model=None, served_model="old/model") is None
