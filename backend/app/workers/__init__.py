"""Background workers.

This package is the home for any long-running task triggered by the API.
The API should call into `audio_worker.process_task(task_id)` and let this
module own the lifecycle (status transitions, retries, error reporting).

Milestone 2 will wire this to a real queue (Celery / RQ / arq) so the API
stays non-blocking. For now `process_task` runs synchronously, which is
fine for local dev and unit tests.
"""
