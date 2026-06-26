"""Tests for `app.services.task_service`.

Focuses on the business-logic invariants that the API and worker rely on:
  * `claim_for_processing` is atomic and idempotent
  * `set_progress` clamps out-of-range values
  * `safe_filename` strips path traversal
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AudioTask, AudioTaskStatus
from app.services import task_service


def test_create_task_assigns_uploaded_status_and_safe_filename(db_session: Session) -> None:
    task = task_service.create_task(db_session, "../../etc/passwd")
    db_session.commit()
    assert task.id is not None
    assert task.status == AudioTaskStatus.UPLOADED
    # Path traversal must be stripped to the basename only.
    assert task.filename == "passwd"
    # The progress field defaults to 0 so a freshly inserted row reads as
    # "not started" on the UI.
    assert task.progress == 0


def test_safe_filename_falls_back_to_default() -> None:
    assert task_service.safe_filename("") == "upload.bin"
    assert task_service.safe_filename("song.mp3") == "song.mp3"
    assert task_service.safe_filename("/tmp/song.mp3") == "song.mp3"
    # Path traversal collapses to the basename; a "name" of just separators
    # becomes the empty string, which the service replaces with the default.
    assert task_service.safe_filename("///") == "upload.bin"


def test_set_progress_clamps_out_of_range_values(db_session: Session) -> None:
    task = task_service.create_task(db_session, "song.wav")
    db_session.commit()

    task_service.set_progress(db_session, task, 150)
    assert task.progress == 100

    task_service.set_progress(db_session, task, -42)
    assert task.progress == 0

    task_service.set_progress(db_session, task, 37, "Separating drums")
    assert task.progress == 37
    assert task.current_step == "Separating drums"

    # Passing `current_step=None` must not clear the existing label.
    task_service.set_progress(db_session, task, 50)
    assert task.current_step == "Separating drums"


def test_claim_for_processing_is_atomic_and_idempotent(db_session: Session) -> None:
    task = task_service.create_task(db_session, "song.wav")
    db_session.commit()

    claimed = task_service.claim_for_processing(db_session, task.id)
    assert claimed is not None
    assert claimed.status == AudioTaskStatus.PROCESSING

    # A second claim on the same task must return None — the row is no
    # longer in UPLOADED/FAILED, so the UPDATE is a no-op.
    again = task_service.claim_for_processing(db_session, task.id)
    assert again is None


def test_claim_for_processing_404s_missing_task(db_session: Session) -> None:
    assert task_service.claim_for_processing(db_session, 99999) is None


def test_claim_for_processing_allows_retry_from_failed(db_session: Session) -> None:
    task = task_service.create_task(db_session, "song.wav")
    db_session.commit()

    # Simulate a previous failed run.
    task.status = AudioTaskStatus.FAILED
    db_session.commit()

    claimed = task_service.claim_for_processing(db_session, task.id)
    assert claimed is not None
    assert claimed.status == AudioTaskStatus.PROCESSING


def test_claim_for_processing_rejects_finished(db_session: Session) -> None:
    task = task_service.create_task(db_session, "song.wav")
    db_session.commit()

    task.status = AudioTaskStatus.FINISHED
    db_session.commit()

    # FINISHED is terminal — caller should re-upload rather than re-process.
    assert task_service.claim_for_processing(db_session, task.id) is None


def test_mark_finished_sets_timestamps_and_error_message(db_session: Session) -> None:
    task = task_service.create_task(db_session, "song.wav")
    db_session.commit()

    task_service.mark_finished(db_session, task, success=False, error_message="boom")
    assert task.status == AudioTaskStatus.FAILED
    assert task.error_message == "boom"
    assert task.finished_at is not None

    # A successful re-run clears the error and bumps progress to 100.
    task_service.mark_finished(db_session, task, success=True)
    assert task.status == AudioTaskStatus.FINISHED
    assert task.error_message is None
    assert task.progress == 100


def test_list_tasks_paginates_newest_first(db_session: Session) -> None:
    for i in range(5):
        task_service.create_task(db_session, f"song-{i}.wav")
    db_session.commit()

    page1 = task_service.list_tasks(db_session, limit=2, offset=0)
    page2 = task_service.list_tasks(db_session, limit=2, offset=2)
    page3 = task_service.list_tasks(db_session, limit=2, offset=4)

    assert [t.id for t in page1] == [5, 4]
    assert [t.id for t in page2] == [3, 2]
    assert [t.id for t in page3] == [1]


def test_delete_task_returns_the_deleted_row(db_session: Session) -> None:
    task = task_service.create_task(db_session, "song.wav")
    db_session.commit()

    deleted = task_service.delete_task(db_session, task.id)
    assert deleted is not None
    assert deleted.id == task.id
    # Re-fetching must now return None.
    assert task_service.get_task(db_session, task.id) is None
