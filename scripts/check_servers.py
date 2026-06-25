"""Verify both dev servers are responsive."""
import urllib.request
import urllib.error

def check(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status, r.read()[:200].decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"

for name, url in [
    ("backend docs", "http://127.0.0.1:8000/docs"),
    ("backend api",  "http://127.0.0.1:8000/api/audio"),
    ("frontend",     "http://127.0.0.1:5173/"),
]:
    code, body = check(url)
    print(f"{name:12s} {url:42s} -> {code}  {body[:80]}")
