import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_FILE = "history.db"

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS restore_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                db_type TEXT NOT NULL,
                db_name TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_size_mb REAL NOT NULL,
                created_db INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                duration_sec REAL NOT NULL,
                logs TEXT,
                source_path TEXT,
                destination TEXT,
                user_name TEXT,
                server_user TEXT,
                db_user TEXT,
                executed_by TEXT
            )
        """)
        # Auto-migrate schema if columns are missing
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(restore_history)")
        existing_cols = {row["name"] for row in cursor.fetchall()}
        if "source_path" not in existing_cols:
            cursor.execute("ALTER TABLE restore_history ADD COLUMN source_path TEXT")
        if "destination" not in existing_cols:
            cursor.execute("ALTER TABLE restore_history ADD COLUMN destination TEXT")
        if "user_name" not in existing_cols:
            cursor.execute("ALTER TABLE restore_history ADD COLUMN user_name TEXT")
        if "server_user" not in existing_cols:
            cursor.execute("ALTER TABLE restore_history ADD COLUMN server_user TEXT")
        if "db_user" not in existing_cols:
            cursor.execute("ALTER TABLE restore_history ADD COLUMN db_user TEXT")
        if "executed_by" not in existing_cols:
            cursor.execute("ALTER TABLE restore_history ADD COLUMN executed_by TEXT")
        conn.commit()

def add_history(
    db_type: str,
    db_name: str,
    file_name: str,
    file_size_mb: float,
    created_db: bool,
    status: str,
    duration_sec: float,
    logs: str = "",
    source_path: str = "",
    destination: str = "",
    server_user: str = "",
    db_user: str = "",
    executed_by: str = "",
    user_name: str = ""
) -> int:
    init_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    s_path = source_path if source_path else file_name
    dest = destination if destination else db_name
    exec_user = executed_by if executed_by else user_name
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO restore_history 
            (timestamp, db_type, db_name, file_name, file_size_mb, created_db, status, duration_sec, logs, source_path, destination, user_name, server_user, db_user, executed_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_str,
            db_type,
            db_name,
            file_name,
            round(file_size_mb, 2),
            1 if created_db else 0,
            status,
            round(duration_sec, 2),
            logs,
            s_path,
            dest,
            user_name or exec_user,
            server_user,
            db_user,
            exec_user
        ))
        conn.commit()
        return cursor.lastrowid

def get_history(limit: int = 200) -> List[Dict[str, Any]]:
    init_db()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM restore_history ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            # Graceful fallbacks for older rows
            if not d.get("server_user"):
                d["server_user"] = "user" if "SSH" in d.get("db_type", "") else os.getenv("USERNAME", "local")
            if not d.get("db_user"):
                d["db_user"] = "app_dev"
            if not d.get("executed_by"):
                d["executed_by"] = d.get("user_name") or "-"
            result.append(d)
        return result

get_restore_history = get_history

def get_restore_record(record_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM restore_history WHERE id = ?", (record_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        if not d.get("server_user"):
            d["server_user"] = "user" if "SSH" in d.get("db_type", "") else os.getenv("USERNAME", "local")
        if not d.get("db_user"):
            d["db_user"] = "app_dev"
        if not d.get("executed_by"):
            d["executed_by"] = d.get("user_name") or "-"
        return d

def clear_history() -> None:
    init_db()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM restore_history")
        conn.commit()

def delete_history_item(record_id: int) -> None:
    init_db()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM restore_history WHERE id = ?", (record_id,))
        conn.commit()
