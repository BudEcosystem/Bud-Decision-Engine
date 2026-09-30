"""The studio's own database, DATA/studio.db: decisions, templates, examples, feedback and settings.

One SQLite file in WAL mode. Writes go through one connection guarded by a lock (one transaction per decision, a few
milliseconds against a model's tens to hundreds); reads use a read-only connection per thread, which WAL keeps
consistent while writes go on. Model worker processes never open it.

    db.open(DATA / "studio.db")          # at startup: PRAGMAs, checks, migrations
    db.write(lambda c: c.execute(...))   # one transaction
    db.read().execute(...)               # a snapshot read
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sqlite3
import threading
import time
from pathlib import Path

MIN_SQLITE = (3, 37, 0)             # STRICT tables
MIGRATIONS = Path(__file__).resolve().parent / "migrations"
WS = "ws_local"                     # the one workspace until the studio has several users


class HistoryUnavailable(RuntimeError):
    """History is switched off (SQLite too old, or a newer database opened read-only). Decisions still work."""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data_dir = self.path.parent
        self.lock = threading.RLock()
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self.writer: sqlite3.Connection | None = None
        self.available = False
        self.read_only = False
        self.reason = ""
        self.search = False
        self.applied: list[int] = []

    # -------------------------------------------------------------------------------------------------------- connect
    def _connect(self, ro: bool = False) -> sqlite3.Connection:
        uri = f"file:{self.path.as_posix()}{'?mode=ro' if ro else ''}"
        c = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA cache_size=-65536")
        c.execute("PRAGMA temp_store=MEMORY")
        self._conns.append(c)
        return c

    def open(self) -> "Database":
        if sqlite3.sqlite_version_info < MIN_SQLITE:
            self.reason = (f"History needs SQLite {'.'.join(map(str, MIN_SQLITE))} or newer; this Python has "
                           f"{sqlite3.sqlite_version}. Decisions still work, but nothing is saved.")
            return self
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists() or self.path.stat().st_size == 0
        w = self._connect()
        if fresh:
            w.execute("PRAGMA auto_vacuum=INCREMENTAL")     # only takes effect on an empty file
        w.execute("PRAGMA journal_mode=WAL")
        w.execute("PRAGMA synchronous=NORMAL")
        self.writer = w
        for p in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                if p.exists():
                    os.chmod(p, 0o600)
            except OSError:
                pass
        self.migrate()
        self.available = True
        try:
            self.search = bool(w.execute("SELECT 1 FROM sqlite_master WHERE name='decision_fts'").fetchone())
        except sqlite3.Error:
            self.search = False
        return self

    def close(self):
        for c in self._conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._conns.clear()
        self.writer = None
        self._local = threading.local()
        self.available = False

    # -------------------------------------------------------------------------------------------------------- use
    def write(self, fn):
        """Run fn(conn) in one IMMEDIATE transaction; commit on success, roll back on any exception."""
        if not self.available:
            raise HistoryUnavailable(self.reason or "History is not available.")
        if self.read_only:
            raise HistoryUnavailable(self.reason)
        with self.lock:
            c = self.writer
            c.execute("BEGIN IMMEDIATE")
            try:
                out = fn(c)
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")
            return out

    def read(self) -> sqlite3.Connection:
        if not self.available:
            raise HistoryUnavailable(self.reason or "History is not available.")
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._connect(ro=True)
            self._local.conn = c
        return c

    # -------------------------------------------------------------------------------------------------------- migrations
    def migrate(self):
        w = self.writer
        w.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                  "checksum TEXT NOT NULL, applied_at INTEGER NOT NULL) STRICT")
        done = {r["version"]: r for r in w.execute("SELECT * FROM schema_migrations")}
        files = sorted(p for p in MIGRATIONS.iterdir() if p.suffix in (".sql", ".py") and p.name[:4].isdigit())
        known = max((int(p.name[:4]) for p in files), default=0)
        newest = max(done, default=0)
        if newest > known:
            self.read_only = True
            self.reason = ("This history was written by a newer version of the studio, so it opens read-only. "
                           "Update the studio to keep saving decisions.")
            return
        pending = []
        for p in files:
            v = int(p.name[:4])
            checksum = hashlib.sha256(p.read_bytes()).hexdigest()
            if v in done:
                if done[v]["checksum"] != checksum:
                    raise RuntimeError(f"Migration {p.name} changed after it was applied to {self.path}. "
                                       "Restore the original file; migrations are never edited once released.")
                continue
            pending.append((v, p, checksum))
        if pending and done:
            self._backup(pending[0][0])
        for v, p, checksum in pending:
            w.execute("BEGIN IMMEDIATE")
            try:
                if p.suffix == ".sql":
                    for stmt in _statements(p.read_text()):
                        w.execute(stmt)
                else:
                    spec = importlib.util.spec_from_file_location(f"basal_migration_{v}", p)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    mod.run(w, self)
                w.execute("INSERT INTO schema_migrations VALUES (?,?,?,?)", (v, p.stem, checksum, int(time.time() * 1000)))
                w.execute(f"PRAGMA user_version={v}")
                w.execute("COMMIT")
            except BaseException:
                w.execute("ROLLBACK")
                raise
            self.applied.append(v)

    def _backup(self, version: int):
        d = self.data_dir / "backups"
        d.mkdir(exist_ok=True)
        target = d / f"studio-m{version:04d}-{time.strftime('%Y%m%dT%H%M%S')}.db"
        try:
            self.writer.execute("VACUUM INTO ?", (str(target),))
            os.chmod(target, 0o600)
        except (sqlite3.Error, OSError) as e:
            print(f"[history] backup before migration {version} failed: {e}", flush=True)
            return
        for old in sorted(d.glob("studio-m*.db"))[:-3]:
            try:
                old.unlink()
            except OSError:
                pass


def _statements(sql: str) -> list[str]:
    """Split a migration into statements, keeping trigger bodies (BEGIN ... END;) whole."""
    out, buf = [], []
    for line in sql.splitlines():
        s = line.split("--", 1)[0] if not line.strip().startswith("--") else ""
        if not s.strip():
            continue
        buf.append(s)
        joined = "\n".join(buf)
        if joined.rstrip().endswith(";") and sqlite3.complete_statement(joined):
            out.append(joined.strip())
            buf = []
    if "".join(buf).strip():
        out.append("\n".join(buf))
    return out


_db: Database | None = None


def open(path) -> Database:   # noqa: A001 - the module's natural verb
    global _db
    if _db is not None:
        _db.close()
    _db = Database(Path(path)).open()
    return _db


def get() -> Database:
    if _db is None:
        raise HistoryUnavailable("History has not been opened.")
    return _db


def available() -> bool:
    return _db is not None and _db.available


def write(fn):
    return get().write(fn)


def read() -> sqlite3.Connection:
    return get().read()


def now_ms() -> int:
    return int(time.time() * 1000)
