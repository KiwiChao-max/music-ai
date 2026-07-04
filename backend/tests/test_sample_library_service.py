"""Tests for `app.services.sample_library_service`.

The service is responsible for:
  * creating libraries from (filename, content) pairs;
  * mapping filenames to GM percussion notes by alias lookup;
  * atomic single-active-library management;
  * safe file deletion that cleans both the DB rows and the on-disk
    directory.

The tests run against the same in-memory SQLite used by the rest of the
suite, with the on-disk root pointed at a per-test temp dir via the
`storage_dir` fixture.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.services import sample_library_service
from app.services.sample_library_service import (
    SampleLibraryService,
    _resolve_note_from_name,
    _resolve_velocity_range,
    _safe_filename,
)


def test_safe_filename_strips_directories_and_filters_unknown_ext() -> None:
    # Directory traversal is collapsed to the basename — `Path.name` does
    # the heavy lifting, and we just confirm our wrapper respects it.
    assert _safe_filename("../../etc/kick.wav") == "kick.wav"
    # Anything without a recognised audio extension is rejected.
    assert _safe_filename("notes.txt") is None
    assert _safe_filename("") is None
    assert _safe_filename(".hidden") is None


@pytest.mark.parametrize(
    "filename,expected_note",
    [
        ("kick.wav", 36),
        ("KICK.WAV", 36),  # case-insensitive
        ("bass_drum.wav", 36),
        ("snare_01.wav", 38),  # trailing round-robin number stripped
        ("Closed-Hat.wav", 42),  # dash → underscore normalization
        ("open_hat.wav", 46),
        ("crash.wav", 49),
        ("ride.wav", 51),
        ("china.wav", 52),
        ("splash.wav", 55),
        ("cowbell.wav", 56),
        ("tambourine.wav", 54),
        ("claves.wav", 75),
        ("studio_kick.wav", 36),  # token match on "kick"
    ],
)
def test_resolve_note_from_name_maps_common_aliases(
    filename: str, expected_note: int
) -> None:
    assert _resolve_note_from_name(filename) == expected_note


def test_resolve_note_from_name_returns_none_for_unrecognised() -> None:
    assert _resolve_note_from_name("piano_c4.wav") is None
    assert _resolve_note_from_name("unknown_drum.wav") is None


def test_create_library_persists_files_and_maps_notes(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    info = service.create_library(
        db_session,
        name="Studio kit",
        description="Recorded 2025",
        files=[
            ("kick.wav", b"kick-bytes"),
            ("snare.wav", b"snare-bytes"),
            ("open_hat.wav", b"hat-bytes"),
            ("notes.txt", b"ignored"),  # wrong extension → dropped
            ("unknown_drum.wav", b"ignored"),  # unrecognised name → dropped
        ],
    )
    db_session.commit()

    assert info.id is not None
    assert info.name == "Studio kit"
    assert info.description == "Recorded 2025"
    assert info.is_active is False
    notes = sorted(sample.midi_note for sample in info.files)
    assert notes == [36, 38, 46]
    # On-disk files were actually written.
    library_dir = storage_dir / "sample-libraries" / str(info.id)
    assert (library_dir / "kick.wav").read_bytes() == b"kick-bytes"


def test_create_library_rejects_empty_payload(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    with pytest.raises(ValueError, match="name is required"):
        service.create_library(db_session, name="   ", files=[("kick.wav", b"x")])
    with pytest.raises(ValueError, match="at least one sample"):
        service.create_library(db_session, name="Kit", files=[])


def test_create_library_rolls_back_when_no_recognised_samples(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    with pytest.raises(ValueError, match="no samples could be mapped to GM drum notes"):
        service.create_library(
            db_session,
            name="Bad kit",
            files=[("piano_c4.wav", b"x")],
        )
    # The directory written before the rollback must be cleaned up so we
    # never leave orphan files behind.
    root = storage_dir / "sample-libraries"
    assert not any(root.iterdir()) if root.exists() else True


def test_activate_only_one_library_active_at_a_time(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    a = service.create_library(
        db_session, name="Kit A", files=[("kick.wav", b"a")]
    )
    b = service.create_library(
        db_session, name="Kit B", files=[("snare.wav", b"b")]
    )
    db_session.commit()

    service.activate(db_session, a.id)
    assert service.active_library(db_session).id == a.id
    service.activate(db_session, b.id)
    active = service.active_library(db_session)
    assert active.id == b.id
    # Library A must now be marked inactive.
    a_fresh = service.get_library(db_session, a.id)
    assert a_fresh.is_active is False


def test_delete_library_removes_files_and_db_rows(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    info = service.create_library(
        db_session, name="Doomed", files=[("kick.wav", b"x")]
    )
    db_session.commit()
    library_dir = storage_dir / "sample-libraries" / str(info.id)
    assert library_dir.is_dir()

    assert service.delete_library(db_session, info.id) is True
    assert not library_dir.exists()
    assert service.get_library(db_session, info.id) is None
    # Deleting a non-existent library returns False rather than raising.
    assert service.delete_library(db_session, 99999) is False


def test_list_libraries_returns_all_libraries_with_files(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    a = service.create_library(
        db_session, name="First", files=[("kick.wav", b"a")]
    )
    db_session.commit()
    b = service.create_library(
        db_session, name="Second", files=[("snare.wav", b"b")]
    )
    db_session.commit()

    listed = service.list_libraries(db_session)
    # Both libraries come back, each with its own files populated.
    assert {library.id for library in listed} == {a.id, b.id}
    by_id = {library.id: library for library in listed}
    assert {sample.midi_note for sample in by_id[a.id].files} == {36}
    assert {sample.midi_note for sample in by_id[b.id].files} == {38}


def test_list_libraries_preserves_velocity_layers_through_schema(
    db_session: Session, storage_dir: Path
) -> None:
    """The SampleFileInfo schema must surface velocity_min/velocity_max.

    Regression test: previously these fields were dropped at the API
    boundary because SampleFileInfo did not declare them, so the
    frontend's sample-based drum player could never pick the right
    velocity layer.
    """
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    info = service.create_library(
        db_session,
        name="Velocity kit",
        files=[
            ("kick_vel_001_064.wav", b"soft"),
            ("kick_vel_065_127.wav", b"hard"),
        ],
    )
    db_session.commit()

    listed = service.list_libraries(db_session)
    by_id = {lib.id: lib for lib in listed}
    files = by_id[info.id].files
    # Both layers present, each carrying its parsed velocity range.
    ranges = sorted(
        (sample.velocity_min, sample.velocity_max) for sample in files
    )
    assert ranges == [(1, 64), (65, 127)]


def test_export_library_returns_mapping(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    lib = service.create_library(
        db_session,
        name="Export Test",
        files=[
            ("kick.wav", b"k"),
            ("snare.wav", b"s"),
            ("hat_closed.wav", b"h"),
        ],
        description="For export",
    )
    db_session.commit()

    exported = service.export_library(db_session, lib.id)
    assert exported is not None
    assert exported["version"] == 1
    assert exported["name"] == "Export Test"
    assert exported["description"] == "For export"
    assert exported["format"] == "gm_percussion_mapping"
    assert exported["note_range"] == [35, 81]
    assert exported["sample_count"] == 3
    assert 36 in exported["mapping"]
    assert 38 in exported["mapping"]
    assert 42 in exported["mapping"]
    assert exported["mapping"][36]["label"] == "kick"
    assert exported["mapping"][38]["label"] == "snare"


def test_export_library_returns_none_for_missing(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    assert service.export_library(db_session, 999) is None


def test_update_sample_note_changes_mapping(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    lib = service.create_library(
        db_session, name="Update Test", files=[("kick.wav", b"k")]
    )
    db_session.commit()

    sample = lib.files[0]
    updated = service.update_sample_note(db_session, lib.id, sample.id, 38)
    assert updated is not None
    assert updated.files[0].midi_note == 38

    exported = service.export_library(db_session, lib.id)
    assert exported is not None
    assert 38 in exported["mapping"]
    assert 36 not in exported["mapping"]


def test_update_library_name(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    lib = service.create_library(
        db_session, name="Old Name", files=[("kick.wav", b"k")]
    )
    db_session.commit()

    updated = service.update_library(db_session, lib.id, name="New Name")
    assert updated is not None
    assert updated.name == "New Name"


def test_update_library_description(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    lib = service.create_library(
        db_session, name="Test", files=[("kick.wav", b"k")], description="Old desc"
    )
    db_session.commit()

    updated = service.update_library(db_session, lib.id, description="New desc")
    assert updated is not None
    assert updated.description == "New desc"


def test_update_library_empty_name_raises(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    lib = service.create_library(
        db_session, name="Test", files=[("kick.wav", b"k")]
    )
    db_session.commit()

    with pytest.raises(ValueError, match="name cannot be empty"):
        service.update_library(db_session, lib.id, name="   ")


def test_update_library_missing_returns_none(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    assert service.update_library(db_session, 999, name="Test") is None


def test_batch_remove_samples(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    lib = service.create_library(
        db_session,
        name="Batch Test",
        files=[
            ("kick.wav", b"k"),
            ("snare.wav", b"s"),
            ("hat_closed.wav", b"h"),
        ],
    )
    db_session.commit()

    sample_ids = [sf.id for sf in lib.files[:2]]
    updated = service.batch_remove_samples(db_session, lib.id, sample_ids)
    assert updated is not None
    assert len(updated.files) == 1
    assert updated.files[0].midi_note == 42


def test_batch_remove_empty_list(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    lib = service.create_library(
        db_session, name="Batch Empty", files=[("kick.wav", b"k")]
    )
    db_session.commit()

    updated = service.batch_remove_samples(db_session, lib.id, [])
    assert updated is not None
    assert len(updated.files) == 1


def test_batch_remove_missing_library(
    db_session: Session, storage_dir: Path
) -> None:
    service = SampleLibraryService(root=storage_dir / "sample-libraries")
    assert service.batch_remove_samples(db_session, 999, [1, 2]) is None


# ---- _resolve_velocity_range --------------------------------------------
@pytest.mark.parametrize(
    "filename,expected",
    [
        # No velocity info → full range.
        ("kick.wav", (1, 127)),
        # Explicit "vel_NNN_NNN" form.
        ("kick_vel_001_064.wav", (1, 64)),
        ("snare_vel_065_127.wav", (65, 127)),
        # Short-form "vN-M" / "v_N_M" / "v N M" — both numbers are
        # 1..127 velocity values.
        ("kick_v1-50.wav", (1, 50)),
        ("kick_v_51_100.wav", (51, 100)),
        ("kick_v 101 127.wav", (101, 127)),
        # Out-of-order values are swapped.
        ("kick_v100-50.wav", (50, 100)),
        # Clamped to [1, 127].
        ("kick_v0-200.wav", (1, 127)),
        # Dynamic suffixes.
        ("kick_pp.wav", (1, 42)),
        ("snare_ff.wav", (111, 127)),
        # English labels.
        ("snare_soft.wav", (1, 63)),
        ("crash_hard.wav", (64, 127)),
        # "v1" alone (layer index, no explicit upper bound) is NOT
        # consumed by the short-form range parser — it falls through
        # to (1, 127) because there is no second number.
        ("kick_v1.wav", (1, 127)),
        ("kick_v2.wav", (1, 127)),
    ],
)
def test_resolve_velocity_range_parses_supported_patterns(
    filename: str, expected: tuple[int, int]
) -> None:
    assert _resolve_velocity_range(filename) == expected

