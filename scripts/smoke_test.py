"""Smoke-test the four audio endpoints."""
import json
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://127.0.0.1:8000"
SCRIPT_DIR = Path(__file__).resolve().parent
TMP = SCRIPT_DIR / "_sample.wav"
TMP.write_bytes(b"RIFF" + b"\x00" * 100)  # minimal dummy audio

def req(method: str, path: str, *, data: bytes | None = None, headers: dict | None = None) -> tuple[int, str]:
    r = urllib.request.Request(BASE + path, method=method, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")

# 1) GET list (empty)
code, body = req("GET", "/api/audio")
print(f"GET  /api/audio            -> {code}  {body}")

# 2) POST upload
with TMP.open("rb") as f:
    data = f.read()
boundary = "----test1234"
form = (
    f"--{boundary}\r\n"
    f"Content-Disposition: form-data; name=\"file\"; filename=\"test.wav\"\r\n"
    f"Content-Type: audio/wav\r\n\r\n"
).encode() + data + f"\r\n--{boundary}--\r\n".encode()
code, body = req(
    "POST",
    "/api/audio/upload",
    data=form,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
print(f"POST /api/audio/upload     -> {code}  {body}")
task_id = json.loads(body)["task_id"]

# 3) GET one
code, body = req("GET", f"/api/audio/{task_id}")
print(f"GET  /api/audio/{task_id}        -> {code}  {body}")

# 4) GET list (after insert)
code, body = req("GET", "/api/audio")
print(f"GET  /api/audio            -> {code}  {body}")

# 5) DELETE
code, body = req("DELETE", f"/api/audio/{task_id}")
print(f"DELETE /api/audio/{task_id}      -> {code}  {body}")

# 6) GET one (should 404)
code, body = req("GET", f"/api/audio/{task_id}")
print(f"GET  /api/audio/{task_id} (gone) -> {code}  {body}")

TMP.unlink(missing_ok=True)
