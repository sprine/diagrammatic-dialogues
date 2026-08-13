"""Store. A trail is a rooted tree of cards; each card is one Claude turn."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("SID_DB", Path(__file__).resolve().parent.parent / "diagrams.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS trail (
    id          TEXT PRIMARY KEY,
    target_dir  TEXT NOT NULL,
    -- code | docs: chosen once when the trail is opened, since every card in it
    -- resumes the same session and inherits the root prompt's framing.
    kind        TEXT NOT NULL DEFAULT 'code',
    title       TEXT NOT NULL DEFAULT 'untitled',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS card (
    id          TEXT PRIMARY KEY,
    trail_id    TEXT NOT NULL REFERENCES trail(id) ON DELETE CASCADE,
    parent_id   TEXT REFERENCES card(id) ON DELETE CASCADE,
    depth       INTEGER NOT NULL DEFAULT 0,
    -- the remark that produced this card, and where on the parent it was planted
    remark      TEXT NOT NULL DEFAULT '',
    anchor_node TEXT,
    anchor_label TEXT NOT NULL DEFAULT '',
    -- what came back
    status      TEXT NOT NULL DEFAULT 'running',   -- running | done | error
    title       TEXT NOT NULL DEFAULT '',
    ascii       TEXT NOT NULL DEFAULT '',
    answer      TEXT NOT NULL DEFAULT '',
    points      TEXT NOT NULL DEFAULT '[]',       -- json: the supporting specifics
    error       TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '[]',        -- json: what the agent actually looked at
    changes     TEXT NOT NULL DEFAULT '[]',        -- json: files written, when write mode is on
    -- how it was produced
    model       TEXT NOT NULL DEFAULT '',
    effort      TEXT NOT NULL DEFAULT '',
    write_mode  INTEGER NOT NULL DEFAULT 0,
    web_mode    INTEGER NOT NULL DEFAULT 0,
    session_id  TEXT,
    cost_usd    REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS card_trail  ON card(trail_id);
CREATE INDEX IF NOT EXISTS card_parent ON card(parent_id);

-- One press of the report button: a render that came out wrong, filed as
-- training data. The file on disk is the artefact; this is the index.
CREATE TABLE IF NOT EXISTS report (
    id          TEXT PRIMARY KEY,
    card_id     TEXT REFERENCES card(id) ON DELETE SET NULL,
    card_title  TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# CREATE TABLE IF NOT EXISTS will not widen a table that already exists, and a
# trail is worth keeping across a schema change.
ADDED_COLUMNS = [
    ("card", "points", "TEXT NOT NULL DEFAULT '[]'"),
    ("card", "web_mode", "INTEGER NOT NULL DEFAULT 0"),
    ("trail", "kind", "TEXT NOT NULL DEFAULT 'code'"),
]


def init_schema():
    with db() as conn:
        conn.executescript(SCHEMA)
        for table, column, decl in ADDED_COLUMNS:
            present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def card_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ("evidence", "changes", "points"):
        d[field] = json.loads(d.get(field) or "[]")
    d["write_mode"] = bool(d.get("write_mode"))
    d["web_mode"] = bool(d.get("web_mode"))
    return d


def lineage(conn, card_id: str) -> list[dict]:
    """Root -> card. This chain is the horizontal strip the user scrolls."""
    chain = []
    cur = card_id
    while cur:
        row = conn.execute("SELECT * FROM card WHERE id = ?", (cur,)).fetchone()
        if not row:
            break
        chain.append(card_row(row))
        cur = row["parent_id"]
    return chain[::-1]


def children(conn, card_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM card WHERE parent_id = ? ORDER BY created_at", (card_id,)
    ).fetchall()
    return [card_row(r) for r in rows]
