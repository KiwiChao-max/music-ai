"""Per-task memory guard and worker lifecycle protection.

Before a heavy audio task begins, we check the current RSS of the
worker process and compare it against a configured threshold.  If the
process is already near the container limit (e.g. a previous task
leaked memory), we raise a ``MemoryPressureError`` so the task can
be re-queued to a fresh worker child instead of OOM-killing the
entire container.

Combined with Celery's ``worker_max_memory_per_child``, this provides
two layers of protection:

1. **Pre-task gate** (this module) --- refuse to start a task if the
   worker is already bloated.  The task is rejected and re-queued.
2. **Post-task recycling** (Celery ``worker_max_memory_per_child``)
   --- after a worker child exceeds the memory threshold, it is
   gracefully replaced before picking up the next task.

Both layers are optional; set ``WORKER_MEMORY_LIMIT_MB`` to 0 to
disable the pre-task gate.
"""
from __future__ import annotations

import logging
import os

from app.utils.errors import ServerError

logger = logging.getLogger(__name__)


class MemoryPressureError(ServerError):
    """Raised when the worker process is too close to the memory ceiling.

    This exception is raised inside the worker *before* starting a heavy
    task when the current RSS exceeds the pre-task gate. The task is
    re-queued so a fresh child can pick it up. It never propagates to the
    API response layer directly.
    """
    code = "memory_pressure"
    message = "Server is under memory pressure. Please retry shortly."
    status_code = 503  # Service Unavailable


def _rss_mb() -> int:
    """Return the current process RSS in MiB.

    Falls back to 0 on platforms where ``/proc/self/status`` is
    unavailable (macOS, Windows).
    """
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    # "VmRSS:   1234567 kB"
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 0


def _available_memory_mb() -> int:
    """Return the currently available system memory in MiB (MemAvailable).

    On cgroup-v2 systems this reads ``memory.current`` vs
    ``memory.max``; on v1 or bare-metal it reads ``/proc/meminfo``.
    """
    try:
        # cgroup v2
        mem_current = "/sys/fs/cgroup/memory.current"
        mem_max = "/sys/fs/cgroup/memory.max"
        if os.path.exists(mem_current) and os.path.exists(mem_max):
            with open(mem_current) as fh:
                current = int(fh.read().strip())
            with open(mem_max) as fh:
                max_val = fh.read().strip()
            if max_val != "max":
                return (int(max_val) - current) // (1024 * 1024)
    except (OSError, ValueError):
        pass
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 0


def enforce_memory_limit(limit_mb: int) -> None:
    """Raise ``MemoryPressureError`` if the worker is too close to the limit.

    Call this at the *start* of every heavy task, before loading
    Demucs / Basic Pitch / ADTOS into memory.  The check is cheap
    (a few /proc reads) and prevents the most common OOM scenario:
    a bloated worker child accepting one more heavy task.

    Parameters
    ----------
    limit_mb:
        Per-worker-child memory ceiling in MiB.  If the current RSS
        exceeds this value, the task is rejected.
    """
    if limit_mb <= 0:
        return

    rss = _rss_mb()
    if rss == 0:
        # Can't read RSS --- skip the check (non-Linux platform).
        return

    available = _available_memory_mb()

    if rss >= limit_mb:
        raise MemoryPressureError(
            f"worker RSS {rss} MiB >= limit {limit_mb} MiB; "
            f"rejecting task to avoid OOM"
        )

    # Also check available memory: if the system is already under
    # pressure, reject even if this worker is below the limit.
    # We need at least ~2 GiB headroom for a Demucs run.
    min_headroom = 2_048  # MiB
    if available > 0 and available < min_headroom:
        raise MemoryPressureError(
            f"system memory pressure: only {available} MiB available "
            f"(need >= {min_headroom} MiB for a safe Demucs run); "
            f"rejecting task to avoid OOM"
        )

    logger.debug(
        "memory gate: rss=%d MiB / limit=%d MiB, available=%d MiB --- ok",
        rss, limit_mb, available,
    )