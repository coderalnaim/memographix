from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            hash TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            language TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            line INTEGER NOT NULL,
            signature TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS edges (
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            relation TEXT NOT NULL,
            path TEXT NOT NULL,
            line INTEGER,
            confidence TEXT NOT NULL DEFAULT 'EXTRACTED',
            PRIMARY KEY (source, target, relation, path, line)
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_hash TEXT NOT NULL,
            normalized_intent TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            validation_json TEXT NOT NULL DEFAULT '{}',
            token_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_hash ON tasks(task_hash);
        CREATE TABLE IF NOT EXISTS task_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            hash TEXT,
            symbol_id TEXT,
            line_start INTEGER,
            line_end INTEGER,
            excerpt TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS benchmark_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            corpus_path TEXT NOT NULL,
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            question TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            task_id INTEGER,
            packet_tokens INTEGER NOT NULL DEFAULT 0,
            baseline_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_saved_tokens INTEGER NOT NULL DEFAULT 0,
            stale_prevention INTEGER NOT NULL DEFAULT 0,
            skipped_reason TEXT NOT NULL DEFAULT '',
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_events_type_time
            ON memory_events(event_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_memory_events_task
            ON memory_events(task_id);
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        raw = json.loads(value)
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}
