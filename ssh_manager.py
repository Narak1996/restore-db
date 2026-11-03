import os
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Callable
import paramiko

def get_ssh_client(ssh_cfg: Dict[str, Any]) -> paramiko.SSHClient:
    """Creates an authenticated Paramiko SSH client from config."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    host = ssh_cfg.get("host", "127.0.0.1")
    port = int(ssh_cfg.get("port", 22))
    user = ssh_cfg.get("user", "root")
    auth_type = ssh_cfg.get("auth_type", "password")
    password = ssh_cfg.get("password", "") or None
    key_path = ssh_cfg.get("key_path", "").strip() or None

    kwargs = {
        "hostname": host,
        "port": port,
        "username": user,
        "timeout": 10,
        "banner_timeout": 15
    }

    if auth_type == "key" and key_path and os.path.exists(key_path):
        kwargs["key_filename"] = key_path
    elif password:
        kwargs["password"] = password

    client.connect(**kwargs)
    return client


def test_ssh_connection(ssh_cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """Tests SSH connectivity and retrieves remote host info."""
    client = None
    try:
        client = get_ssh_client(ssh_cfg)
        stdin, stdout, stderr = client.exec_command("uname -srm; uptime -p 2>/dev/null || uptime", timeout=10)
        output = stdout.read().decode("utf-8", errors="replace").strip()
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        sys_info = " | ".join(lines) if lines else "SSH Connected"
        return True, f"SSH Connected: {sys_info}"
    except Exception as e:
        return False, f"SSH Connection Failed: {str(e)}"
    finally:
        if client:
            client.close()


def list_remote_backup_files(ssh_cfg: Dict[str, Any], remote_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lists database dump files located in a remote server directory via SFTP."""
    target_dir = remote_dir or ssh_cfg.get("remote_backup_dir", "/var/backups")
    if not target_dir:
        return []

    valid_exts = {".sql", ".dump", ".tar", ".backup", ".gz"}
    results = []
    client = None

    try:
        client = get_ssh_client(ssh_cfg)
        sftp = client.open_sftp()
        try:
            entries = sftp.listdir_attr(target_dir)
        except IOError as e:
            return []

        for entry in entries:
            fname = entry.filename
            lower_name = fname.lower()
            ext = os.path.splitext(lower_name)[1]
            if ext in valid_exts or lower_name.endswith(".sql.gz"):
                size_mb = entry.st_size / (1024 * 1024)
                mtime_str = datetime.fromtimestamp(entry.st_mtime).strftime("%Y-%m-%d %H:%M")
                full_remote_path = f"{target_dir.rstrip('/')}/{fname}"
                results.append({
                    "name": fname,
                    "path": full_remote_path,
                    "size_mb": round(size_mb, 2),
                    "raw_size": entry.st_size,
                    "modified": mtime_str
                })

        sftp.close()
        # Sort newest modified first
        results.sort(key=lambda x: x["modified"], reverse=True)
        return results
    except Exception:
        return []
    finally:
        if client:
            client.close()


def execute_remote_command_stream(
    client: paramiko.SSHClient,
    command: str,
    log_fn: Optional[Callable[[str], None]] = None
) -> Tuple[int, str]:
    """Executes a command on the remote server over SSH, streaming stdout/stderr in real-time."""
    logs_buffer = []

    def log(msg: str):
        logs_buffer.append(msg)
        if log_fn:
            log_fn(msg)

    transport = client.get_transport()
    session = transport.open_session()
    session.set_combine_stderr(True)
    session.exec_command(command)

    buf = ""
    while True:
        if session.recv_ready():
            chunk = session.recv(2048).decode("utf-8", errors="replace")
            buf += chunk
            while "\r" in buf or "\n" in buf:
                idx_r = buf.find("\r")
                idx_n = buf.find("\n")
                if idx_r != -1 and (idx_n == -1 or idx_r < idx_n):
                    line, buf = buf[:idx_r], buf[idx_r + 1:]
                else:
                    line, buf = buf[:idx_n], buf[idx_n + 1:]
                line = line.strip()
                if line:
                    if "[" in line and "]" in line and ("B/s" in line or "%" in line):
                        log(f"> [Stream Progress] {line}")
                    else:
                        log(f"> {line}")
        elif session.exit_status_ready():
            if buf.strip():
                log(f"> {buf.strip()}")
            break
        time.sleep(0.04)

    exit_code = session.recv_exit_status()
    session.close()
    return exit_code, "\n".join(logs_buffer)


def remote_create_database(
    ssh_cfg: Dict[str, Any],
    db_type: str,
    db_cfg: Dict[str, Any],
    db_name: str,
    drop_existing: bool = False,
    log_fn: Optional[Callable[[str], None]] = None
) -> None:
    """Prepares / Creates the database on the remote server via SSH."""
    def log(msg: str):
        if log_fn: log_fn(msg)

    client = get_ssh_client(ssh_cfg)
    try:
        host = db_cfg.get("host", "127.0.0.1")
        port = db_cfg.get("port", 5432 if db_type == "PostgreSQL" else 3306)
        user = db_cfg.get("user", "postgres" if db_type == "PostgreSQL" else "root")
        password = db_cfg.get("password", "")
        # Escape single quotes in password
        escaped_pw = password.replace("'", "'\\''")

        if db_type == "PostgreSQL":
            maint_db = db_cfg.get("maintenance_db", "postgres")
            if drop_existing:
                log(f"[Remote SSH] Terminating connections and dropping database '{db_name}'...")
                term_cmd = (
                    f"PGPASSWORD='{escaped_pw}' psql -h '{host}' -p {port} -U '{user}' -d '{maint_db}' "
                    f"-c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}' AND pid <> pg_backend_pid();\" "
                    f"-c 'DROP DATABASE IF EXISTS \"{db_name}\";'"
                )
                code, out = execute_remote_command_stream(client, term_cmd, log_fn=log)
                if code != 0:
                    log(f"[Remote SSH Warning] Drop command exited with code {code}")

            log(f"[Remote SSH] Creating PostgreSQL database '{db_name}' if not exists...")
            check_and_create = (
                f"PGPASSWORD='{escaped_pw}' psql -h '{host}' -p {port} -U '{user}' -d '{maint_db}' "
                f"-tc \"SELECT 1 FROM pg_database WHERE datname = '{db_name}'\" | grep -q 1 || "
                f"PGPASSWORD='{escaped_pw}' psql -h '{host}' -p {port} -U '{user}' -d '{maint_db}' -c \"CREATE DATABASE \\\"{db_name}\\\" ENCODING 'UTF8';\""
            )
            code, out = execute_remote_command_stream(client, check_and_create, log_fn=log)
            if code != 0:
                raise RuntimeError(f"Failed to create PostgreSQL database on remote server (exit code {code})")
            log(f"[Remote SSH] Database '{db_name}' is ready.")

        else: # MariaDB
            clean_db = db_name.replace('`', '').strip()
            if drop_existing:
                log(f"[Remote SSH] Dropping MariaDB database `{clean_db}` if exists...")
                drop_cmd = (
                    f"CLI=$(command -v mariadb 2>/dev/null || command -v mysql 2>/dev/null || echo mysql); "
                    f"MYSQL_PWD='{escaped_pw}' \"$CLI\" -h '{host}' -P {port} -u '{user}' "
                    f"-e 'DROP DATABASE IF EXISTS `{clean_db}`;'"
                )
                execute_remote_command_stream(client, drop_cmd, log_fn=log)

            log(f"[Remote SSH] Creating MariaDB database `{clean_db}` if not exists...")
            create_cmd = (
                f"CLI=$(command -v mariadb 2>/dev/null || command -v mysql 2>/dev/null || echo mysql); "
                f"MYSQL_PWD='{escaped_pw}' \"$CLI\" -h '{host}' -P {port} -u '{user}' "
                f"-e 'CREATE DATABASE IF NOT EXISTS `{clean_db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;'"
            )
            code, out = execute_remote_command_stream(client, create_cmd, log_fn=log)
            if code != 0:
                raise RuntimeError(f"Failed to create MariaDB database on remote server (exit code {code})")
            log(f"[Remote SSH] Database `{clean_db}` is ready.")
    finally:
        client.close()


def list_remote_postgresql_databases(ssh_cfg: Dict[str, Any], db_cfg: Dict[str, Any]) -> List[str]:
    """Queries existing PostgreSQL databases on the remote server over SSH."""
    client = None
    try:
        client = get_ssh_client(ssh_cfg)
        host = db_cfg.get("host", "127.0.0.1")
        port = db_cfg.get("port", 5432)
        user = db_cfg.get("user", "postgres")
        password = db_cfg.get("password", "")
        escaped_pw = password.replace("'", "'\\''")
        maint_db = db_cfg.get("maintenance_db", "postgres")

        cmd = (
            f"PGPASSWORD='{escaped_pw}' psql -h '{host}' -p {port} -U '{user}' -d '{maint_db}' "
            f"-Atc \"SELECT datname FROM pg_database WHERE datistemplate = false;\""
        )
        stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
        output = stdout.read().decode("utf-8", errors="replace").strip()
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return lines
    except Exception:
        return []
    finally:
        if client:
            client.close()


def list_remote_mariadb_databases(ssh_cfg: Dict[str, Any], db_cfg: Dict[str, Any]) -> List[str]:
    """Queries existing MariaDB databases on the remote server over SSH."""
    client = None
    try:
        client = get_ssh_client(ssh_cfg)
        host = db_cfg.get("host", "127.0.0.1")
        port = db_cfg.get("port", 3306)
        user = db_cfg.get("user", "root")
        password = db_cfg.get("password", "")
        escaped_pw = password.replace("'", "'\\''")

        cmd = (
            f"CLI=$(command -v mariadb 2>/dev/null || command -v mysql 2>/dev/null || echo mysql); "
            f"MYSQL_PWD='{escaped_pw}' \"$CLI\" -h '{host}' -P {port} -u '{user}' -s -N -e 'SHOW DATABASES;'"
        )
        stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
        output = stdout.read().decode("utf-8", errors="replace").strip()
        system_dbs = {"information_schema", "mysql", "performance_schema", "sys"}
        lines = [line.strip() for line in output.splitlines() if line.strip() and line.strip().lower() not in system_dbs]
        return lines
    except Exception:
        return []
    finally:
        if client:
            client.close()

def remote_restore_database(
    ssh_cfg: Dict[str, Any],
    db_type: str,
    db_cfg: Dict[str, Any],
    db_name: str,
    remote_file_path: str,
    create_db: bool = True,
    drop_existing: bool = False,
    log_fn: Optional[Callable[[str], None]] = None
) -> Tuple[bool, str]:
    """Executes the full database creation and restore pipeline on the remote server over SSH."""
    logs_buffer = []

    def log(msg: str):
        logs_buffer.append(msg)
        if log_fn:
            log_fn(msg)

    start_time = time.time()
    log("=== Starting Remote Database Restore over SSH ===")
    log(f"SSH Host: {ssh_cfg.get('host')} : {ssh_cfg.get('port', 22)} (User: {ssh_cfg.get('user')})")
    log(f"Engine: {db_type}")
    log(f"Target Database: {db_name}")
    log(f"Remote File: {remote_file_path}")

    client = None
    try:
        # Step 1: Database Preparation / Creation on remote server
        if create_db or drop_existing:
            remote_create_database(
                ssh_cfg=ssh_cfg,
                db_type=db_type,
                db_cfg=db_cfg,
                db_name=db_name,
                drop_existing=drop_existing,
                log_fn=log
            )

        client = get_ssh_client(ssh_cfg)
        host = db_cfg.get("host", "127.0.0.1")
        port = db_cfg.get("port", 5432 if db_type == "PostgreSQL" else 3306)
        user = db_cfg.get("user", "postgres" if db_type == "PostgreSQL" else "root")
        password = db_cfg.get("password", "")
        escaped_pw = password.replace("'", "'\\''")

        is_custom_dump = remote_file_path.endswith((".dump", ".tar", ".backup", ".dir"))
        is_gzip = remote_file_path.endswith(".gz")

        log(f"[Remote SSH] Executing streaming restore on remote server...")

        if db_type == "PostgreSQL":
            clean_db = db_name.replace('"', '').strip()
            if is_custom_dump:
                restore_cmd = (
                    f"PGPASSWORD='{escaped_pw}' pg_restore -h '{host}' -p {port} -U '{user}' "
                    f"-d '{clean_db}' --verbose --no-owner '{remote_file_path}'"
                )
            elif is_gzip:
                restore_cmd = (
                    f"STREAM=$(command -v pv >/dev/null 2>&1 && echo 'pv -f -i 1' || echo 'cat'); "
                    f"$STREAM '{remote_file_path}' | zcat | PGPASSWORD='{escaped_pw}' psql -h '{host}' -p {port} "
                    f"-U '{user}' -d '{clean_db}'"
                )
            else:
                restore_cmd = (
                    f"STREAM=$(command -v pv >/dev/null 2>&1 && echo 'pv -f -i 1' || echo 'cat'); "
                    f"$STREAM '{remote_file_path}' | PGPASSWORD='{escaped_pw}' psql -h '{host}' -p {port} "
                    f"-U '{user}' -d '{clean_db}'"
                )

            log(f"[Remote SSH] Command: {restore_cmd.replace(escaped_pw, '******')}")
            code, out = execute_remote_command_stream(client, restore_cmd, log_fn=log)
            # pg_restore exit code 0 is success, 1 is warning (permissible roles/privileges)
            if code not in (0, 1):
                raise RuntimeError(f"Remote PostgreSQL restore failed with exit code {code}")

        else: # MariaDB
            clean_db = db_name.replace("`", "").replace("'", "").strip()
            # Rewrite CREATE DATABASE and USE statements in the first 250 lines so that
            # tables are always restored into the user's target database (clean_db),
            # preventing mysqldump headers from redirecting data into a different DB.
            sed_filter = (
                f"sed -e '1,250s/^[[:blank:]]*CREATE[[:blank:]]\\+DATABASE[[:blank:]]\\+.*$/-- [skipped CREATE DATABASE]/I' "
                f"-e '1,250s/^[[:blank:]]*USE[[:blank:]]\\+.*$/USE `{clean_db}`;/I'"
            )
            if is_gzip:
                restore_cmd = (
                    f"CLI=$(command -v mariadb 2>/dev/null || command -v mysql 2>/dev/null || echo mysql); "
                    f"STREAM=$(command -v pv >/dev/null 2>&1 && echo 'pv -f -i 1' || echo 'cat'); "
                    f"$STREAM '{remote_file_path}' | zcat | {sed_filter} | MYSQL_PWD='{escaped_pw}' \"$CLI\" -h '{host}' -P {port} -u '{user}' '{clean_db}'"
                )
            else:
                restore_cmd = (
                    f"CLI=$(command -v mariadb 2>/dev/null || command -v mysql 2>/dev/null || echo mysql); "
                    f"STREAM=$(command -v pv >/dev/null 2>&1 && echo 'pv -f -i 1' || echo 'cat'); "
                    f"$STREAM '{remote_file_path}' | {sed_filter} | MYSQL_PWD='{escaped_pw}' \"$CLI\" -h '{host}' -P {port} -u '{user}' '{clean_db}'"
                )

            log(f"[Remote SSH] Command: {restore_cmd.replace(escaped_pw, '******') if escaped_pw else restore_cmd}")
            code, out = execute_remote_command_stream(client, restore_cmd, log_fn=log)
            if code != 0:
                raise RuntimeError(f"Remote MariaDB restore failed with exit code {code}")

        # Post-restore verification stats
        log(f"[Remote SSH] Restore stream finished. Verifying tables in `{clean_db}`...")
        try:
            from verify_restore import get_database_proof
            proof = get_database_proof(db_type, db_cfg, clean_db)
            if proof.get("success"):
                log(f"[Verification Proof] Restored: {proof['total_tables']} tables, {proof['total_rows']:,} rows ({proof['size_mb']} MB).")
        except Exception:
            pass

        elapsed = time.time() - start_time
        log(f"=== Remote Restore Completed Successfully in {elapsed:.2f} seconds ===")
        return True, "\n".join(logs_buffer)

    except Exception as e:
        elapsed = time.time() - start_time
        log(f"\n[ERROR] Remote Restore failed: {str(e)}")
        log(f"=== Remote Restore Terminated with Error after {elapsed:.2f} seconds ===")
        return False, "\n".join(logs_buffer)
    finally:
        if client:
            client.close()
