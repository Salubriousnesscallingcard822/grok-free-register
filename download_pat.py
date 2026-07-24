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
pkg, pattern = sys.argv[1], sys.argv[2]
url = f"https://pypi.org/simple/{pkg}/"
html = opener.open(url, timeout=30).read().decode("utf-8", "ignore")
found = re.findall(r'href="([^"]+)"', html)
matched = []
for href in found:
    clean = href.split("#", 1)[0]
    name = clean.rsplit("/", 1)[-1]
    if re.fullmatch(pattern, name) and ".dev" not in name and "rc" not in name and "a" not in name.split("-")[1] and "b" not in name.split("-")[1]:
        matched.append(clean if clean.startswith("http") else urllib.request.urljoin(url, clean))
if not matched:
    # fallback without pre-release filter
    for href in found:
        clean = href.split("#", 1)[0]
        name = clean.rsplit("/", 1)[-1]
        if re.fullmatch(pattern, name) and ".dev" not in name:
            matched.append(clean if clean.startswith("http") else urllib.request.urljoin(url, clean))
if not matched:
    raise SystemExit("no wheel")
wheel_url = matched[-1]
name = wheel_url.rsplit("/", 1)[-1]
dest = WHEELS / name
if dest.exists() and dest.stat().st_size > 0:
    print("skip", name)
else:
    print("get", name)
    dest.write_bytes(opener.open(wheel_url, timeout=60).read())
    print("saved", name, dest.stat().st_size)
print("OK", dest)
