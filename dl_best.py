import os, re, sys, urllib.request
from pathlib import Path

PROXY = "http://127.0.0.1:7897"
os.environ["HTTP_PROXY"] = PROXY
os.environ["HTTPS_PROXY"] = PROXY
proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
opener = urllib.request.build_opener(proxy)
urllib.request.install_opener(opener)

MIRRORS = [
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://pypi.org/simple",
]
WHEELS = Path(r"E:\download\claude\CodeX\grok-free-register-main\wheels")
WHEELS.mkdir(exist_ok=True)

pkg = sys.argv[1]
pattern = sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 3_000_000

def ver_key(name: str):
    # playwright-1.61.0-py3-none-win_amd64.whl
    ver = name.split("-", 1)[1].rsplit("-py3", 1)[0]
    parts = []
    for p in re.split(r"[.]", ver):
        if p.isdigit():
            parts.append(int(p))
        else:
            parts.append(0)
    return tuple(parts)

def find_latest():
    last_err = None
    for base in MIRRORS:
        try:
            html = opener.open(f"{base}/{pkg}/", timeout=15).read().decode("utf-8", "ignore")
        except Exception as e:
            last_err = e
            continue
        matched = []
        for href in re.findall(r'href="([^"]+)"', html):
            clean = href.split("#", 1)[0]
            name = clean.rsplit("/", 1)[-1]
            if not re.fullmatch(pattern, name) or ".dev" in name:
                continue
            url = clean if clean.startswith("http") else urllib.request.urljoin(f"{base}/{pkg}/", clean)
            matched.append((ver_key(name), url, name))
        if matched:
            matched.sort()
            return matched[-1]
    raise SystemExit(f"no wheel for {pkg}: {last_err}")

ver, url, name = find_latest()
dest = WHEELS / name
part = WHEELS / (name + ".part")
print(f"chosen {name}", flush=True)
print(f"url {url}", flush=True)
if dest.exists() and dest.stat().st_size > 0:
    print("already", dest, dest.stat().st_size)
    raise SystemExit(0)
start = part.stat().st_size if part.exists() else 0
req = urllib.request.Request(url, headers={"Range": f"bytes={start}-"} if start else {})
with opener.open(req, timeout=25) as resp:
    status = getattr(resp, "status", 200)
    if start and status == 200:
        start = 0
        mode = "wb"
    else:
        mode = "ab" if start else "wb"
    got = 0
    with open(part, mode) as f:
        while got < limit:
            data = resp.read(min(131072, limit - got))
            if not data:
                break
            f.write(data)
            got += len(data)
    size = part.stat().st_size
    cr = resp.headers.get("Content-Range") or ""
    m = re.search(r"/(\d+)$", cr)
    total = int(m.group(1)) if m else None
    cl = resp.headers.get("Content-Length")
    print(f"{name} +{got} total={size} full={total} cl={cl} status={status}", flush=True)
    if total is not None and size >= total:
        part.replace(dest)
        print("DONE", dest)
    elif cl and mode == "wb" and size >= int(cl):
        part.replace(dest)
        print("DONE", dest)
    else:
        print("CONTINUE", size)
