#!/usr/bin/env python3
import os
import re
import json
import socket
import ipaddress
import hashlib
import requests
import argparse
from urllib.parse import urlparse
from pathlib import Path
from mimetypes import guess_extension

DATA_DIR = "content/maintainers/"
CACHE_FILE = ".image-cache.json"
IMAGES_DIR = "public/images/"
MAX_SIZE_KB = 500

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; forklore-image-sync/1.0)"}

Path(IMAGES_DIR).mkdir(parents=True, exist_ok=True)

parser = argparse.ArgumentParser(description="Sync remote images to local storage")
parser.add_argument("json_file", nargs="?", help="Specific JSON file in content/maintainers/")
parser.add_argument("--debug", action="store_true")
args = parser.parse_args()

cache = json.loads(Path(CACHE_FILE).read_text()) if Path(CACHE_FILE).exists() else {}


def log(msg):
    if args.debug:
        print(msg)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def get_ext(url, content_type=""):
    ext = os.path.splitext(urlparse(url).path)[-1].split("?")[0]
    if ext:
        return ext
    return guess_extension(content_type.split(";")[0]) or ".img" if content_type else ".img"


def resolve_url(url):
    """Convert GitHub blob URLs to raw.githubusercontent.com."""
    gh = re.match(r"https://github\.com/([^/]+/[^/]+)/blob/(.+)", url)
    if gh:
        return f"https://raw.githubusercontent.com/{gh.group(1)}/{gh.group(2)}"
    return url


def is_safe_url(url):
    """Reject URLs that are not plain http(s) or that resolve to non-public IPs (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        addrs = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False
    for *_, sockaddr in addrs:
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def download(url):
    if not is_safe_url(url):
        print(f"[ERROR] {url}: blocked (invalid scheme or non-public address)")
        return None, None
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type", "")
    except Exception as e:
        print(f"[ERROR] {url}: {e}")
        return None, None


def warn_size(filepath):
    size_kb = os.path.getsize(filepath) / 1024
    if size_kb > MAX_SIZE_KB:
        ext = os.path.splitext(filepath)[1].lower()
        print(f"[WARN] {os.path.basename(filepath)} is {size_kb:.0f} KB (limit {MAX_SIZE_KB} KB)")
        if ext in (".jpg", ".jpeg", ".png"):
            print(f'  Fix: magick {filepath} -resize "800x800>" -strip -quality 75 {filepath}')


def process_image(url, filename_base):
    if not url or not url.strip() or url.startswith("/images/"):
        return url or ""

    cached = cache.get(url)
    if cached:
        filepath = os.path.join(IMAGES_DIR, cached["filename"])
        if os.path.exists(filepath) and sha256(Path(filepath).read_bytes()) == cached["hash"]:
            log(f"[SKIP] {cached['filename']}")
            warn_size(filepath)
            return f"/images/{cached['filename']}"

    resolved = resolve_url(url)
    if resolved != url:
        log(f"[RESOLVED] {url} → {resolved}")

    content, content_type = download(resolved)
    if not content:
        return ""

    filename = f"{filename_base}{get_ext(resolved, content_type)}"
    filepath = os.path.join(IMAGES_DIR, filename)
    Path(filepath).write_bytes(content)
    cache[url] = {"hash": sha256(content), "filename": filename}

    print(f"[SAVED] {filename}")
    warn_size(filepath)
    return f"/images/{filename}"


def process_json(file):
    path = os.path.join(DATA_DIR, file)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[SKIP] {file}: {e}")
        return

    base = os.path.splitext(file)[0]
    changed = False

    if isinstance(data.get("photo"), str):
        result = process_image(data["photo"], f"{base}_photo")
        if result != data["photo"]:
            data["photo"] = result
            changed = True

    for i, project in enumerate(data.get("projects", [])):
        url = project.get("logo", "")
        if isinstance(url, str):
            safe_name = project.get("name", f"project_{i}").replace(" ", "_").lower()
            result = process_image(url, f"{base}_{safe_name}")
            if result != url:
                project["logo"] = result
                changed = True

    if changed:
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[DONE] {file}")


files = [args.json_file] if args.json_file else [f for f in os.listdir(DATA_DIR) if f.endswith(".json")]
for f in files:
    if not f.endswith(".json"):
        print("[ERROR] Need .json file")
    else:
        process_json(f)

Path(CACHE_FILE).write_text(json.dumps(cache, indent=2))
print("[OK] Done.")
