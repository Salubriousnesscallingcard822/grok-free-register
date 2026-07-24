import os, re, sys, urllib.request
from pathlib import Path

PROXY = (os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or "").strip()
if PROXY:
    os.environ["HTTP_PROXY"] = PROXY
    os.environ["HTTPS_PROXY"] = PROXY
proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
opener = urllib.request.build_opener(proxy)
urllib.request.install_opener(opener)

WHEELS = Path("wheels")
WHEELS.mkdir(exist_ok=True)
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 800_000  # bytes per run

html = opener.open("https://pypi.org/simple/playwright/", timeout=30).read().decode("utf-8", "ignore")
matched = []
for href in re.findall(r'href="([^"]+)"', html):
    clean = href.split("#", 1)[0]
    name = clean.rsplit("/", 1)[-1]
    if re.fullmatch(r"playwright-\d+\.\d+\.\d+-py3-none-win_amd64\.whl", name):
        matched.append(clean if clean.startswith("http") else urllib.request.urljoin("https://pypi.org/simple/playwright/", clean))
if not matched:
    raise SystemExit("no wheel")
url = matched[-1]
name = url.rsplit("/", 1)[-1]
dest = WHEELS / name
part = WHEELS / (name + ".part")
start = part.stat().st_size if part.exists() else 0
if dest.exists() and dest.stat().st_size > 0:
    print("already", dest, dest.stat().st_size)
    raise SystemExit(0)

req = urllib.request.Request(url, headers={"Range": f"bytes={start}-"} if start else {})
with opener.open(req, timeout=30) as resp:
    status = getattr(resp, "status", 200)
    if start and status == 200:
        # server ignored range
        start = 0
        mode = "wb"
    else:
        mode = "ab" if start else "wb"
    got = 0
    with open(part, mode) as f:
        while got < LIMIT:
            data = resp.read(min(64_000, LIMIT - got))
            if not data:
                break
            f.write(data)
            got += len(data)
    size = part.stat().st_size
    cl = resp.headers.get("Content-Range") or resp.headers.get("Content-Length")
    print(f"wrote +{got} total={size} header={cl}")
    # if no more data and we didn't hit limit early with empty, finalize when content complete
    # Better: if Content-Range like bytes a-b/total and size >= total
    m = re.search(r"/(\d+)$", resp.headers.get("Content-Range") or "")
    total = int(m.group(1)) if m else None
    if total is not None and size >= total:
        part.replace(dest)
        print("DONE", dest, size)
    elif got == 0 and size > 0 and total is None:
        # finished without total info
        part.replace(dest)
        print("DONE_NO_TOTAL", dest, size)
    else:
        print("CONTINUE", size)
