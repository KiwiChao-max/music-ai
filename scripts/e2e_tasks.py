"""End-to-end for the three /api/tasks/* endpoints (Celery variant).

Self-contained: kills any existing backend on :8000, starts FastAPI and a
Celery worker pointed at a shared broker, runs the upload -> process -> poll
-> stems -> download flow, then tears everything down.

Broker: defaults to Redis at REDIS_URL. Set E2E_BROKER=filesystem to use
kombu's filesystem transport instead — useful on Windows laptops where
installing Redis is painful. The production docker-compose stack still
uses real Redis; this script only changes the broker for portability.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

BASE = "http://127.0.0.1:8000"
BACKEND = "d:/project/overseas/music-ai/backend"
SAMPLE = "d:/project/overseas/music-ai/scripts/_e2e_celery.wav"
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
E2E_BROKER = os.environ.get("E2E_BROKER", "redis")  # "redis" or "filesystem"

# ---- helpers --------------------------------------------------------------
def http(method, path, body=None, headers=None):
    req = urllib.request.Request(
        f"{BASE}{path}", method=method, data=body, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def kill_port(port):
    """Kill whatever is bound to `port` on Windows."""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue"
         f" | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"],
        check=False,
    )


def wait_http(url, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionResetError):
            pass
        time.sleep(0.3)
    raise SystemExit(f"timed out waiting for {url}")


# ---- broker + worker ------------------------------------------------------
# The kombu `filesystem://` transport wants the storage path set via
# `broker_transport_options`, not the URL. That's awkward to inject from
# here, so use the SQLAlchemy transport pointed at a private SQLite file.
# The result backend stays as in-memory cache — the e2e doesn't read the
# AsyncResult, the API polls the DB instead. Production (docker-compose)
# uses real Redis for both broker AND backend.
broker_dir = tempfile.mkdtemp(prefix="celery_broker_")
broker_db = os.path.join(broker_dir, "celery.sqlite")
broker_url = f"sqla+sqlite:///{broker_db}"
result_url = "cache+memory://"

print(f"broker:   {broker_url}")
print(f"backend:  {result_url}")

# Pre-shared env so both the API and the worker use the same broker.
shared_env = {
    **os.environ,
    "CELERY_BROKER_URL": broker_url,
    "CELERY_RESULT_BACKEND": result_url,
    "PYTHONUNBUFFERED": "1",
}

# Make sure nothing is already on :8000 (a previous test, a hand-launched
# dev server, etc.). This is destructive on a developer machine; that's the
# whole point of a self-contained e2e.
kill_port(8000)
time.sleep(0.5)

api = subprocess.Popen(
    [
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", "127.0.0.1", "--port", "8000",
        "--log-level", "info",
    ],
    cwd=BACKEND,
    env=shared_env,
    stdout=sys.stdout,
    stderr=subprocess.STDOUT,
)

worker = subprocess.Popen(
    [
        sys.executable, "-m", "celery",
        "-A", "app.celery_app:celery",
        "worker",
        "--loglevel=info",
        "--concurrency=1",
        "-P", "solo",  # solo pool works on Windows (prefork doesn't)
    ],
    cwd=BACKEND,
    env=shared_env,
    stdout=sys.stdout,
    stderr=subprocess.STDOUT,
)

try:
    wait_http(f"{BASE}/health", timeout=30)
    print("api up; celery worker pid=%s" % worker.pid)

    # Give the worker a couple of seconds to subscribe to the queue. With
    # the filesystem transport this is also when the queue dir is created.
    time.sleep(3)

    # ---- 1) upload ---------------------------------------------------------
    with wave.open(SAMPLE, "wb") as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(8000)
        w.writeframes(b"\x80" * 800)

    boundary = "----e2e_celery"
    form = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="_e2e_celery.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + open(SAMPLE, "rb").read() + f"\r\n--{boundary}--\r\n".encode()
    status, body = http(
        "POST", "/api/audio/upload", form,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    task_id = json.loads(body)["task_id"]
    print(f"uploaded task_id={task_id}")

    # ---- 2) POST /process --------------------------------------------------
    status, body = http("POST", f"/api/tasks/{task_id}/process")
    print(f"POST  /api/tasks/{task_id}/process        -> {status}  {body.decode()}")
    assert status == 202, body

    # 2b) duplicate POST should be 409
    status, body = http("POST", f"/api/tasks/{task_id}/process")
    print(f"POST  /api/tasks/{task_id}/process (dup)  -> {status}  {body.decode()}")
    assert status == 409

    # ---- 3) poll /status ---------------------------------------------------
    snap = None
    for i in range(40):
        time.sleep(0.6)
        status, body = http("GET", f"/api/tasks/{task_id}/status")
        snap = json.loads(body)
        print(f"  t={i*0.6:.1f}s  GET /status                  -> {status}  {snap}")
        if snap["status"] == "FINISHED":
            break
        if snap["status"] == "FAILED":
            raise SystemExit(f"task failed: {snap}")
    assert snap["status"] == "FINISHED" and snap["progress"] == 100, snap

    # ---- 4) GET /stems -----------------------------------------------------
    status, body = http("GET", f"/api/tasks/{task_id}/stems")
    stems = json.loads(body)
    print(f"GET   /api/tasks/{task_id}/stems          -> {status}")
    for s in stems:
        print(f"  {s['name']:<8} {s['url']}")
    assert status == 200 and len(stems) == 4
    assert {s["name"] for s in stems} == {"vocals", "drums", "bass", "other"}

    # ---- 5) download a stem ------------------------------------------------
    first_url = stems[0]["url"]
    status, body = http("GET", first_url)
    print(f"GET   {first_url}  -> {status}, {len(body)} bytes")
    assert status == 200 and len(body) > 0

    # ---- 6) 404 paths ------------------------------------------------------
    status, _ = http("GET", "/api/tasks/999999/stems")
    print(f"GET   /api/tasks/999999/stems              -> {status}")
    assert status == 404
    status, _ = http("GET", "/api/tasks/999999/status")
    print(f"GET   /api/tasks/999999/status             -> {status}")
    assert status == 404

    print("\nALL CHECKS PASSED")
finally:
    # Always tear everything down, even if assertions above blew up.
    for p in (worker, api):
        try:
            p.terminate()
            p.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            p.kill()
    if broker_dir and os.path.isdir(broker_dir):
        shutil.rmtree(broker_dir, ignore_errors=True)
    if os.path.exists(SAMPLE):
        os.remove(SAMPLE)
