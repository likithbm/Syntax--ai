"""SQLite history storage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    image_filename TEXT NOT NULL,
    diagram_type TEXT NOT NULL,
    language TEXT NOT NULL,
    requirement TEXT NOT NULL DEFAULT '',
    extracted_logic TEXT NOT NULL,
    generated_code TEXT NOT NULL,
    code_filename TEXT NOT NULL DEFAULT '',
    security_findings TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    verification_message TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_history_ts ON history(timestamp DESC);
"""


def _connect(path: Path | str) -> sqlite3.Connection:
    p = Path(path)
    if str(path) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path | str) -> None:
    with closing(_connect(path)) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def save_result(path: Path | str, rec: dict) -> int:
    init_db(path)
    with closing(_connect(path)) as conn:
        cur = conn.execute(
            """INSERT INTO history (timestamp, image_filename, diagram_type, language, requirement,
               extracted_logic, generated_code, code_filename, security_findings,
               verification_status, verification_message, model)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.get("timestamp") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                rec["image_filename"], rec["diagram_type"], rec["language"], rec.get("requirement", ""),
                json.dumps(rec["extracted_logic"]), rec["generated_code"], rec.get("code_filename", ""),
                json.dumps(rec["security_findings"]), rec["verification_status"],
                rec.get("verification_message", ""), rec.get("model", ""),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def _row_to_dict(row: sqlite3.Row, full: bool) -> dict:
    d = {
        "id": row["id"], "timestamp": row["timestamp"], "image_filename": row["image_filename"],
        "diagram_type": row["diagram_type"], "language": row["language"],
        "verification_status": row["verification_status"],
    }
    if full:
        d.update({
            "requirement": row["requirement"],
            "extracted_logic": json.loads(row["extracted_logic"]),
            "generated_code": row["generated_code"],
            "code_filename": row["code_filename"],
            "security_findings": json.loads(row["security_findings"]),
            "verification_message": row["verification_message"],
            "model": row["model"],
        })
    return d


def list_history(path: Path | str, limit: int = 50) -> list[dict]:
    init_db(path)
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    return [_row_to_dict(r, full=False) for r in rows]


def get_history(path: Path | str, item_id: int) -> dict | None:
    init_db(path)
    with closing(_connect(path)) as conn:
        row = conn.execute("SELECT * FROM history WHERE id = ?", (item_id,)).fetchone()
    return _row_to_dict(row, full=True) if row else None


def delete_history(path: Path | str, item_id: int) -> bool:
    init_db(path)
    with closing(_connect(path)) as conn:
        cur = conn.execute("DELETE FROM history WHERE id = ?", (item_id,))
        conn.commit()
        return cur.rowcount > 0


def clear_history(path: Path | str) -> int:
    init_db(path)
    with closing(_connect(path)) as conn:
        cur = conn.execute("DELETE FROM history")
        conn.commit()
        return cur.rowcount
