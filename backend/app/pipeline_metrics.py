"""Pipeline metrics for Prometheus.

Defines histograms, counters, and gauges that track the full audio
processing pipeline from queue ingress to final output.  All metrics
are registered with the global Prometheus REGISTRY so they appear
automatically on the ``GET /metrics`` endpoint.

Metrics covered:
  1. Queue wait time           --- UPLOADED -> PROCESSING
  2. Stage durations           --- per-step timing (stems, transcription, ...)
  3. Failure reasons           --- exception type on pipeline failure
  4. Model fallback rate       --- Demucs CLI / ADTOS rule-based fallback
  5. Memory peak               --- max RSS during task execution
  6. Queue length              --- tasks waiting per status
  7. Storage usage             --- bytes used by uploads / outputs

Usage
-----
* The API layer (``health.py``) refreshes queue-length and storage gauges
  on every ``/metrics`` scrape.
* The worker (``audio_worker.py``) records stage durations, queue wait,
  failure reasons, and memory peaks.
* Model fallback counters are incremented in ``stems.py`` and
  ``transcription.py`` whenever a primary model is unavailable.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# 1. Queue wait time
#    Measured from ``task.created_at`` to the moment the worker first
#    claims the task.  Recorded in ``process_task()``.
# ---------------------------------------------------------------------------
PIPELINE_QUEUE_WAIT_SECONDS = Histogram(
    "music_ai_pipeline_queue_wait_seconds",
    "Time a task spent waiting in queue before being picked up by a worker.",
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
)

# ---------------------------------------------------------------------------
# 2. Stage durations
#    One observation per stage per task.  The ``stage`` label identifies
#    the pipeline step (e.g. "stems", "transcription", "analysis").
# ---------------------------------------------------------------------------
PIPELINE_STAGE_DURATION_SECONDS = Histogram(
    "music_ai_pipeline_stage_duration_seconds",
    "Duration of each pipeline stage.",
    labelnames=("stage",),
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 900),
)

# Total end-to-end pipeline wall-clock (PROCESSING -> FINISHED/FAILED).
PIPELINE_TOTAL_DURATION_SECONDS = Histogram(
    "music_ai_pipeline_total_duration_seconds",
    "Total pipeline processing time.",
    buckets=(10, 30, 60, 120, 300, 600, 900, 1800, 3600),
)

# ---------------------------------------------------------------------------
# 3. Failure reasons
#    Incremented once per failed task, with the exception class name as
#    the ``exception_type`` label.  A separate high-level counter tracks
#    the overall success/failure split.
# ---------------------------------------------------------------------------
PIPELINE_FAILURES_TOTAL = Counter(
    "music_ai_pipeline_failures_total",
    "Number of pipeline failures, by exception type.",
    labelnames=("exception_type",),
)

PIPELINE_TASKS_COMPLETED = Counter(
    "music_ai_pipeline_tasks_completed",
    "Number of completed tasks, by outcome.",
    labelnames=("outcome",),
)

# ---------------------------------------------------------------------------
# 4. Model fallback
#    Incremented when a primary model is unavailable and the pipeline
#    falls back to a simpler alternative.
# ---------------------------------------------------------------------------
PIPELINE_MODEL_FALLBACK_TOTAL = Counter(
    "music_ai_pipeline_model_fallback_total",
    "Number of model fallback events.",
    labelnames=("model", "fallback_reason"),
)

# ---------------------------------------------------------------------------
# 5. Memory peak
#    Recorded at task completion.  The histogram tracks the maximum RSS
#    observed during the pipeline run.
# ---------------------------------------------------------------------------
PIPELINE_MEMORY_PEAK_BYTES = Histogram(
    "music_ai_pipeline_memory_peak_bytes",
    "Peak RSS memory during pipeline execution.",
    buckets=(
        256 * 1024 * 1024,
        512 * 1024 * 1024,
        1 * 1024 * 1024 * 1024,
        2 * 1024 * 1024 * 1024,
        4 * 1024 * 1024 * 1024,
        8 * 1024 * 1024 * 1024,
    ),
)

# ---------------------------------------------------------------------------
# 6. Queue length
#    Refreshed on every /metrics scrape.  ``TASKS_TOTAL`` in health.py
#    already covers per-status counts; this gauge adds an explicit
#    "waiting" label (UPLOADED tasks) for the queue-length metric.
# ---------------------------------------------------------------------------
PIPELINE_QUEUE_LENGTH = Gauge(
    "music_ai_pipeline_queue_length",
    "Number of tasks waiting in the processing queue (status=UPLOADED).",
)

# ---------------------------------------------------------------------------
# 7. Storage usage
#    Refreshed on every /metrics scrape.  Tracks the total bytes used
#    by uploads and outputs respectively.
# ---------------------------------------------------------------------------
PIPELINE_STORAGE_BYTES = Gauge(
    "music_ai_pipeline_storage_bytes",
    "Storage used by task uploads and outputs.",
    labelnames=("scope",),
)
