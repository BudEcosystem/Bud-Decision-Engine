"""Shared test setup: the repository root is importable (no install needed), and unit tests never touch the real
studio data (basal.paths reads BASAL_DATA when first imported)."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BASAL_DATA", tempfile.mkdtemp(prefix="basal-tests-"))
os.environ.setdefault("BASAL_FAKE_MODEL", "1")
