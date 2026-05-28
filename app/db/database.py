import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional


DB_PATH = os.getenv("DB_PATH", "data/reports.db")


def get_connection() -> sqlite3.Connection:
    db_path = os.getenv("DB_PATH", DB_PATH)
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                company_url TEXT    NOT NULL,
                company_name TEXT,
                status      TEXT    NOT NULL DEFAULT 'running',
                report_json TEXT,
                token_cost  REAL    DEFAULT 0.0,
                run_ms      INTEGER DEFAULT 0,
                error_log   TEXT,
                created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_url_created
            ON reports (company_url, created_at)
        """)
        conn.commit()


def get_cached_report(company_url: str, max_age_hours: int = 24) -> Optional[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM reports
            WHERE company_url = ?
              AND status = 'done'
              AND created_at > ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (company_url, cutoff),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    if result.get("report_json"):
        result["report"] = json.loads(result["report_json"])
    return result


def create_report_record(company_url: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO reports (company_url, status) VALUES (?, 'running')",
            (company_url,),
        )
        conn.commit()
        return cur.lastrowid


def update_report(report_id: int, **kwargs) -> None:
    if not kwargs:
        return
    allowed = {
        "company_url", "company_name", "status", "report_json",
        "token_cost", "run_ms", "error_log",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [report_id]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE reports SET {set_clause} WHERE id = ?", values
        )
        conn.commit()


def list_reports(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, company_url, company_name, status, token_cost, run_ms, created_at
            FROM reports
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_report_by_id(report_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    if result.get("report_json"):
        result["report"] = json.loads(result["report_json"])
    return result
