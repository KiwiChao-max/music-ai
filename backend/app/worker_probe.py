"""Worker resource probing and concurrency calculation.

Probes the execution environment (CPU cores, available memory, GPU
presence) and derives safe concurrency values for audio-processing
workers.  Demucs / Basic Pitch / ADTOS are CPU- and memory-heavy;
running too many concurrent tasks causes OOM kills or severe
thrashing.  This module lets the worker compute a reasonable
concurrency ceiling at startup instead of relying on a hard-coded
``--concurrency=2`` that may be too conservative on a 32-core box
or too aggressive on a 2-core VM.

All probe functions are best-effort: if a probe fails (e.g. no
``/proc/meminfo`` inside a container with a restricted filesystem),
we fall back to the configured defaults.
"""
from __future__ import annotations

import logging
import math
import os
import shutil
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-model memory estimates (RSS in MiB).
# These are *idle* (loaded but not inferring) numbers measured on a
# reference x86-64 Linux host with PyTorch 2.x + CUDA 12.x.
# Inference adds ~30-50 % overhead, so the per-task budget below
# includes a 1.5x safety margin.
# ---------------------------------------------------------------------------
# htdemucs_6s loaded on CPU: ~1.2 GiB RSS, ~2.2 GiB during inference.
_DEMUCS_CPU_MB = 2_200  # peak RSS during a 5-minute stereo track
# htdemucs_6s on GPU: ~800 MiB VRAM + 600 MiB host RSS.
_DEMUCS_GPU_MB = 800
# Basic Pitch ONNX on CPU: ~400 MiB RSS, ~600 MiB during inference.
_BASIC_PITCH_CPU_MB = 600
# ADTOS (torch + checkpoint) on CPU: ~500 MiB RSS.
_ADTOS_CPU_MB = 500
# ADTOS on GPU: ~400 MiB VRAM.
_ADTOS_GPU_MB = 400

# ---------------------------------------------------------------------------
# Conservative per-task memory budget (MiB) --- the pipeline runs
# sequentially, so the peak is the maximum of its phases, not the sum.
# We take the worst-case Demucs number and add ~20 % headroom for the
# Python runtime, DB connections, and temporary WAV buffers.
# ---------------------------------------------------------------------------
_PER_TASK_CPU_MB = int(_DEMUCS_CPU_MB * 1.2)  # ~2 640 MiB
_PER_TASK_GPU_MB = int(_DEMUCS_GPU_MB * 1.2)  # ~960 MiB VRAM

# OS / container overhead we reserve outside the per-task budget.
_OS_RESERVE_MB = 512  # keep at least 512 MiB for the kernel + Celery broker


@dataclass
class WorkerResources:
    """Summary of the execution environment that a worker can use."""

    cpu_cores: int = 1
    total_memory_mb: int = 1024
    gpu_count: int = 0
    gpu_memory_mb: list[int] = field(default_factory=list)
    # Derived safe concurrency.
    cpu_concurrency: int = 1
    gpu_concurrency: int = 0
    recommended_concurrency: int = 1
    # Human-readable label for the detected profile.
    profile: str = "unknown"


def _detect_cpu_cores() -> int:
    """Return the number of logical CPU cores available to the process.

    On Linux containers this respects cgroup v1/v2 limits; on macOS /
    Windows it returns ``os.cpu_count()``.
    """
    try:
        # cgroup v2
        if os.path.exists("/sys/fs/cgroup/cpu.max"):
            with open("/sys/fs/cgroup/cpu.max") as fh:
                parts = fh.read().strip().split()
                if parts[0] != "max":
                    quota = int(parts[0])
                    period = int(parts[1]) if len(parts) > 1 else 100_000
                    return max(1, quota // period)
        # cgroup v1
        cfs_quota = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
        cfs_period = "/sys/fs/cgroup/cpu/cpu.cfs_period_us"
        if os.path.exists(cfs_quota) and os.path.exists(cfs_period):
            with open(cfs_quota) as fh:
                quota = int(fh.read().strip())
            with open(cfs_period) as fh:
                period = int(fh.read().strip())
            if quota > 0:
                return max(1, quota // period)
    except (OSError, ValueError):
        pass
    return os.cpu_count() or 1


def _detect_memory_mb() -> int:
    """Return the total available memory in MiB, respecting cgroup limits."""
    try:
        # cgroup v2
        mem_max = "/sys/fs/cgroup/memory.max"
        if os.path.exists(mem_max):
            with open(mem_max) as fh:
                val = fh.read().strip()
            if val != "max":
                return int(val) // (1024 * 1024)
        # cgroup v1
        mem_limit = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
        if os.path.exists(mem_limit):
            with open(mem_limit) as fh:
                val = fh.read().strip()
            limit = int(val)
            if limit < (1 << 60):  # not "unlimited" sentinel
                return limit // (1024 * 1024)
    except (OSError, ValueError):
        pass
    # Fallback: /proc/meminfo
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 1024  # conservative fallback


def _detect_gpu() -> tuple[int, list[int]]:
    """Return (gpu_count, [vram_mb_per_gpu, ...]).

    Tries PyTorch first (most likely for Demucs), then ``nvidia-smi``.
    """
    vram: list[int] = []
    try:
        import torch  # type: ignore[import-untyped]

        count = torch.cuda.device_count()
        for i in range(count):
            props = torch.cuda.get_device_properties(i)
            vram.append(props.total_memory // (1024 * 1024))
        if count > 0:
            return count, vram
    except Exception:
        pass
    try:
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            import subprocess

            result = subprocess.run(
                [nvidia_smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                if line.strip():
                    vram.append(int(line.strip()))
            return len(vram), vram
    except Exception:
        pass
    return 0, []


def _calculate_concurrency(resources: WorkerResources) -> None:
    """Derive safe concurrency from the detected resources."""
    usable_memory = resources.total_memory_mb - _OS_RESERVE_MB

    # CPU-only path: how many concurrent Demucs tasks can we fit?
    resources.cpu_concurrency = max(
        1,
        min(
            resources.cpu_cores,
            usable_memory // _PER_TASK_CPU_MB,
        ),
    )

    # GPU path: one task per GPU (Demucs typically saturates a GPU).
    if resources.gpu_count > 0 and resources.gpu_memory_mb:
        gpu_limited = sum(
            vram // _PER_TASK_GPU_MB for vram in resources.gpu_memory_mb
        )
        resources.gpu_concurrency = max(1, min(resources.gpu_count, gpu_limited))
        # When GPU is available, prefer GPU concurrency (faster) but also
        # respect memory constraints --- don't launch more tasks than RAM
        # allows.
        resources.recommended_concurrency = min(
            resources.gpu_concurrency,
            max(1, usable_memory // _PER_TASK_CPU_MB),
        )
    else:
        resources.gpu_concurrency = 0
        resources.recommended_concurrency = resources.cpu_concurrency

    # Profile label for logging.
    if resources.gpu_count > 0:
        resources.profile = (
            f"gpu-{resources.gpu_count}x"
            f"({','.join(str(m) for m in resources.gpu_memory_mb)}MiB)"
        )
    else:
        resources.profile = f"cpu-{resources.cpu_cores}c-{resources.total_memory_mb}MiB"


def probe_resources() -> WorkerResources:
    """Detect runtime resources and return a concurrency recommendation.

    Call this once at worker startup.  The returned struct can be
    logged and used to parameterise the Celery worker pool.
    """
    cpu = _detect_cpu_cores()
    mem = _detect_memory_mb()
    gpu_count, gpu_mem = _detect_gpu()

    resources = WorkerResources(
        cpu_cores=cpu,
        total_memory_mb=mem,
        gpu_count=gpu_count,
        gpu_memory_mb=gpu_mem,
    )
    _calculate_concurrency(resources)

    logger.info(
        "worker probe: %s -> concurrency=%d (cpu=%d gpu=%d)",
        resources.profile,
        resources.recommended_concurrency,
        resources.cpu_concurrency,
        resources.gpu_concurrency,
    )
    return resources


def resolve_concurrency(
    *,
    explicit: int | None = None,
    resources: WorkerResources | None = None,
) -> int:
    """Return the final concurrency value.

    Priority:
    1. Explicit ``WORKER_CONCURRENCY`` env var (operator override).
    2. Detected resource-based recommendation.
    3. Fallback to 2 (the original default).
    """
    if explicit is not None and explicit > 0:
        return explicit
    if resources is not None:
        return resources.recommended_concurrency
    return max(1, (os.cpu_count() or 2) // 2)  # conservative: half the cores