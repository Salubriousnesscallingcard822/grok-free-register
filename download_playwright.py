import os, re, sys, urllib.request
from pathlib import Path
PROXY = (os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or "").strip()
if PROXY:
    os.environ["HTTP_PROXY"] = PROXY
    os.environ["HTTPS_PROXY"] = PROXY
proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
opener = urllib.request.build_opener(proxy)
urllib.request.install_opener(opener)
ROOT = Path(__file__).resolve().parent
WHEELS = ROOT / "wheels"
WHEELS.mkdir(exist_ok=True)
pkg = "playwright"
pattern = r"playwright-\d+\.\d+\.\d+-py3-none-win_amd64\.whl"
url = f"https://pypi.org/simple/{pkg}/"
html = opener.open(url, timeout=30).read().decode("utf-8", "ignore")
found = re.findall(r'href="([^"]+)"', html)
matched = []
for href in found:
    clean = href.split("#", 1)[0]
    name = clean.rsplit("/", 1)[-1]
    if re.fullmatch(pattern, name):
        matched.append(clean if clean.startswith("http") else urllib.request.urljoin(url, clean))
if not matched:
    raise SystemExit("no playwright wheel")
wheel_url = matched[-1]
name = wheel_url.rsplit("/", 1)[-1]
dest = WHEELS / name
part = Path(str(dest) + ".part")
start = part.stat().st_size if part.exists() else 0
req = urllib.request.Request(wheel_url)
if start:
    req.add_header("Range", f"bytes={start}-")
print(f"download {name} from {start}", flush=True)
try:
    with opener.open(req, timeout=60) as resp:
        total = resp.headers.get("Content-Length")
        mode = "ab" if start and resp.status == 206 else "wb"
        if mode == "wb":
            start = 0
        written = start
        with open(part if mode == "ab" or start == 0 else dest, mode) as f:
            # always write to part first
            pass
except Exception as e:
    print("ERR", e)
    raise
# reopen cleanly
req = urllib.request.Request(wheel_url)
if start:
    req.add_header("Range", f"bytes={start}-")
with opener.open(req, timeout=60) as resp:
    if start and getattr(resp, "status", 200) not in (200, 206):
        start = 0
    if not start:
        part.write_bytes(b"")
        start = 0
        req = urllib.request.Request(wheel_url)
        resp.close()
        resp = opener.open(req, timeout=60)
    chunk = 256 * 1024
    with open(part, "ab" if start else "wb") as f:
        while True:
            data = resp.read(chunk)
            if not data:
                break
            f.write(data)
            start += len(data)
            if start % (1024 * 1024) < chunk:
                print(f" progressive {start}", flush=True)
            # keep each invocation short enough
            if start and start % (2 * 1024 * 1024) < chunk:
                print(f"checkpoint {start}", flush=True)
                print("CONTINUE", start, flush=True)
                raise SystemExit(0)
part.replace(dest)
print("DONE", dest, dest.stat().st_size)
