from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    frame_index INTEGER,
    pts_ms INTEGER,
    agent TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS processed_files (
    path TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    job_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    force_run INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    result_json TEXT,
    event_id INTEGER,
    meta_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_job ON agent_runs(job_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);

CREATE TABLE IF NOT EXISTS agent_orchestrations (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    current_step INTEGER NOT NULL DEFAULT 0,
    force_run INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_orch_job ON agent_orchestrations(job_id);
CREATE INDEX IF NOT EXISTS idx_agent_orch_status ON agent_orchestrations(status);
"""


async def _migrate(conn: aiosqlite.Connection) -> None:
    cur = await conn.execute("PRAGMA table_info(jobs)")
    cols = {str(r[1]) for r in await cur.fetchall()}
    if "summary_json" not in cols:
        await conn.execute("ALTER TABLE jobs ADD COLUMN summary_json TEXT")
        await conn.commit()
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_orchestrations'"
    )
    if await cur.fetchone() is None:
        await conn.executescript(
            """
            CREATE TABLE agent_orchestrations (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                status TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                current_step INTEGER NOT NULL DEFAULT 0,
                force_run INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );
            CREATE INDEX idx_agent_orch_job ON agent_orchestrations(job_id);
            CREATE INDEX idx_agent_orch_status ON agent_orchestrations(status);
            """
        )
        await conn.commit()


async def connect(db_path: Path) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA)
    await conn.commit()
    await _migrate(conn)
    return conn
