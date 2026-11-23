#!/usr/bin/env python3
"""Download the JavaScript runtime that ships with the desktop app.

yt-dlp can no longer solve YouTube's JavaScript challenges on its own: it needs
an external runtime (Deno/Node/QuickJS) plus the ``yt-dlp-ejs`` solver scripts.
Asking every user to install Deno would defeat the point of a one-click
downloader, so the build bundles QuickJS-NG's ``qjs`` binary (~2 MB, MIT
licensed) and the app points yt-dlp at it.

The binary is not committed to the repository. Run this script before
``pyinstaller`` (the release workflow does it automatically):

    python scripts/fetch_js_runtime.py           # fetch for this machine
    python scripts/fetch_js_runtime.py --check    # only verify it is present
    python scripts/fetch_js_runtime.py --force    # re-download
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import stat
import sys
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.18.0"
URL = "https://github.com/quickjs-ng/quickjs/releases/download/v{version}/{asset}"

# sha256 of every asset we support, so a truncated or tampered download is
# rejected instead of being shipped to users.
ASSETS: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("Windows", "x86_64"): (
        "qjs-windows-x86_64.exe",
        "ff03398d74ea6e6bd3a8e58058f7003225abcac229d7d57775b7aaae812a49c7",
    ),
    ("Linux", "x86_64"): (
        "qjs-linux-x86_64",
        "85ded23b9fabe8c1644fc40a8d2bbdd71157affcbd807359c618cc717ee24d7d",
    ),
    ("Linux", "aarch64"): (
        "qjs-linux-aarch64",
        "2c005c7171424a598d31c53c8dc4a0ae2e4958eb6fbb37908c885a307c78187c",
    ),
    ("Darwin", "x86_64"): (
        "qjs-darwin-x86_64",
        "fa2c5dc78095daff566b32fab995ced12927875253b84216b91b834af2ef2b74",
    ),
    ("Darwin", "aarch64"): (
        "qjs-darwin-arm64",
        "91fe89916d3b0a6c01c4dc8382a99c3c1fd91ecfc214aa81ca051902bcce590d",
    ),
}


def arch_key(machine: str) -> Optional[str]:
    value = (machine or "").lower()
    if value in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if value in ("arm64", "aarch64"):
        return "aarch64"
    return None


def resolve_asset() -> Tuple[str, str]:
    key = (platform.system(), arch_key(platform.machine()) or "")
    try:
        return ASSETS[key]
    except KeyError:
        raise SystemExit(
            f"No bundled JavaScript runtime for {key[0]}/{key[1] or 'unknown'}. "
            "Install Deno or Node instead; the app will detect it."
        )


def target_path() -> Path:
    name = "qjs.exe" if os.name == "nt" else "qjs"
    return ROOT / "vendor" / "js" / name


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(asset: str, expected: str, destination: Path) -> None:
    url = URL.format(version=VERSION, asset=asset)
    print(f"Downloading {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as response, open(tmp, "wb") as fh:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
        actual = sha256(tmp)
        if actual != expected:
            raise SystemExit(
                f"Checksum mismatch for {asset}: expected {expected}, got {actual}"
            )
        os.replace(tmp, destination)
        if os.name != "nt":
            destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP
                              | stat.S_IXOTH)
    finally:
        tmp.unlink(missing_ok=True)
    print(f"Bundled JavaScript runtime ready: {destination}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if the runtime is missing instead of downloading it")
    parser.add_argument("--force", action="store_true",
                        help="download even when a verified copy already exists")
    args = parser.parse_args(argv)

    asset, expected = resolve_asset()
    destination = target_path()

    if not args.force and destination.is_file() and sha256(destination) == expected:
        print(f"Bundled JavaScript runtime already present: {destination}")
        return 0

    if args.check:
        print(f"Bundled JavaScript runtime missing: {destination}", file=sys.stderr)
        return 1

    download(asset, expected, destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
