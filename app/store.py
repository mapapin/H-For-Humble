"""Local persistence. Vault secrets are stored only as authenticated ciphertext."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("HUMBLE_DATA_DIR", ROOT / "data"))


@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / "humble.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def init():
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id), expires REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id), name TEXT NOT NULL,
            url TEXT NOT NULL, created REAL NOT NULL, UNIQUE(user_id, url)
        );
        CREATE TABLE IF NOT EXISTS audits (
            id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id), project_id TEXT REFERENCES projects(id),
            name TEXT NOT NULL, url TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
            config TEXT NOT NULL, results TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '[]',
            progress TEXT NOT NULL DEFAULT '{}', error TEXT, finished REAL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, audit_id TEXT REFERENCES audits(id),
            created REAL NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS audit_owner ON audits(user_id, created);
        CREATE INDEX IF NOT EXISTS event_audit ON events(audit_id, id);
        CREATE TABLE IF NOT EXISTS vault_entries (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
            origin TEXT NOT NULL, secret BLOB NOT NULL, has_login INTEGER NOT NULL,
            header_count INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS vault_owner ON vault_entries(user_id, updated);
        """)
        # Fix workflows: suggested fixes (patch + before/after images), auto-mode rounds, re-evaluation parent.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(audits)")}
        for name, ddl in (("fixes", "TEXT NOT NULL DEFAULT '{}'"), ("rounds", "TEXT NOT NULL DEFAULT '[]'"),
                          ("parent_id", "TEXT"), ("scores", "TEXT NOT NULL DEFAULT '{}'"),
                          ("context", "TEXT NOT NULL DEFAULT '{}'"), ("static_scan", "TEXT NOT NULL DEFAULT '{}'"),
                          ("insight", "TEXT NOT NULL DEFAULT '{}'")):
            if name not in columns:
                db.execute(f"ALTER TABLE audits ADD COLUMN {name} {ddl}")
        # Saved sites: a project the user keeps, with its journey presets and default credentials.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(projects)")}
        for name, ddl in (("saved", "INTEGER NOT NULL DEFAULT 0"), ("journeys", "TEXT NOT NULL DEFAULT '[]'"),
                          ("vault_id", "TEXT"), ("updated", "REAL")):
            if name not in columns:
                db.execute(f"ALTER TABLE projects ADD COLUMN {name} {ddl}")


JSON_FIELDS = ("results", "summary", "progress", "fixes", "rounds", "scores", "context", "static_scan", "insight",
               "journeys")


def event(audit_id, kind, message):
    with connect() as db:
        db.execute("INSERT INTO events(audit_id,created,kind,message) VALUES(?,?,?,?)",
                   (audit_id, time.time(), kind, message))


def update(audit_id, **fields):
    allowed = {"status", "results", "summary", "progress", "error", "finished", "fixes", "rounds", "scores", "context",
               "static_scan", "insight"}
    assert fields and set(fields) <= allowed
    values = [json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()]
    with connect() as db:
        db.execute(f"UPDATE audits SET {','.join(k + '=?' for k in fields)} WHERE id=?", (*values, audit_id))


def serialize(row):
    result = dict(row)
    for key in ("config", *JSON_FIELDS):
        if key in result and result[key] is not None:
            result[key] = json.loads(result[key])
    result.pop("user_id", None)
    return result
