"""SQLite persistence for signals, feedback, policy versions and audit."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
              id TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              insight_id TEXT NOT NULL,
              verdict TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '',
              held_out INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policy_current (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              version INTEGER NOT NULL,
              name TEXT NOT NULL,
              source TEXT NOT NULL,
              knobs TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policy_version (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              source TEXT NOT NULL,
              decision TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policy_event (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              version INTEGER NOT NULL,
              name TEXT NOT NULL,
              source TEXT NOT NULL,
              decision TEXT NOT NULL,
              reason TEXT NOT NULL,
              arm TEXT NOT NULL,
              parent_version INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cache (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              payload TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def clear(self) -> None:
        self.conn.executescript(
            """
            DELETE FROM signals;
            DELETE FROM feedback;
            DELETE FROM policy_current;
            DELETE FROM policy_version;
            DELETE FROM policy_event;
            DELETE FROM audit;
            DELETE FROM cache;
            """
        )
        self.conn.commit()

    def signals(self) -> list[dict]:
        rows = self.conn.execute("SELECT payload FROM signals ORDER BY id").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def replace_signals(self, signals: list[dict]) -> None:
        self.conn.execute("DELETE FROM signals")
        for signal in signals:
            self.conn.execute(
                "INSERT INTO signals (id, payload) VALUES (?, ?)",
                (signal["id"], json.dumps(signal, ensure_ascii=False)),
            )
        self.conn.commit()

    def upsert_signals(self, signals: list[dict]) -> int:
        for signal in signals:
            self.conn.execute(
                "INSERT OR REPLACE INTO signals (id, payload) VALUES (?, ?)",
                (signal["id"], json.dumps(signal, ensure_ascii=False)),
            )
        self.conn.commit()
        return len(signals)

    def feedback(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT insight_id, verdict, note, held_out, created_at, id FROM feedback ORDER BY id"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "insight_id": row["insight_id"],
                "verdict": row["verdict"],
                "note": row["note"],
                "held_out": bool(row["held_out"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def add_feedback(self, insight_id: str, verdict: str, note: str, held_out: bool) -> dict:
        cur = self.conn.execute(
            """
            INSERT INTO feedback (insight_id, verdict, note, held_out, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (insight_id, verdict, note, 1 if held_out else 0, now()),
        )
        self.conn.commit()
        return {
            "id": cur.lastrowid,
            "insight_id": insight_id,
            "verdict": verdict,
            "note": note,
            "held_out": held_out,
        }

    def current(self) -> dict | None:
        row = self.conn.execute("SELECT version, name, source, knobs FROM policy_current WHERE id = 1").fetchone()
        if row is None:
            return None
        return {
            "version": row["version"],
            "name": row["name"],
            "source": row["source"],
            "knobs": json.loads(row["knobs"]),
        }

    def set_current(self, version: int, name: str, source: str, knobs: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO policy_current (id, version, name, source, knobs)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              version = excluded.version,
              name = excluded.name,
              source = excluded.source,
              knobs = excluded.knobs
            """,
            (version, name, source, json.dumps(knobs, ensure_ascii=False)),
        )
        self.conn.commit()

    def upsert_version(self, saved: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO policy_version (version, name, source, decision)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(version) DO UPDATE SET
              name = excluded.name,
              source = excluded.source,
              decision = excluded.decision
            """,
            (
                int(saved["version"]),
                saved.get("name") or "",
                saved.get("source") or "",
                saved.get("decision") or "",
            ),
        )
        self.conn.commit()

    def source_for(self, version: int) -> str:
        row = self.conn.execute(
            "SELECT source FROM policy_version WHERE version = ?", (version,)
        ).fetchone()
        if row and row["source"]:
            return row["source"]
        event = self.conn.execute(
            """
            SELECT source FROM policy_event
            WHERE version = ? AND source != ''
            ORDER BY id DESC LIMIT 1
            """,
            (version,),
        ).fetchone()
        return event["source"] if event else ""

    def add_event(self, event: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO policy_event
              (version, name, source, decision, reason, arm, parent_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(event.get("version") or 0),
                event.get("name") or "",
                event.get("source") or "",
                event.get("decision") or "",
                event.get("reason") or "",
                event.get("arm") or "",
                int(event.get("parent_version") or 0),
                now(),
            ),
        )
        self.conn.commit()

    def events(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT id, version, name, decision, reason, arm, parent_version, created_at
            FROM policy_event ORDER BY id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def versions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT version, name, decision FROM policy_version ORDER BY version"
        ).fetchall()
        return [dict(row) for row in rows]

    def add_audit(self, payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO audit (payload, created_at) VALUES (?, ?)",
            (json.dumps(payload, ensure_ascii=False), now()),
        )
        self.conn.commit()

    def audit(self, limit: int = 40) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, payload, created_at FROM audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            item = json.loads(row["payload"])
            item["audit_id"] = row["id"]
            item["created_at"] = row["created_at"]
            out.append(item)
        return out

    def set_cache(self, payload: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO cache (id, payload) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
            """,
            (json.dumps(payload, ensure_ascii=False),),
        )
        self.conn.commit()

    def cache(self) -> dict:
        row = self.conn.execute("SELECT payload FROM cache WHERE id = 1").fetchone()
        if row is None:
            return {}
        return json.loads(row["payload"])
