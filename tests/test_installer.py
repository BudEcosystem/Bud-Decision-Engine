"""Regression tests for installer/engine.py (no network, no PyTorch needed).

Both guard the first-run failure on macOS, where the app's data folder is ~/Library/Application Support:
* uv splits --constraint / --override paths at spaces, so the install must never pass a file path that way;
* PyTorch can print warnings (which begin with a file path) to stderr, and those must never be read as data.

    python -m pytest tests/test_installer.py -q
"""
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("engine", ROOT / "installer" / "engine.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

WARNING = "{d}/lib/python3.12/site-packages/torch/__init__.py:1: UserWarning: something PyTorch printed"


@pytest.fixture
def noisy_python(tmp_path):
    """A stand-in "python" in a folder with a space that answers like PyTorch would, plus a warning on stderr."""
    if os.name == "nt":
        pytest.skip("uses a POSIX shell script as a stand-in interpreter")
    d = tmp_path / "Application Support" / "engine-env"
    (d / "bin").mkdir(parents=True)
    py = d / "bin" / "python"
    check = json.dumps({"ok": True, "torch": "2.11.0", "transformers": "5.17.0"})
    py.write_text(f"""#!/bin/sh
echo '{WARNING.format(d=d)}' >&2
case "$2" in
  *json.dumps*) echo '{check}' ;;            # the device check
  *torch.__version__*) echo 2.11.0 ;;        # the version question
esac
echo '{WARNING.format(d=d)}' >&2
""")
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    return py


def test_version_ignores_warnings(noisy_python):
    assert engine.torch_version(noisy_python) == "2.11.0"


def test_device_check_ignores_warnings(noisy_python):
    assert engine.verify(noisy_python, "mps") == (True, "2.11.0")


def test_install_never_passes_paths_to_split_prone_uv_options(noisy_python, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(engine, "stream", lambda cmd, env=None: seen.append(cmd) or 0)
    monkeypatch.setattr(engine, "detect", lambda: {"os": "macos", "arch": "aarch64", "accelerators": [
        {"id": "mps", "kind": "apple", "name": "Apple M3", "memory_gb": 36, "unified_memory": True},
        {"id": "cpu", "kind": "cpu", "name": "Apple M3", "memory_gb": 36}], "recommended": "mps", "notes": []})
    monkeypatch.setattr(engine, "emit", lambda **kw: None)
    venv = noisy_python.parent.parent
    engine.install("mps", venv, tmp_path / "Application Support" / "data", "uv")
    uv_calls = [c for c in seen if c[:3] == ["uv", "pip", "install"]]
    assert uv_calls, "the installer ran no uv pip install"
    for cmd in uv_calls:
        for flag in ("-c", "--constraint", "--constraints", "--override", "--overrides"):
            assert flag not in cmd, f"{flag} would split a path with spaces: {cmd}"
    libs = next(c for c in uv_calls if str(engine.PROJECT / "requirements.txt") in c)
    assert "torch==2.11.0" in libs, "the installed PyTorch build must be pinned while the libraries install"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
