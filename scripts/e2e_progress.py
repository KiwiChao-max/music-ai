"""End-to-end: upload a task then run the worker and watch progress."""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND = PROJECT_ROOT / "backend"
SAMPLE = PROJECT_ROOT / "scripts" / "_e2e.wav"
# Minimal valid WAV: PCM 8kHz mono 8-bit, 0.1s of silence.
import wave
with wave.open(str(SAMPLE), "wb") as w:
    w.setnchannels(1); w.setsampwidth(1); w.setframerate(8000)
    w.writeframes(b"\x80" * 800)

# 1) upload
boundary = "----e2e"
form = (
    f"--{boundary}\r\n"
    f"Content-Disposition: form-data; name=\"file\"; filename=\"_e2e.wav\"\r\n"
    f"Content-Type: audio/wav\r\n\r\n"
).encode() + SAMPLE.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
req = urllib.request.Request(
    f"{BASE}/api/audio/upload",
    method="POST",
    data=form,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
with urllib.request.urlopen(req) as r:
    task_id = json.loads(r.read())["task_id"]
print(f"uploaded task_id={task_id}")

# 2) spawn the worker in a subprocess so it has the right sys.path
subprocess.Popen(
    [sys.executable, "-m", "app.workers.audio_worker", str(task_id)],
    cwd=BACKEND,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

# 3) poll progress
for i in range(20):
    time.sleep(0.6)
    with urllib.request.urlopen(f"{BASE}/api/audio/{task_id}") as r:
        t = json.loads(r.read())
    print(f"  t={i*0.6:.1f}s  status={t['status']:<10}  progress={t['progress']:>3}%  step={t['current_step']!r}")
    if t["status"] in ("FINISHED", "FAILED"):
        break

SAMPLE.unlink(missing_ok=True)
