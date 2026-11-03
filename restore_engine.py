import os
import sys
import glob
import shutil
import subprocess
import time
import gzip
from typing import Dict, Any, Callable, Optional, Tuple
import pymysql
import psycopg

def find_tool_executable(db_type: str, tool_name: str, custom_path: Optional[str] = None) -> Optional[str]:
    """Finds client CLI binaries (psql, pg_restore, mariadb, mysql) on Windows/OS."""
    # 1. Custom path check
    if custom_path and os.path.exists(custom_path):
        if os.path.isfile(custom_path):
            return custom_path
        # If directory specified, check if tool is inside
        cand = os.path.join(custom_path, tool_name if not tool_name.endswith(".exe") else tool_name)
        if os.path.exists(cand):
            return cand
        cand_exe = cand + ".exe"
        if os.path.exists(cand_exe):
            return cand_exe

    # 2. Check PATH
    found = shutil.which(tool_name)
    if found:
        return found
    if not tool_name.endswith(".exe"):
        found = shutil.which(f"{tool_name}.exe")
        if found:
            return found

    # 3. Search common Windows directories
    patterns = []
    if db_type.lower() == "postgresql":
        patterns = [
            r"C:\Program Files\PostgreSQL\*\bin\\" + tool_name + ".exe",
            r"C:\Program Files (x86)\PostgreSQL\*\bin\\" + tool_name + ".exe",
            os.path.expanduser(r"~\AppData\Local\Programs\PostgreSQL\*\bin\\" + tool_name + ".exe")
        ]
    elif db_type.lower() in ("mariadb", "mysql"):
        patterns = [
            r"C:\Program Files\MariaDB *\bin\\" + tool_name + ".exe",
            r"C:\Program Files\MySQL\MySQL Server *\bin\\" + tool_name + ".exe",
            r"C:\xampp\mysql\bin\\" + tool_name + ".exe",
            r"C:\laragon\bin\mysql\*\bin\\" + tool_name + ".exe",
            r"C:\wamp64\bin\mysql\*\bin\\" + tool_name + ".exe",
            r"C:\Program Files (x86)\MySQL\MySQL Server *\bin\\" + tool_name + ".exe",
        ]

    for p in patterns:
        matches = glob.glob(p)
        if matches:
            # Sort to pick highest version if multiple
            matches.sort(reverse=True)
            return matches[0]

    return None


def prepare_mariadb_database(config: Dict[str, Any], db_name: str, drop_existing: bool = False, log_fn: Optional[Callable[[str], None]] = None) -> None:
    def log(msg: str):
        if log_fn: log_fn(msg)

    conn = pymysql.connect(
        host=config.get("host", "localhost"),
        port=int(config.get("port", 3306)),
        user=config.get("user", "root"),
        password=config.get("password", ""),
        connect_timeout=10
    )
    try:
        with conn.cursor() as cur:
            if drop_existing:
                log(f"[MariaDB] Dropping database `{db_name}` if exists...")
                cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`;")
            log(f"[MariaDB] Creating database `{db_name}` if not exists...")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
            conn.commit()
            log(f"[MariaDB] Database `{db_name}` is ready.")
    finally:
        conn.close()


def prepare_postgresql_database(config: Dict[str, Any], db_name: str, drop_existing: bool = False, log_fn: Optional[Callable[[str], None]] = None) -> None:
    def log(msg: str):
        if log_fn: log_fn(msg)

    conn_str = (
        f"host={config.get('host', 'localhost')} "
        f"port={config.get('port', 5432)} "
        f"user={config.get('user', 'postgres')} "
        f"password={config.get('password', '')} "
        f"dbname={config.get('maintenance_db', 'postgres')} "
        f"connect_timeout=10"
    )
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            if drop_existing:
                log(f"[PostgreSQL] Terminating existing connections to '{db_name}'...")
                cur.execute("""
                    SELECT pg_terminate_backend(pid) 
                    FROM pg_stat_activity 
                    WHERE datname = %s AND pid <> pg_backend_pid();
                """, (db_name,))
                log(f"[PostgreSQL] Dropping database '{db_name}' if exists...")
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}";')
            
            # Check existence
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if not cur.fetchone():
                log(f"[PostgreSQL] Creating database '{db_name}'...")
                cur.execute(f'CREATE DATABASE "{db_name}" ENCODING \'UTF8\';')
                log(f"[PostgreSQL] Database '{db_name}' created successfully.")
            else:
                log(f"[PostgreSQL] Database '{db_name}' already exists.")


def stream_and_execute_sql(conn, file_path: str, target_db: Optional[str] = None, log_fn: Optional[Callable[[str], None]] = None, batch_size_bytes: int = 2 * 1024 * 1024) -> None:
    """Streams and executes large SQL files in chunked statement batches with live progress."""
    def log(msg: str):
        if log_fn: log_fn(msg)

    total_bytes = os.path.getsize(file_path)
    total_mb = total_bytes / (1024 * 1024)
    processed_bytes = 0
    current_batch = []
    current_size = 0
    batch_count = 0
    line_idx = 0

    open_func = gzip.open if file_path.endswith(".gz") else open
    with conn.cursor() as cur:
        with open_func(file_path, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_idx += 1
                # Prevent mysqldump header from redirecting execution to a different database
                if target_db and line_idx <= 250:
                    stripped_lower = line.strip().lower()
                    if stripped_lower.startswith("create database") or stripped_lower.startswith("create schema"):
                        continue
                    if stripped_lower.startswith("use "):
                        clean_target = target_db.replace("`", "").replace("'", "")
                        line = f"USE `{clean_target}`;\n"

                line_len = len(line.encode("utf-8"))
                processed_bytes += line_len
                current_size += line_len
                current_batch.append(line)

                # Check for statement terminator at batch size threshold
                stripped = line.rstrip()
                if stripped.endswith(";") and current_size >= batch_size_bytes:
                    batch_sql = "".join(current_batch)
                    batch_count += 1
                    pct = (processed_bytes / total_bytes * 100) if total_bytes > 0 else 100
                    log(f"-> Executing batch #{batch_count} ({processed_bytes / (1024*1024):.1f} / {total_mb:.1f} MB — {pct:.1f}%)...")
                    cur.execute(batch_sql)
                    current_batch = []
                    current_size = 0

            # Execute any remaining statements
            if current_batch:
                batch_sql = "".join(current_batch).strip()
                if batch_sql:
                    batch_count += 1
                    log(f"-> Executing final batch #{batch_count} (100%)...")
                    cur.execute(batch_sql)

    log(f"Successfully executed {batch_count} batches ({total_mb:.2f} MB).")


def restore_mariadb_via_driver(config: Dict[str, Any], db_name: str, file_path: str, log_fn: Optional[Callable[[str], None]] = None) -> None:
    """Fallback: Execute SQL script directly via PyMySQL driver with batch streaming."""
    def log(msg: str):
        if log_fn: log_fn(msg)

    log(f"[MariaDB Driver Mode] Connecting to database '{db_name}'...")
    conn = pymysql.connect(
        host=config.get("host", "localhost"),
        port=int(config.get("port", 3306)),
        user=config.get("user", "root"),
        password=config.get("password", ""),
        database=db_name,
        client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS,
        charset='utf8mb4',
        autocommit=True
    )
    try:
        log("[MariaDB Driver Mode] Streaming and executing SQL script...")
        stream_and_execute_sql(conn, file_path, target_db=db_name, log_fn=log)
        log("[MariaDB Driver Mode] Restore completed successfully!")
    finally:
        conn.close()


def restore_postgresql_via_driver(config: Dict[str, Any], db_name: str, file_path: str, log_fn: Optional[Callable[[str], None]] = None) -> None:
    """Fallback: Execute SQL script directly via psycopg driver with batch streaming."""
    def log(msg: str):
        if log_fn: log_fn(msg)

    log(f"[PostgreSQL Driver Mode] Connecting to database '{db_name}'...")
    conn_str = (
        f"host={config.get('host', 'localhost')} "
        f"port={config.get('port', 5432)} "
        f"user={config.get('user', 'postgres')} "
        f"password={config.get('password', '')} "
        f"dbname={db_name}"
    )
    with psycopg.connect(conn_str, autocommit=True) as conn:
        log("[PostgreSQL Driver Mode] Streaming and executing SQL script...")
        stream_and_execute_sql(conn, file_path, target_db=db_name, log_fn=log)
        log("[PostgreSQL Driver Mode] Restore completed successfully!")


def restore_database(
    db_type: str,
    config: Dict[str, Any],
    target_db: str,
    file_path: str,
    create_db: bool = True,
    drop_existing: bool = False,
    log_fn: Optional[Callable[[str], None]] = None
) -> Tuple[bool, str]:
    """
    Main restore orchestrator.
    Returns (success: bool, final_logs: str)
    """
    logs_buffer = []

    def log(msg: str):
        logs_buffer.append(msg)
        if log_fn:
            log_fn(msg)

    start_time = time.time()
    log(f"=== Starting Database Restore Process ===")
    log(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Database Type: {db_type}")
    log(f"Target Database: {target_db}")
    log(f"File Path: {file_path}")
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    log(f"File Size: {file_size_mb:.2f} MB")

    try:
        # Step 1: Database creation / preparation
        if db_type == "PostgreSQL":
            if create_db or drop_existing:
                prepare_postgresql_database(config, target_db, drop_existing=drop_existing, log_fn=log)
        else: # MariaDB
            if create_db or drop_existing:
                prepare_mariadb_database(config, target_db, drop_existing=drop_existing, log_fn=log)

        # Step 2: Determine tool or driver execution
        host = config.get("host", "localhost")
        port = str(config.get("port", 5432 if db_type == "PostgreSQL" else 3306))
        user = config.get("user", "postgres" if db_type == "PostgreSQL" else "root")
        password = config.get("password", "")
        custom_cli = config.get("cli_path", "")

        is_custom_dump = file_path.endswith((".dump", ".tar", ".backup", ".dir"))

        if db_type == "PostgreSQL":
            tool_to_find = "pg_restore" if is_custom_dump else "psql"
            tool_bin = find_tool_executable("postgresql", tool_to_find, custom_cli)

            if tool_bin:
                log(f"[PostgreSQL] Found native CLI utility: {tool_bin}")
                env = os.environ.copy()
                if password:
                    env["PGPASSWORD"] = password

                if is_custom_dump:
                    cmd = [
                        tool_bin,
                        "-h", host,
                        "-p", port,
                        "-U", user,
                        "-d", target_db,
                        "--verbose",
                        "--no-owner",
                        file_path
                    ]
                else:
                    cmd = [
                        tool_bin,
                        "-h", host,
                        "-p", port,
                        "-U", user,
                        "-d", target_db,
                        "-f", file_path
                    ]

                log(f"[PostgreSQL] Executing command: {' '.join(cmd)}")
                proc = subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                for line in iter(proc.stdout.readline, ''):
                    if line:
                        log(f"> {line.strip()}")
                proc.stdout.close()
                return_code = proc.wait()

                # For pg_restore, exit code 0 is clean, 1 is warning (often harmless permissions/roles)
                if return_code not in (0, 1):
                    raise RuntimeError(f"pg_restore/psql failed with exit code {return_code}")
                log(f"[PostgreSQL] Native tool completed successfully (exit code {return_code}).")
            else:
                if is_custom_dump:
                    raise RuntimeError(
                        f"PostgreSQL custom archive format ({os.path.basename(file_path)}) requires pg_restore.exe! "
                        f"Please specify the PostgreSQL bin directory in Configuration."
                    )
                log("[PostgreSQL] Native psql not found in PATH or Config. Falling back to Python psycopg driver...")
                restore_postgresql_via_driver(config, target_db, file_path, log_fn=log)

        else: # MariaDB
            tool_bin = find_tool_executable("mariadb", "mariadb", custom_cli) or find_tool_executable("mariadb", "mysql", custom_cli)
            if tool_bin:
                log(f"[MariaDB] Found native CLI utility: {tool_bin}")
                cmd = [
                    tool_bin,
                    f"-h{host}",
                    f"-P{port}",
                    f"-u{user}"
                ]
                if password:
                    cmd.append(f"-p{password}")
                cmd.append(target_db)

                log(f"[MariaDB] Piping '{file_path}' into {os.path.basename(tool_bin)}...")
                open_func = gzip.open if file_path.endswith(".gz") else open
                with open_func(file_path, "rb") as in_file:
                    proc = subprocess.Popen(
                        cmd,
                        stdin=in_file,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    stdout, stderr = proc.communicate()

                if stdout:
                    for line in stdout.splitlines():
                        log(f"> {line}")
                if stderr:
                    for line in stderr.splitlines():
                        log(f"[stderr] {line}")

                if proc.returncode != 0:
                    raise RuntimeError(f"mariadb/mysql client failed with exit code {proc.returncode}: {stderr}")
                log("[MariaDB] Native restore completed successfully!")
            else:
                log("[MariaDB] Native mariadb/mysql CLI not found in PATH or Config. Falling back to PyMySQL driver...")
                restore_mariadb_via_driver(config, target_db, file_path, log_fn=log)

        elapsed = time.time() - start_time
        log(f"=== Restore Completed Successfully in {elapsed:.2f} seconds ===")
        return True, "\n".join(logs_buffer)

    except Exception as e:
        elapsed = time.time() - start_time
        log(f"\n[ERROR] Restore failed: {str(e)}")
        log(f"=== Restore Terminated with Error after {elapsed:.2f} seconds ===")
        return False, "\n".join(logs_buffer)
