import os
import re
import sys
import urllib.request
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

# package name -> simple regex for preferred wheels
PACKAGES = [
    ("python-dotenv", r"python_dotenv-.*-py3-none-any\.whl"),
    ("requests", r"requests-.*-py3-none-any\.whl"),
    ("httpx", r"httpx-.*-py3-none-any\.whl"),
    ("idna", r"idna-.*-py3-none-any\.whl"),
    ("certifi", r"certifi-.*-py3-none-any\.whl"),
    ("charset-normalizer", r"charset_normalizer-.*-py3-none-any\.whl"),
    ("urllib3", r"urllib3-.*-py3-none-any\.whl"),
    ("anyio", r"anyio-.*-py3-none-any\.whl"),
    ("httpcore", r"httpcore-.*-py3-none-any\.whl"),
    ("h11", r"h11-.*-py3-none-any\.whl"),
    ("sniffio", r"sniffio-.*-py3-none-any\.whl"),
    ("playwright", r"playwright-.*-py3-none-win_amd64\.whl"),
    ("pyee", r"pyee-.*-py3-none-any\.whl"),
    ("greenlet", r"greenlet-.*-cp312-cp312-win_amd64\.whl"),
    ("cloakbrowser", r"cloakbrowser-.*-py3-none-any\.whl"),
]

def latest_wheel(pkg: str, pattern: str) -> str:
    url = f"https://pypi.org/simple/{pkg}/"
    html = opener.open(url, timeout=60).read().decode("utf-8", "ignore")
    # href may be absolute or relative; also may include #sha256=...
    found = re.findall(r'href="([^"]+)"', html)
    matched = []
    for href in found:
        clean = href.split("#", 1)[0]
        name = clean.rsplit("/", 1)[-1]
        if re.fullmatch(pattern, name):
            if clean.startswith("http"):
                matched.append(clean)
            else:
                # relative to simple page
                matched.append(urllib.request.urljoin(url, clean))
    if not matched:
        raise RuntimeError(f"no wheel for {pkg} pattern {pattern}")
    return matched[-1]

def download(url: str) -> Path:
    name = url.rsplit("/", 1)[-1]
    dest = WHEELS / name
    if dest.exists() and dest.stat().st_size > 0:
        print(f"skip {name}", flush=True)
        return dest
    print(f"get {name}", flush=True)
    data = opener.open(url, timeout=120).read()
    dest.write_bytes(data)
    print(f"saved {name} ({len(data)} bytes)", flush=True)
    return dest

def main() -> int:
    for pkg, pattern in PACKAGES:
        try:
            url = latest_wheel(pkg, pattern)
            download(url)
        except Exception as exc:
            print(f"FAIL {pkg}: {exc}", flush=True)
            return 1
    print("ALL_OK", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
