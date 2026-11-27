"""Self-update path for the yt-dlp engine.

YouTube changes its player constantly, so a copy of yt-dlp frozen into an
installer goes stale within weeks and downloads start failing. This module
adds a small, reversible update path:

* :func:`activate` makes a verified per-user copy take priority over the
  bundled one. It must run before anything imports ``yt_dlp``.
* :func:`fetch_release` asks PyPI for the newest published version.
* :func:`install` downloads that wheel into a staging folder, checks its hash
  and layout, and runs it in a fresh interpreter as a health check. Only then
  does it swap the new copy in and keep the old one aside.
* :func:`rollback` restores the previous copy, or removes the per-user copy so
  the bundled one is used again.

The bundled copy is never modified or deleted, so a fresh install always works
offline. Everything here is Qt-free and unit tested; network access uses only
``urllib``.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Optional
from urllib.request import Request, urlopen

log = logging.getLogger("youtube_downloader")

PYPI_JSON_URL = "https://pypi.org/pypi/yt-dlp/json"
USER_AGENT = "YouTubeDownloader-ytdlp-updater"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 4 * 1024 * 1024
DOWNLOADS_TIMEOUT = 60
HEALTHCHECK_TIMEOUT = 180
_MIN_EXTRACTORS = 100

_VERSION_RE = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #

def data_dir() -> Path:
    """Per-user folder for updates and state (never inside the install dir)."""
    override = os.environ.get("YOUTUBE_DOWNLOADER_DATA_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":  # pragma: no cover - non-Windows
        base = str(Path.home() / "Library" / "Application Support")
    else:  # pragma: no cover - Linux
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "YouTubeDownloader"


@dataclass(frozen=True)
class Store:
    """The on-disk layout of the per-user yt-dlp copies."""

    root: Path

    @property
    def current(self) -> Path:
        return self.root / "current"

    @property
    def previous(self) -> Path:
        return self.root / "previous"

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    def staging(self, version: str) -> Path:
        safe = re.sub(r"[^0-9A-Za-z._-]", "_", version)[:40] or "new"
        return self.root / f"staging-{safe}"


def default_store() -> Store:
    return Store(data_dir() / "yt-dlp")


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #

def parse_version(text: Optional[str]) -> tuple:
    """``"2025.11.12.232957"`` -> ``(2025, 11, 12, 232957)`` for comparison."""
    if not text:
        return ()
    parts = re.findall(r"\d+", str(text))
    return tuple(int(p) for p in parts)


def is_newer(latest: Optional[str], current: Optional[str]) -> bool:
    """True only when *latest* is strictly newer than *current*."""
    if not latest:
        return False
    if not current:
        return True
    return parse_version(latest) > parse_version(current)


def read_version(root: Path) -> Optional[str]:
    """Version recorded inside a copied/downloaded package, without importing it."""
    try:
        text = (Path(root) / "yt_dlp" / "version.py").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def active_version() -> Optional[str]:
    """Version of the yt-dlp that is actually loaded in this process."""
    module = sys.modules.get("yt_dlp.version")
    if module is not None:
        value = getattr(module, "__version__", None)
        if isinstance(value, str):
            return value
    try:  # not imported yet: this triggers the same import the app would do
        from yt_dlp.version import __version__ as version

        return version
    except Exception:  # noqa: BLE001 - yt-dlp missing entirely
        return None


def _looks_like_package(root: Path) -> bool:
    root = Path(root)
    return (root / "yt_dlp" / "__init__.py").is_file() and (root / "yt_dlp" / "version.py").is_file()


# --------------------------------------------------------------------------- #
# Import priority
# --------------------------------------------------------------------------- #

class UserCopyFinder(importlib.abc.MetaPathFinder):
    """Loads ``yt_dlp`` from a per-user folder, ahead of a frozen bundle.

    PyInstaller registers its own meta path finder, which would otherwise win
    over ``sys.path``; being earlier in ``sys.meta_path`` is what makes the
    per-user copy authoritative inside a built .exe.
    """

    ytdlp_updater_finder = True

    def __init__(self, root: Path):
        self.root = Path(root)

    def find_spec(self, fullname: str, path=None, target=None):  # noqa: ANN001
        if fullname != "yt_dlp" and not fullname.startswith("yt_dlp."):
            return None
        base = self.root.joinpath(*fullname.split("."))
        try:
            if base.is_dir():
                init = base / "__init__.py"
                if init.is_file():
                    return importlib.util.spec_from_file_location(
                        fullname, init, submodule_search_locations=[str(base)]
                    )
                return importlib.util.spec_from_file_location(
                    fullname, None, submodule_search_locations=[str(base)]
                )
            module_file = base.parent / f"{base.name}.py"
            if module_file.is_file():
                return importlib.util.spec_from_file_location(fullname, module_file)
        except OSError:  # pragma: no cover - permissions / races
            return None
        return None


def _install_finder(root: Path) -> None:
    sys.meta_path[:] = [f for f in sys.meta_path if not getattr(f, "ytdlp_updater_finder", False)]
    sys.meta_path.insert(0, UserCopyFinder(root))
    entry = str(Path(root))
    while entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)
    importlib.invalidate_caches()


def activate(store: Optional[Store] = None) -> Optional[str]:
    """Prefer the per-user copy, if there is a usable one.

    Returns the version that will be imported, or ``None`` when the bundled
    copy stays in charge. Must be called before the first ``import yt_dlp``.
    """
    store = store or default_store()
    if not _looks_like_package(store.current):
        return None
    version = read_version(store.current)
    if not version:
        return None
    _install_finder(store.current)
    log.info("Using updated yt-dlp %s from %s", version, store.current)
    return version


def active_status(store: Optional[Store] = None) -> Dict[str, Any]:
    """What the running app is using, for the status line."""
    store = store or default_store()
    user_version = read_version(store.current) if _looks_like_package(store.current) else None
    return {
        "version": active_version() or user_version,
        "source": "updated" if user_version else "bundled",
        "path": str(store.current) if user_version else None,
        "installed_version": user_version,
    }


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

def read_state(store: Optional[Store] = None) -> Dict[str, Any]:
    store = store or default_store()
    try:
        with open(store.state_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(store: Optional[Store] = None, **updates: Any) -> Dict[str, Any]:
    store = store or default_store()
    state = read_state(store)
    state.update(updates)
    try:
        store.root.mkdir(parents=True, exist_ok=True)
        tmp = store.state_file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, store.state_file)
    except OSError as exc:  # pragma: no cover - disk problems
        log.warning("Could not save yt-dlp update state: %s", exc)
    return state


def check_due(store: Optional[Store] = None, interval: int = CHECK_INTERVAL_SECONDS) -> bool:
    """True when the last successful check is older than *interval*."""
    last = read_state(store).get("last_check") or 0
    try:
        last = float(last)
    except (TypeError, ValueError):
        last = 0.0
    return (time.time() - last) >= interval


# --------------------------------------------------------------------------- #
# PyPI
# --------------------------------------------------------------------------- #

def _pick_wheel(files: list) -> Optional[Dict[str, Any]]:
    wheels = []
    for item in files or []:
        name = str(item.get("filename") or "")
        if not name.endswith(".whl"):
            continue
        if not name.startswith("yt_dlp-"):
            continue
        wheels.append(item)
    if not wheels:
        return None
    # Prefer the universal wheel; yt-dlp normally publishes only that one.
    wheels.sort(key=lambda w: ("none-any" not in str(w.get("filename")), str(w.get("filename"))))
    chosen = wheels[0]
    digests = chosen.get("digests") or {}
    return {
        "url": chosen.get("url"),
        "sha256": digests.get("sha256"),
        "size": chosen.get("size"),
        "filename": chosen.get("filename"),
    }


def fetch_release(timeout: int = 20) -> Dict[str, Any]:
    """Newest yt-dlp release on PyPI, with its universal wheel's URL and hash."""
    request = Request(PYPI_JSON_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        raw = response.read(MAX_METADATA_BYTES)
    data = json.loads(raw)
    info = data.get("info") or {}
    version = info.get("version")
    wheel = _pick_wheel(data.get("urls") or [])
    if not version or not wheel or not wheel.get("url"):
        raise ValueError("PyPI did not publish a usable yt-dlp wheel")
    return {"version": str(version), **wheel}


# --------------------------------------------------------------------------- #
# Install / rollback
# --------------------------------------------------------------------------- #

def _download(url: str, destination: Path, timeout: int = DOWNLOADS_TIMEOUT) -> str:
    """Stream *url* to *destination*; return the sha256 hex digest."""
    digest = hashlib.sha256()
    written = 0
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response, open(destination, "wb") as fh:  # noqa: S310
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_WHEEL_BYTES:
                raise ValueError("The downloaded yt-dlp package is unexpectedly large")
            digest.update(chunk)
            fh.write(chunk)
    if not written:
        raise ValueError("The downloaded yt-dlp package was empty")
    return digest.hexdigest()


def _extract_wheel(wheel: Path, target: Path) -> None:
    """Extract *wheel* into *target*, refusing anything outside it."""
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            name = member.filename
            if name.endswith("/"):
                continue
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"Unsafe path in the wheel: {name}")
            destination = target.joinpath(*pure.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, open(destination, "wb") as dst:
                shutil.copyfileobj(src, dst)


def healthcheck_payload(root: Path) -> Dict[str, Any]:
    """Import the copy at *root* and prove it works. Runs in a child process.

    A rolling back only helps if the check is real, so this imports yt-dlp from
    the candidate folder, builds a ``YoutubeDL`` instance and loads the
    extractor list. It also confirms the module really came from *root*, which
    catches a candidate that would have been shadowed by the bundled copy.
    """
    root = Path(root)
    try:
        for name in [n for n in list(sys.modules) if n == "yt_dlp" or n.startswith("yt_dlp.")]:
            del sys.modules[name]
        _install_finder(root)
        import yt_dlp
        from yt_dlp import YoutubeDL
        from yt_dlp.extractor import gen_extractor_classes
        from yt_dlp.version import __version__

        loaded_from = Path(getattr(yt_dlp, "__file__", "") or "").resolve()
        try:
            loaded_from.relative_to(root.resolve())
        except ValueError:
            return {
                "ok": False,
                "error": f"imported yt_dlp from {loaded_from}, not from the candidate copy",
            }
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}):
            pass
        count = len(list(gen_extractor_classes()))
        if count < _MIN_EXTRACTORS:
            return {"ok": False, "error": f"only {count} extractors loaded", "version": __version__}
        return {"ok": True, "version": __version__, "extractors": count, "path": str(root)}
    except Exception as exc:  # noqa: BLE001 - any failure means "do not use this"
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def healthcheck_command(root: Path, out: Path) -> list:
    """Command that runs :func:`healthcheck_payload` in a fresh interpreter."""
    if getattr(sys, "frozen", False):  # PyInstaller: re-enter the app entry point
        return [
            sys.executable,
            "--ytdlp-healthcheck",
            str(root),
            "--ytdlp-healthcheck-out",
            str(out),
        ]
    return [
        sys.executable,
        "-m",
        "app.ytdlp_updater",
        "--healthcheck",
        str(root),
        "--out",
        str(out),
    ]


def run_healthcheck(root: Path, timeout: int = HEALTHCHECK_TIMEOUT) -> Dict[str, Any]:
    """Run the candidate in a child process so it cannot damage this one."""
    with tempfile.TemporaryDirectory(prefix="ytdlp-health-") as tmp:
        out = Path(tmp) / "result.json"
        command = healthcheck_command(Path(root), out)
        try:
            proc = subprocess.run(
                command,
                cwd=str(Path(__file__).resolve().parent.parent),
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform == "win32"
                else 0,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"the new yt-dlp did not answer within {timeout}s"}
        except OSError as exc:
            return {"ok": False, "error": f"could not run the health check: {exc}"}
        try:
            payload = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else f"exit code {proc.returncode}"
            return {"ok": False, "error": f"the health check produced no result ({tail})"}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "the health check returned an unexpected result"}
        return payload


def install(
    store: Optional[Store] = None,
    release: Optional[Dict[str, Any]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    healthcheck_runner: Optional[Callable[[Path], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Stage, verify and swap in a downloaded yt-dlp wheel.

    The live copy is only replaced after the health check passes; on any error
    the candidate is deleted and the existing copies are left untouched.
    """
    store = store or default_store()
    release = release or {}
    emit = log_cb or (lambda _message: None)
    version = str(release.get("version") or "")
    url = release.get("url")
    if not version or not url:
        return {"ok": False, "action": "error", "error": "No yt-dlp download was available."}

    staging = store.staging(version)
    site = staging / "site"
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        emit(f"Downloading yt-dlp {version}…")
        wheel = staging / "yt_dlp.whl"
        digest = _download(str(url), wheel)
        expected = release.get("sha256")
        if expected and digest.lower() != str(expected).lower():
            raise ValueError("the downloaded package did not match its published checksum")

        emit("Checking the package layout…")
        _extract_wheel(wheel, site)
        if not _looks_like_package(site):
            raise ValueError("the package does not contain yt_dlp")
        found = read_version(site)
        if found and found != version:
            raise ValueError(f"the package contains yt-dlp {found}, not {version}")

        emit("Health-checking the new yt-dlp…")
        check = (healthcheck_runner or run_healthcheck)(site)
        if not check.get("ok"):
            raise ValueError(check.get("error") or "the health check failed")

        previous_version = read_version(store.current) if _looks_like_package(store.current) else None
        if store.current.exists():
            if store.previous.exists():
                shutil.rmtree(store.previous, ignore_errors=True)
            os.replace(store.current, store.previous)
        os.replace(site, store.current)

        write_state(
            store,
            installed_version=version,
            installed_at=int(time.time()),
            source_url=str(url),
            previous_version=previous_version,
            last_check=int(time.time()),
            blocked_version=None,
        )
        emit(f"yt-dlp {version} installed for this user.")
        return {
            "ok": True,
            "action": "installed",
            "version": version,
            "previous": previous_version,
            "message": f"yt-dlp {version} installed.",
        }
    except Exception as exc:  # noqa: BLE001 - never break the running app
        emit(f"yt-dlp update failed: {exc}")
        return {"ok": False, "action": "error", "error": str(exc)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def rollback(store: Optional[Store] = None) -> Dict[str, Any]:
    """Go back to the previous copy, or to the bundled one.

    Without a previous copy the per-user folder is removed and its version is
    blocked, so an automatic check cannot immediately reinstall something that
    already failed.
    """
    store = store or default_store()
    try:
        if _looks_like_package(store.previous):
            restored = read_version(store.previous)
            if store.current.exists():
                shutil.rmtree(store.current, ignore_errors=True)
            os.replace(store.previous, store.current)
            write_state(
                store,
                installed_version=restored,
                installed_at=int(time.time()),
                previous_version=None,
                blocked_version=None,
            )
            return {
                "ok": True,
                "action": "restored",
                "version": restored,
                "message": f"Restored yt-dlp {restored}.",
            }
        if _looks_like_package(store.current):
            blocked = read_version(store.current)
            shutil.rmtree(store.current, ignore_errors=True)
            write_state(store, installed_version=None, previous_version=None, blocked_version=blocked)
            return {
                "ok": True,
                "action": "bundled",
                "version": blocked,
                "message": (
                    f"Removed the downloaded yt-dlp {blocked}; the bundled copy will be used."
                ),
            }
        return {"ok": False, "action": "none", "error": "There is no downloaded yt-dlp to remove."}
    except OSError as exc:
        return {"ok": False, "action": "error", "error": f"Could not roll back: {exc}"}


# --------------------------------------------------------------------------- #
# One-shot update used by the app
# --------------------------------------------------------------------------- #

def update_once(
    store: Optional[Store] = None,
    force: bool = False,
    log_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Check PyPI, then install a newer yt-dlp. Returns a UI-friendly dict."""
    store = store or default_store()
    emit = log_cb or (lambda _message: None)
    current = active_version()

    if not force and not check_due(store):
        return {"ok": True, "action": "skipped", "current": current,
                "message": "yt-dlp was checked recently."}

    try:
        release = fetch_release()
    except Exception as exc:  # noqa: BLE001 - offline is normal
        emit(f"yt-dlp update check failed: {exc}")
        return {
            "ok": False,
            "action": "error",
            "current": current,
            "error": f"Could not reach PyPI: {exc}",
            "message": "Could not check PyPI for a yt-dlp update.",
        }

    latest = release["version"]
    write_state(store, last_check=int(time.time()))
    emit(f"yt-dlp in use: {current or 'unknown'}; newest on PyPI: {latest}.")

    if not is_newer(latest, current):
        return {
            "ok": True,
            "action": "up-to-date",
            "current": current,
            "latest": latest,
            "available": False,
            "message": f"yt-dlp {current or latest} is the newest version.",
        }

    if read_state(store).get("blocked_version") == latest:
        emit(f"yt-dlp {latest} was rolled back before; not reinstalling it automatically.")
        return {
            "ok": True,
            "action": "blocked",
            "current": current,
            "latest": latest,
            "available": True,
            "message": (
                f"yt-dlp {latest} is available, but {latest} was rolled back earlier. "
                "Install it from Help ▸ Check for yt-dlp update… if you want to try again."
            ),
        }

    result = install(store, release, log_cb=emit)
    result.update({"current": current, "latest": latest, "available": True})
    return result


# --------------------------------------------------------------------------- #
# Child-process entry point
# --------------------------------------------------------------------------- #

def healthcheck_cli(argv: list) -> int:
    """``--healthcheck DIR [--out FILE]`` / ``--ytdlp-healthcheck DIR ...``."""
    root: Optional[str] = None
    out: Optional[str] = None
    for index, arg in enumerate(argv):
        if arg in ("--healthcheck", "--ytdlp-healthcheck") and index + 1 < len(argv):
            root = argv[index + 1]
        elif arg in ("--out", "--ytdlp-healthcheck-out") and index + 1 < len(argv):
            out = argv[index + 1]
    if not root:
        return 2
    payload = healthcheck_payload(Path(root))
    if out:
        try:
            Path(out).write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass
    else:  # pragma: no cover - command line use only
        print(json.dumps(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover - exercised through a subprocess
    raise SystemExit(healthcheck_cli(sys.argv[1:]))
