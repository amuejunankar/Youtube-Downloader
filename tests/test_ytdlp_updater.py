"""Tests for :mod:`app.ytdlp_updater`.

Everything runs against a temporary store; no test touches the real per-user
folder or the network. The packaged-copy path is exercised through the same
child-process entry point the .exe uses.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app import ytdlp_updater as up

ROOT = Path(__file__).resolve().parent.parent
# Captured before the autouse fixture replaces the module attribute below, so
# the child-process tests exercise the real implementation.
REAL_RUN_HEALTHCHECK = up.run_healthcheck


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def make_package(root: Path, version: str = "2026.1.2", extractors: int = 150) -> Path:
    """A minimal but importable stand-in for the yt-dlp package."""
    root.mkdir(parents=True, exist_ok=True)
    pkg = root / "yt_dlp"
    pkg.mkdir(exist_ok=True)
    (pkg / "version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (pkg / "__init__.py").write_text(
        "from .version import __version__\n"
        "\n"
        "class YoutubeDL:\n"
        "    def __init__(self, params=None):\n"
        "        self.params = params or {}\n"
        "    def __enter__(self):\n"
        "        return self\n"
        "    def __exit__(self, *exc):\n"
        "        return False\n",
        encoding="utf-8",
    )
    extractor = pkg / "extractor"
    extractor.mkdir(exist_ok=True)
    (extractor / "__init__.py").write_text(
        f"def gen_extractor_classes():\n    return [object] * {extractors}\n",
        encoding="utf-8",
    )
    return root


def make_wheel(path: Path, version: str = "2026.1.2", extractors: int = 150) -> Path:
    payload = {
        "yt_dlp/version.py": f'__version__ = "{version}"\n',
        "yt_dlp/__init__.py": (
            "from .version import __version__\n"
            "\n"
            "class YoutubeDL:\n"
            "    def __init__(self, params=None):\n"
            "        self.params = params or {}\n"
            "    def __enter__(self):\n"
            "        return self\n"
            "    def __exit__(self, *exc):\n"
            "        return False\n"
        ),
        "yt_dlp/extractor/__init__.py": (
            f"def gen_extractor_classes():\n    return [object] * {extractors}\n"
        ),
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
    return path


def release_for(wheel: Path, version: str = "2026.1.2", sha256: str | None = None) -> dict:
    digest = sha256 or hashlib.sha256(wheel.read_bytes()).hexdigest()
    return {"version": version, "url": wheel.as_uri(), "sha256": digest,
            "filename": wheel.name, "size": wheel.stat().st_size}


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def store(tmp_path) -> up.Store:
    return up.Store(tmp_path / "yt-dlp")


@pytest.fixture(autouse=True)
def no_healthcheck(monkeypatch):
    """Default: the candidate always passes, unless a test overrides it."""
    monkeypatch.setattr(up, "run_healthcheck", lambda root: {"ok": True, "version": "test"})


# --------------------------------------------------------------------------- #
# Versions and state
# --------------------------------------------------------------------------- #

def test_version_comparison_handles_ytdlp_scheme():
    assert up.parse_version("2025.11.12") == (2025, 11, 12)
    assert up.is_newer("2025.12.1", "2025.11.30")
    assert not up.is_newer("2025.11.30", "2025.11.30")
    assert not up.is_newer("2025.11.30", "2025.12.1")
    assert up.is_newer("2026.1.2", None)
    # Nightly-style suffixes still compare in order.
    assert up.is_newer("2025.11.12.232957", "2025.11.12")


def test_check_due_respects_the_interval(store):
    assert up.check_due(store)
    up.write_state(store, last_check=1)
    assert up.check_due(store)
    import time

    up.write_state(store, last_check=int(time.time()))
    assert not up.check_due(store)
    assert up.check_due(store, interval=0)


def test_read_version_without_importing(tmp_path):
    make_package(tmp_path / "site", "2026.3.4")
    assert up.read_version(tmp_path / "site") == "2026.3.4"
    assert up.read_version(tmp_path / "missing") is None


# --------------------------------------------------------------------------- #
# PyPI metadata
# --------------------------------------------------------------------------- #

def test_fetch_release_prefers_the_universal_wheel(monkeypatch):
    body = {
        "info": {"version": "2026.1.2"},
        "urls": [
            {"filename": "yt_dlp-2026.1.2-py3-none-win_amd64.whl", "url": "https://x/win.whl",
             "digests": {"sha256": "abc"}, "size": 10},
            {"filename": "yt_dlp-2026.1.2-py3-none-any.whl", "url": "https://x/any.whl",
             "digests": {"sha256": "def"}, "size": 20},
        ],
    }
    monkeypatch.setattr(up, "urlopen", lambda *a, **k: FakeResponse(json.dumps(body).encode()))
    release = up.fetch_release()
    assert release["version"] == "2026.1.2"
    assert release["url"] == "https://x/any.whl"
    assert release["sha256"] == "def"


def test_fetch_release_without_a_wheel_raises(monkeypatch):
    body = {"info": {"version": "2026.1.2"}, "urls": [{"filename": "yt_dlp-2026.1.2.tar.gz"}]}
    monkeypatch.setattr(up, "urlopen", lambda *a, **k: FakeResponse(json.dumps(body).encode()))
    with pytest.raises(ValueError):
        up.fetch_release()


# --------------------------------------------------------------------------- #
# Install
# --------------------------------------------------------------------------- #

def test_install_swaps_in_a_verified_copy(store, tmp_path):
    wheel = make_wheel(tmp_path / "yt_dlp-2026.1.2-py3-none-any.whl")
    result = up.install(store, release_for(wheel))
    assert result["ok"] and result["action"] == "installed"
    assert up.read_version(store.current) == "2026.1.2"
    assert not store.previous.exists()
    assert up.read_state(store)["installed_version"] == "2026.1.2"
    assert not list(store.root.glob("staging-*"))


def test_install_rejects_a_bad_checksum(store, tmp_path):
    wheel = make_wheel(tmp_path / "yt_dlp-2026.1.2-py3-none-any.whl")
    result = up.install(store, release_for(wheel, sha256="0" * 64))
    assert not result["ok"]
    assert "checksum" in result["error"]
    assert not store.current.exists()


def test_failed_healthcheck_keeps_the_working_copy(store, tmp_path, monkeypatch):
    first = make_wheel(tmp_path / "yt_dlp-2026.1.2-py3-none-any.whl", "2026.1.2")
    assert up.install(store, release_for(first, "2026.1.2"))["ok"]

    second = make_wheel(tmp_path / "yt_dlp-2026.2.1-py3-none-any.whl", "2026.2.1")
    monkeypatch.setattr(up, "run_healthcheck",
                        lambda root: {"ok": False, "error": "ImportError: boom"})
    result = up.install(store, release_for(second, "2026.2.1"))

    assert not result["ok"]
    assert "ImportError" in result["error"]
    assert up.read_version(store.current) == "2026.1.2"
    assert not store.previous.exists()
    assert not list(store.root.glob("staging-*"))


def test_install_refuses_a_wheel_with_the_wrong_version(store, tmp_path):
    wheel = make_wheel(tmp_path / "yt_dlp-2026.1.2-py3-none-any.whl", "2025.10.1")
    result = up.install(store, release_for(wheel, "2026.1.2", sha256=None))
    assert not result["ok"]
    assert "2025.10.1" in result["error"]
    assert not store.current.exists()


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #

def test_rollback_restores_the_previous_copy(store, tmp_path):
    for version in ("2026.1.2", "2026.2.1"):
        wheel = make_wheel(tmp_path / f"yt_dlp-{version}-py3-none-any.whl", version)
        assert up.install(store, release_for(wheel, version))["ok"]
    assert up.read_version(store.current) == "2026.2.1"
    assert up.read_version(store.previous) == "2026.1.2"

    result = up.rollback(store)
    assert result["ok"] and result["action"] == "restored"
    assert up.read_version(store.current) == "2026.1.2"
    assert not store.previous.exists()


def test_rollback_without_a_previous_copy_returns_to_bundled(store, tmp_path):
    wheel = make_wheel(tmp_path / "yt_dlp-2026.1.2-py3-none-any.whl")
    assert up.install(store, release_for(wheel))["ok"]

    result = up.rollback(store)
    assert result["ok"] and result["action"] == "bundled"
    assert not store.current.exists()
    assert up.read_state(store)["blocked_version"] == "2026.1.2"


def test_rollback_with_nothing_installed_reports_it(store):
    result = up.rollback(store)
    assert not result["ok"] and result["action"] == "none"


# --------------------------------------------------------------------------- #
# Import priority
# --------------------------------------------------------------------------- #

def test_activate_prefers_the_per_user_copy(store, monkeypatch):
    make_package(store.current, "2026.1.2")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    try:
        assert up.activate(store) == "2026.1.2"
        assert sys.path[0] == str(store.current)
        finder = sys.meta_path[0]
        assert getattr(finder, "ytdlp_updater_finder", False)
        spec = finder.find_spec("yt_dlp")
        assert spec is not None and str(spec.origin).startswith(str(store.current))
        sub = finder.find_spec("yt_dlp.extractor")
        assert sub is not None and sub.submodule_search_locations
        assert finder.find_spec("requests") is None
    finally:
        sys.meta_path[:] = [f for f in sys.meta_path
                            if not getattr(f, "ytdlp_updater_finder", False)]
        if str(store.current) in sys.path:
            sys.path.remove(str(store.current))


def test_activate_ignores_a_broken_copy(store):
    store.current.mkdir(parents=True)
    (store.current / "yt_dlp").mkdir()
    assert up.activate(store) is None


def test_active_status_marks_the_source(store, monkeypatch):
    monkeypatch.setattr(up, "default_store", lambda: store)
    monkeypatch.setattr(up, "active_version", lambda: "2025.11.12")
    status = up.active_status()
    assert status["source"] == "bundled" and status["path"] is None

    make_package(store.current, "2026.1.2")
    status = up.active_status()
    assert status["source"] == "updated"
    assert status["path"] == str(store.current)


# --------------------------------------------------------------------------- #
# update_once
# --------------------------------------------------------------------------- #

def test_update_once_installs_when_pypi_is_newer(store, tmp_path, monkeypatch):
    wheel = make_wheel(tmp_path / "yt_dlp-2026.1.2-py3-none-any.whl")
    monkeypatch.setattr(up, "default_store", lambda: store)
    monkeypatch.setattr(up, "active_version", lambda: "2025.11.12")
    monkeypatch.setattr(up, "fetch_release", lambda timeout=20: release_for(wheel))

    messages = []
    result = up.update_once(store, force=True, log_cb=messages.append)

    assert result["ok"] and result["action"] == "installed"
    assert up.read_version(store.current) == "2026.1.2"
    assert any("newest on PyPI" in m for m in messages)


def test_update_once_skips_a_recent_check(store, monkeypatch):
    import time

    up.write_state(store, last_check=int(time.time()))

    def explode(*_a, **_k):
        raise AssertionError("fetch_release must not run when the check is not due")

    monkeypatch.setattr(up, "fetch_release", explode)
    assert up.update_once(store, force=False)["action"] == "skipped"


def test_update_once_reports_offline(store, monkeypatch):
    def offline(*_a, **_k):
        raise OSError("network unreachable")

    monkeypatch.setattr(up, "fetch_release", offline)
    result = up.update_once(store, force=True)
    assert result["action"] == "error"
    assert "network unreachable" in result["error"]


def test_update_once_does_not_reinstall_a_blocked_version(store, tmp_path, monkeypatch):
    wheel = make_wheel(tmp_path / "yt_dlp-2026.1.2-py3-none-any.whl")
    up.write_state(store, blocked_version="2026.1.2")
    monkeypatch.setattr(up, "active_version", lambda: "2025.11.12")
    monkeypatch.setattr(up, "fetch_release", lambda timeout=20: release_for(wheel))

    result = up.update_once(store, force=True)
    assert result["action"] == "blocked"
    assert not store.current.exists()


def test_update_once_reports_up_to_date(store, tmp_path, monkeypatch):
    wheel = make_wheel(tmp_path / "yt_dlp-2026.1.2-py3-none-any.whl")
    monkeypatch.setattr(up, "active_version", lambda: "2026.1.2")
    monkeypatch.setattr(up, "fetch_release", lambda timeout=20: release_for(wheel))

    result = up.update_once(store, force=True)
    assert result["action"] == "up-to-date"
    assert not store.current.exists()


# --------------------------------------------------------------------------- #
# Child-process health check (the path a built .exe uses)
# --------------------------------------------------------------------------- #

def _run_child(args, timeout=120):
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_healthcheck_passes_for_a_real_package(tmp_path):
    root = make_package(tmp_path / "site")
    result = REAL_RUN_HEALTHCHECK(root)
    assert result["ok"], result
    assert result["version"] == "2026.1.2"
    assert result["extractors"] == 150


def test_healthcheck_rejects_a_folder_without_ytdlp(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    result = REAL_RUN_HEALTHCHECK(root)
    assert not result["ok"]
    assert result["error"]


def test_healthcheck_rejects_an_import_error(tmp_path):
    root = tmp_path / "bad"
    make_package(root)
    (root / "yt_dlp" / "__init__.py").write_text("import does_not_exist\n", encoding="utf-8")
    result = REAL_RUN_HEALTHCHECK(root)
    assert not result["ok"]
    assert "does_not_exist" in result["error"] or "ModuleNotFound" in result["error"]


def test_frozen_style_entry_point_writes_a_result(tmp_path):
    """The .exe re-enters itself with --ytdlp-healthcheck; main.py must handle it."""
    root = make_package(tmp_path / "site")
    out = tmp_path / "result.json"
    proc = _run_child(["main.py", "--ytdlp-healthcheck", str(root),
                       "--ytdlp-healthcheck-out", str(out)])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] and payload["version"] == "2026.1.2"
