#!/usr/bin/env python3
"""Download the FFmpeg binaries that ship with the Windows build.

The app needs FFmpeg to merge separate video + audio streams (anything above
360p/720p on YouTube) and to convert to MP3. Bundling a *working* copy is what
makes the installer self-contained.

Do **not** bundle whatever ``ffmpeg.exe`` happens to be on ``PATH``: package
managers such as Chocolatey and WinGet install small *shims* into a ``bin`` or
``Links`` folder that only work on the machine that created them. Copying such
a shim into the app produced an ``ffmpeg.exe`` that could not run, so every
high-resolution download failed while merging.

This script downloads the official gyan.dev release build, verifies it against
the publisher's ``.sha256``, extracts only ``ffmpeg.exe`` and ``ffprobe.exe``
into ``vendor/ffmpeg/`` and then runs ``ffmpeg -version`` as a final check.

    python scripts/fetch_ffmpeg.py            # fetch (skips a verified copy)
    python scripts/fetch_ffmpeg.py --check    # only verify it is present
    python scripts/fetch_ffmpeg.py --force    # re-download

Windows only; on other platforms the script exits 0 without doing anything.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
BUILD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
SUM_URL = BUILD_URL + ".sha256"
BINARIES = ("ffmpeg.exe", "ffprobe.exe")


def target_dir() -> Path:
    return ROOT / "vendor" / "ffmpeg"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runs(exe: Path, name: str) -> bool:
    """True when *exe* really is a working FFmpeg/FFprobe binary."""
    try:
        proc = subprocess.run(
            [str(exe), "-version"],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    first = (proc.stdout or "").splitlines()[0].lower() if proc.stdout else ""
    return proc.returncode == 0 and name in first and "version" in first


def already_present() -> bool:
    return all(runs(target_dir() / name, name.split(".")[0]) for name in BINARIES)


def download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, open(destination, "wb") as fh:
        shutil.copyfileobj(response, fh)


def fetch() -> int:
    target_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yd-ffmpeg-") as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / "ffmpeg.zip"
        expected = tmp_dir / "ffmpeg.zip.sha256"

        print(f"Downloading {BUILD_URL}")
        download(BUILD_URL, archive)
        download(SUM_URL, expected)

        want = expected.read_text(encoding="utf-8").split()[0].strip().lower()
        got = sha256(archive)
        if want != got:
            print(f"Checksum mismatch: expected {want}, got {got}", file=sys.stderr)
            return 1

        with zipfile.ZipFile(archive) as zf:
            names = {Path(n).name: n for n in zf.namelist()}
            for name in BINARIES:
                member = names.get(name)
                if member is None:
                    print(f"{name} is missing from the archive", file=sys.stderr)
                    return 1
                with zf.open(member) as src, open(target_dir() / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    for name in BINARIES:
        exe = target_dir() / name
        if not runs(exe, name.split(".")[0]):
            print(f"{exe} does not run; refusing to bundle it", file=sys.stderr)
            return 1
        print(f"Verified {exe}")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if a verified copy is missing instead of downloading it")
    parser.add_argument("--force", action="store_true",
                        help="download even when a verified copy already exists")
    args = parser.parse_args(argv)

    if os.name != "nt":
        print("Not Windows: no FFmpeg binaries are bundled for this build.")
        return 0

    if not args.force and already_present():
        print(f"Bundled FFmpeg already present and working: {target_dir()}")
        return 0

    if args.check:
        print(f"Bundled FFmpeg missing or broken in {target_dir()}", file=sys.stderr)
        return 1

    return fetch()


if __name__ == "__main__":
    raise SystemExit(main())
