"""SQLite-backed sync state. This is what makes a run a diff, not a re-import."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STATE_PATH = Path.home() / ".config" / "canvas-calendar" / "state.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    uid         TEXT PRIMARY KEY,
    due_at      TEXT,
    title_hash  TEXT NOT NULL,
    source      TEXT NOT NULL,
    last_synced TEXT
)
"""


@dataclass(frozen=True)
class Record:
    uid: str
    due_at: str | None
    title_hash: str
    source: str


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def upsert(self, uid: str, *, due_at: str | None, title_hash: str, source: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO events (uid, due_at, title_hash, source, last_synced) "
                "VALUES (?,?,?,?,datetime('now')) ON CONFLICT(uid) DO UPDATE SET "
                "due_at=excluded.due_at, title_hash=excluded.title_hash, "
                "source=excluded.source, last_synced=excluded.last_synced",
                (uid, due_at, title_hash, source),
            )

    def get(self, uid: str) -> Record | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT uid, due_at, title_hash, source FROM events WHERE uid=?", (uid,)
            ).fetchone()
        return Record(*row) if row else None

    def all_uids(self) -> set[str]:
        with self._conn() as c:
            return {r[0] for r in c.execute("SELECT uid FROM events")}

    def delete(self, uid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM events WHERE uid=?", (uid,))
