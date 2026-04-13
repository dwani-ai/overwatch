from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS factorio_sessions (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS factorio_frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            rel_path TEXT NOT NULL,
            observed_at REAL NOT NULL,
            bytes_len INTEGER NOT NULL,
            UNIQUE(session_id, step_index),
            FOREIGN KEY(session_id) REFERENCES factorio_sessions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_factorio_frames_session
            ON factorio_frames(session_id);
        """
    )
    conn.commit()


@dataclass
class FrameRecord:
    session_id: str
    step_index: int
    rel_path: str
    observed_at: float
    bytes_len: int


class FactorioSessionStore:
    """
    Filesystem PNG frames + SQLite index under ``root`` (typically ``Settings.factorio_data_root``).
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._db_path = self.root / "sessions.sqlite3"
        self._conn = _connect(self._db_path)
        _init_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    def create_session(self, *, meta: dict[str, Any] | None = None) -> str:
        sid = str(uuid.uuid4())
        now = time.time()
        self._conn.execute(
            "INSERT INTO factorio_sessions (id, created_at, meta_json) VALUES (?, ?, ?)",
            (sid, now, json.dumps(meta or {})),
        )
        self._conn.commit()
        (self.root / "frames" / sid).mkdir(parents=True, exist_ok=True)
        return sid

    def append_frame(self, session_id: str, step_index: int, png_bytes: bytes) -> FrameRecord:
        rel = f"frames/{session_id}/{step_index:06d}.png"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes)
        now = time.time()
        blen = len(png_bytes)
        self._conn.execute(
            """
            INSERT INTO factorio_frames (session_id, step_index, rel_path, observed_at, bytes_len)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, step_index, rel, now, blen),
        )
        self._conn.commit()
        return FrameRecord(session_id, step_index, rel, now, blen)

    def list_frames(self, session_id: str) -> list[FrameRecord]:
        cur = self._conn.execute(
            """
            SELECT session_id, step_index, rel_path, observed_at, bytes_len
            FROM factorio_frames WHERE session_id = ? ORDER BY step_index
            """,
            (session_id,),
        )
        rows = cur.fetchall()
        return [
            FrameRecord(r[0], int(r[1]), r[2], float(r[3]), int(r[4]))
            for r in rows
        ]

    def frame_path(self, record: FrameRecord) -> Path:
        return self.root / record.rel_path
