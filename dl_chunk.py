import os, re, sys, urllib.request
from pathlib import Path

PROXY = "http://127.0.0.1:7897"
os.environ["HTTP_PROXY"] = PROXY
os.environ["HTTPS_PROXY"] = PROXY
proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
opener = urllib.request.build_opener(proxy)
urllib.request.install_opener(opener)

pkg = sys.argv[1]
pattern = sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 700000
WHEELS = Path(r"E:\download\claude\CodeX\grok-free-register-main\wheels")
WHEELS.mkdir(exist_ok=True)

html = opener.open(f"https://pypi.org/simple/{pkg}/", timeout=30).read().decode("utf-8", "ignore")
matched = []
for href in re.findall(r'href="([^"]+)"', html):
    clean = href.split("#", 1)[0]
    name = clean.rsplit("/", 1)[-1]
    if re.fullmatch(pattern, name) and ".dev" not in name:
        matched.append(clean if clean.startswith("http") else urllib.request.urljoin(f"https://pypi.org/simple/{pkg}/", clean))
if not matched:
    raise SystemExit(f"no wheel for {pkg}")
url = matched[-1]
name = url.rsplit("/", 1)[-1]
dest = WHEELS / name
part = WHEELS / (name + ".part")
if dest.exists() and dest.stat().st_size > 0:
    print("already", dest, dest.stat().st_size)
    raise SystemExit(0)
start = part.stat().st_size if part.exists() else 0
req = urllib.request.Request(url, headers={"Range": f"bytes={start}-"} if start else {})
with opener.open(req, timeout=30) as resp:
    status = getattr(resp, "status", 200)
    if start and status == 200:
        start = 0
        mode = "wb"
    else:
        mode = "ab" if start else "wb"
    got = 0
    with open(part, mode) as f:
        while got < limit:
            data = resp.read(min(65536, limit - got))
            if not data:
                break
            f.write(data)
            got += len(data)
    size = part.stat().st_size
    cr = resp.headers.get("Content-Range") or ""
    m = re.search(r"/(\d+)$", cr)
    total = int(m.group(1)) if m else None
    print(f"{name} +{got} total={size} full={total}")
    if total is not None and size >= total:
        part.replace(dest)
        print("DONE", dest)
    elif got == 0 and size > 0 and not total:
        part.replace(dest)
        print("DONE", dest)
    else:
        print("CONTINUE", size)
